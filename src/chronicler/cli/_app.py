"""The root Typer ``app`` object, isolated in its own module.

The CLI used to be a single 1061-line ``main.py``.
Splitting the commands into ``campaigns`` / ``ingest`` / ``inspect`` modules
needs each of them to attach ``@app.command(...)`` to the SAME root app,
but if that app lived in ``main.py`` (which must import the command modules
so their decorators run), the command modules importing ``main`` would form
a circular import. Housing ``app`` here, importing nothing from the command
modules, breaks that cycle: command modules import ``_app``; ``main`` imports
both ``_app`` and the command modules and does the final assembly.
"""

from __future__ import annotations

import typer

# The help blurb shown at ``chronicler --help``.
_HELP = """Turn a Crusader Kings III campaign into a written chronicle.

Point it at a campaign, let it watch your autosaves, and the characters you
track get a biography when they die. It all lands as markdown in a git repo
you own.

Getting started:

  init-prose                  scaffold the chronicle directory
  campaign create <name>      register a campaign
  import-save <path>          backfill from an existing save
  auto-track                  follow the player and their family
  dev                         watch saves and serve the web app

Reading and serving:

  serve                       web app only
  tracked                     who is being followed
  track / untrack <id>        change that

Inspecting and repairing:

  doctor                      check the install before blaming the code
  dump-character <id>         what the database holds on someone
  dump-biography <id>         the prose as written
  regenerate-biography <id>   write it again
  heraldry                    coat-of-arms extraction

Most verbs take --campaign <name>. Run any of them with --help for the rest.
"""

app = typer.Typer(no_args_is_help=True, add_completion=False, help=_HELP)
