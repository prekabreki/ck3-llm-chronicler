"""ck3_chronicler-8jz: smoke-test entrypoints surfaced via the CLI.

Each module here exposes a ``run_*`` callable the typer command thin-wraps,
so the same code path drives:

- ``chronicler smoke-yearly`` (the CLI alias for the patch-playbook flow)
- ``scripts/smoke_yearly_diff.py`` (kept as a back-compat entrypoint)

See :mod:`chronicler.smoke.smoke_yearly` and ``docs/patch-playbook.md``
for the runbook.
"""

from __future__ import annotations
