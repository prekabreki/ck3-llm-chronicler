"""Issue #20: ``chronicler init-prose`` — scaffold a chronicle directory.

The setup step this replaces was "clone the maintainer's private prose
repo", which no public user can do. The mechanics live in
:mod:`chronicler.narrative.prose_scaffold` so the Settings "Initialize"
button (#23) shares them; this module is only the command surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from chronicler.cli._app import app


@app.command("init-prose")
def cmd_init_prose(
    path: Annotated[
        Path | None,
        typer.Argument(
            help="Where to create the chronicle directory. "
            "Defaults to <chronicler data dir>/prose.",
        ),
    ] = None,
    git: Annotated[
        bool,
        typer.Option(
            "--git/--no-git",
            help="Initialise a git repository so your chronicle accumulates as history.",
        ),
    ] = True,
) -> None:
    """Create the chronicle directory the narrative backend writes into.

    Copies the bundled craft rules and voice files, git-inits the result,
    and records the path in settings. Safe to re-run — an existing
    chronicle directory is left exactly as it is.
    """
    from chronicler.narrative.prose_scaffold import default_prose_path, scaffold_prose_dir

    target = path if path is not None else default_prose_path()
    try:
        result = scaffold_prose_dir(target, git_init=git)
    except (ValueError, RuntimeError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    for note in result.notes:
        typer.echo(f"  {note}")

    if result.already_initialised:
        typer.secho(
            f"\nChronicle directory already set up at {result.path}",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(
            f"\nChronicle directory ready at {result.path}",
            fg=typer.colors.GREEN,
        )

    typer.echo(
        "\nNext steps:\n"
        f"  1. Read {result.path / 'README.md'} — it explains the layout and\n"
        "     which files control how your chronicle reads.\n"
        f"  2. Edit {result.path / 'CLAUDE.md'} to shape the chronicler's\n"
        "     register and craft rules; voice/ holds the per-kind rules.\n"
        "  3. Run `chronicler doctor` to confirm the narrative backend and\n"
        "     this directory are both configured.\n"
        "  4. The _smoke/synthetic-jarl-v1.md pair is a worked example —\n"
        "     read the briefing and biography side by side, then delete them."
    )
