"""Issue #20: the shipped `prose-template/` and what must never be in it.

The template is the instruction layer a public user scaffolds with
`chronicler init-prose`. Its source material is the maintainer's private
prose repo, which also holds 368 biographies across five real campaigns —
so this module is the automated half of the "no personal content ever
ships" guarantee (readiness P0-4). The other half is reading the diff.

These tests are deliberately paranoid and deliberately cheap: they run on
every commit, and the failure they exist to catch is unrecoverable once
it reaches a public git history.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "prose-template"

# The five real campaign directories live under these UUIDs in the source
# repo. Matching the SHAPE rather than the values keeps the test honest
# for campaigns that don't exist yet — and keeps the real UUIDs out of
# this file, which ships.
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)

# Private-workflow and personal markers that must not survive the fold-in.
_FORBIDDEN_SUBSTRINGS = (
    ".beads",
    "bd remember",
    "bd prime",
    "BEADS INTEGRATION",
    "_unscoped",
    "ck3_chronicler_prose",
    "C:\\git",
    "C:/git",
    "/home/prekabreki",
    "prekabreki",
)


def _template_files() -> list[Path]:
    return sorted(p for p in TEMPLATE_DIR.rglob("*") if p.is_file())


def test_template_dir_exists() -> None:
    assert TEMPLATE_DIR.is_dir(), f"{TEMPLATE_DIR} is missing"


def test_template_manifest_is_exactly_the_approved_set() -> None:
    """Pin the file list (spec §4). A new file appearing here is either a
    deliberate manifest change or a leak; both must be reviewed, so the
    test fails on either."""
    expected = {
        "CLAUDE.md",
        "README.md",
        "voice/biography.md",
        "voice/biography-woven.md",
        "voice/chronicle-export.md",
        "biographies/.gitkeep",
        "biographies/_smoke/synthetic-jarl-v1.md",
        "briefings/.gitkeep",
        "briefings/_smoke/synthetic-jarl-v1.md",
    }
    actual = {p.relative_to(TEMPLATE_DIR).as_posix() for p in _template_files()}
    assert actual == expected


def test_no_campaign_uuid_directories() -> None:
    """The five real campaign dirs (368 biographies) must not appear in
    any form — not as directories, not in example paths inside the docs."""
    for path in TEMPLATE_DIR.rglob("*"):
        rel = path.relative_to(TEMPLATE_DIR).as_posix()
        assert not _UUID_RE.search(rel), f"campaign-uuid-shaped path: {rel}"


def test_no_campaign_uuids_in_file_contents() -> None:
    """The source CLAUDE.md's File-workflow section used a real campaign
    UUID in its worked example. A UUID in prose is not a directory of
    biographies, but it is still the maintainer's campaign id shipping to
    strangers, and it tells a public user nothing."""
    for path in _template_files():
        if path.name == ".gitkeep":
            continue
        text = path.read_text(encoding="utf-8")
        found = _UUID_RE.findall(text)
        assert not found, f"{path.relative_to(TEMPLATE_DIR)} contains UUID(s): {found}"


@pytest.mark.parametrize("needle", _FORBIDDEN_SUBSTRINGS)
def test_no_private_workflow_markers(needle: str) -> None:
    for path in _template_files():
        if path.name == ".gitkeep":
            continue
        text = path.read_text(encoding="utf-8")
        assert needle.lower() not in text.lower(), (
            f"{path.relative_to(TEMPLATE_DIR)} still mentions {needle!r}"
        )


def test_only_the_three_live_voice_files_ship() -> None:
    """memory-compaction.md and memory-consolidation.md describe the
    memory pipeline demolished in v0.12. Shipping them would document a
    feature that does not exist."""
    voices = {p.name for p in (TEMPLATE_DIR / "voice").iterdir() if p.is_file()}
    assert voices == {"biography.md", "biography-woven.md", "chronicle-export.md"}


def test_every_prompt_kind_has_a_voice_file_in_the_template() -> None:
    """The shared system-prompt assembly (#19) fails loud when a mapped
    voice file is missing, so the template must satisfy every registered
    kind or a fresh install cannot generate at all."""
    from chronicler.narrative.prose_io import _VOICE_FILES_BY_KIND

    for kind, rel in _VOICE_FILES_BY_KIND.items():
        assert (TEMPLATE_DIR / rel).is_file(), f"{kind} -> {rel} missing from the template"


def test_the_smoke_example_is_synthetic() -> None:
    """The example pair is the one biography that ships. It must be the
    fully synthetic jarl (character 99001), not a real character."""
    brief = (TEMPLATE_DIR / "briefings/_smoke/synthetic-jarl-v1.md").read_text(encoding="utf-8")
    bio = (TEMPLATE_DIR / "biographies/_smoke/synthetic-jarl-v1.md").read_text(encoding="utf-8")
    assert "99001" in brief
    assert brief.strip()
    assert bio.strip()


def test_claude_md_keeps_the_craft_rules() -> None:
    """The fold-in drops the private-workflow tail, not the register. If
    these anchors go missing the template still scaffolds and still
    generates — just without the rules that make the prose good, which is
    exactly the silent failure #19 was about."""
    text = (TEMPLATE_DIR / "CLAUDE.md").read_text(encoding="utf-8")
    for anchor in (
        "Anti-fabrication",
        "1p50",
        "Trait changes are bookkeeping",
        "Naming discipline",
        "Forbidden registers",
        "Length and voice",
    ):
        assert anchor in text, f"CLAUDE.md lost the {anchor!r} section"


def test_the_smoke_briefing_header_matches_what_the_renderer_emits() -> None:
    """The shipped example must look like a real briefing.

    Its header was stale by two eras: it still told the model to read
    `../../CLAUDE.md` itself, which stopped being true when the agentic
    wrapper was removed (27ov.14) and the register moved into the system
    prompt (#19). A worked example that contradicts the live format
    teaches a new user the wrong thing about their own files.
    """
    from chronicler.narrative.prose_io import render_briefing_markdown
    from chronicler.narrative.provider import NarrativeRequest

    rendered = render_briefing_markdown(
        NarrativeRequest(
            kind="biography_woven",
            prompt_version="biography_v5",
            system_prompt="(register)",
            user_prompt="(body)",
            metadata={"campaign_uuid": "_smoke", "character_id": "99001"},
        )
    )
    header, _, _ = rendered.partition("---")
    shipped = (TEMPLATE_DIR / "briefings/_smoke/synthetic-jarl-v1.md").read_text(encoding="utf-8")
    for line in header.strip().splitlines():
        if line.strip():
            assert line.strip() in shipped, f"example briefing is missing header line: {line!r}"
