"""Probe Claude Code per-call overhead with vs without --bare.

ck3_chronicler-yprl (Phase 6 of the LLM pipeline audit). Runs the same
minimal prompt twice — once with the chronicler's existing argv, once
with ``--bare`` added — and prints the envelope's usage breakdown so we
can see how much per-call overhead comes from Claude Code's plugin /
skill / hook / CLAUDE.md auto-load surface.

Throwaway script. The point is one number: the input_tokens delta
between the two runs. If it's > 30k tokens per call, file a followup
to adopt --bare (which then requires embedding CLAUDE.md content into
the user prompt explicitly, since --bare disables auto-discovery).

Usage:
    uv run python scripts/probe_overhead.py [--model claude-sonnet-4-6]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from chronicler.config import resolve_prose_repo_path
from chronicler.narrative.claude_code import _build_subprocess_env, _parse_json_envelope


async def run_one(*, argv: list[str], cwd: Path) -> dict:
    """Invoke ``claude --print`` once and return the parsed envelope.

    Uses ``_build_subprocess_env()`` to match the chronicler's actual
    invocation shape: strips ANTHROPIC_API_KEY + other API-mode triggers
    so the CLI uses the Claude Code subscription path. Without this the
    probe inherits the parent shell's API key and bills against API
    credits — which is exactly the bug the chronicler's env-strip
    prevents in production.
    """
    print(f"  invoking: {' '.join(argv[:8])} ...")
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=_build_subprocess_env(),
    )
    stdout, stderr = await proc.communicate()
    elapsed = time.monotonic() - start
    print(f"  exit={proc.returncode}, elapsed={elapsed:.1f}s")
    if proc.returncode != 0:
        print(f"  STDERR: {stderr.decode('utf-8', errors='replace')[:400]}")
    envelope = _parse_json_envelope(stdout)
    if envelope is None:
        print(f"  (no JSON envelope; raw stdout first 400 chars: {stdout[:400]!r})")
        return {}
    return envelope


def fmt_usage(envelope: dict) -> str:
    usage = envelope.get("usage") or {}
    in_m = usage.get("input_tokens") or 0
    cc = usage.get("cache_creation_input_tokens") or 0
    cr = usage.get("cache_read_input_tokens") or 0
    out_t = usage.get("output_tokens") or 0
    total_in = in_m + cc + cr
    cost = envelope.get("total_cost_usd")
    mu = envelope.get("modelUsage") or {}
    mu_keys = ",".join(sorted(mu.keys())) if isinstance(mu, dict) else "?"
    return (
        f"input(marginal/cache_create/cache_read)={in_m:,}/{cc:,}/{cr:,} = {total_in:,} total"
        f"; output={out_t:,}; cost_usd={cost}; modelUsage={mu_keys}"
    )


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--prompt", default="Respond with just the word ok. Do nothing else.")
    args = parser.parse_args(argv)

    prose_repo = resolve_prose_repo_path().value
    if prose_repo is None:
        print("prose repo not found via resolve_prose_repo_path", file=sys.stderr)
        return 2
    prose_repo = Path(prose_repo)
    print(f"prose repo: {prose_repo}")
    print(f"model:      {args.model}")
    print(f"prompt:     {args.prompt!r}")
    print()

    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    common_argv = [
        claude_bin,
        "--print",
        "--model",
        args.model,
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "-p",
        args.prompt,
    ]

    print("RUN 1 — chronicler-shape argv (no --bare):")
    env1 = await run_one(argv=common_argv, cwd=prose_repo)
    print(f"  {fmt_usage(env1)}")
    print()

    bare_argv = [common_argv[0], "--bare", *common_argv[1:]]
    print("RUN 2 — with --bare:")
    env2 = await run_one(argv=bare_argv, cwd=prose_repo)
    print(f"  {fmt_usage(env2)}")
    print()

    u1 = env1.get("usage") or {}
    u2 = env2.get("usage") or {}
    in1 = (
        (u1.get("input_tokens") or 0)
        + (u1.get("cache_creation_input_tokens") or 0)
        + (u1.get("cache_read_input_tokens") or 0)
    )
    in2 = (
        (u2.get("input_tokens") or 0)
        + (u2.get("cache_creation_input_tokens") or 0)
        + (u2.get("cache_read_input_tokens") or 0)
    )

    if env2.get("is_error") or env2.get("api_error_status"):
        # ``--bare`` strictly accepts ANTHROPIC_API_KEY or apiKeyHelper —
        # subscription/OAuth auth is never read. The chronicler strips
        # ANTHROPIC_API_KEY (see claude_code.py:468-484) precisely so the
        # CLI uses Claude Max/Pro inference rather than billing the API.
        # So --bare and the chronicler's auth path are structurally
        # incompatible: adopting --bare requires reverting to API billing.
        print("RECOMMENDATION: --bare is NOT a viable adoption path for the")
        print("chronicler. The CLI's --bare mode only accepts ANTHROPIC_API_KEY")
        print("or apiKeyHelper auth, never the subscription/OAuth path the")
        print("chronicler deliberately uses. Adopting --bare would re-introduce")
        print("the API-billing regression that env-stripping was put in place")
        print("to prevent (claude_code.py:468-484 ck3_chronicler-e95t).")
        print()
        print(f"Run 1 (subscription path) shows {in1:,} input tokens as the")
        print("per-call floor — this is the irreducible Claude Code overhead")
        print("under the chronicler's intended auth model. Cache_creation +")
        print("cache_read make up the bulk; cache reads are billed at ~10%")
        print("the marginal-input rate so the effective cost is much lower")
        print("than the raw count suggests.")
        return 0

    if in1 > 0 and in2 >= 0:
        delta = in1 - in2
        pct = (delta / in1) * 100 if in1 else 0
        print(f"DELTA: --bare saves {delta:,} input tokens per call ({pct:.1f}% reduction)")
        print()
        if delta > 30_000:
            print("RECOMMENDATION: --bare adoption likely pays for itself if and")
            print("only if API billing is acceptable. The chronicler currently")
            print("avoids API billing on purpose; verify before adopting.")
        else:
            print("RECOMMENDATION: --bare overhead savings are modest. The bulk")
            print("of the per-call input is the prompt itself, not Claude Code's")
            print("auto-loaded context. Investigate the prompt assembly instead.")

    # Also dump the full envelope JSONs for offline inspection
    out_dir = Path("logs")
    out_dir.mkdir(exist_ok=True)
    (out_dir / "probe_overhead_nobare.json").write_text(
        json.dumps(env1, indent=2), encoding="utf-8"
    )
    (out_dir / "probe_overhead_bare.json").write_text(json.dumps(env2, indent=2), encoding="utf-8")
    print()
    print(f"Full envelopes saved to {out_dir / 'probe_overhead_*.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
