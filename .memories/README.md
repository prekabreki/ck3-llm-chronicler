# Memories

Durable, grep-friendly project knowledge, one fact per file, committed and shared.

Each memory is `.memories/<kebab-key>.md` opening with YAML frontmatter carrying a one-line
`description:` and an optional `type:`. This index is generated: run `python tools/memory-index.py`
or let the pre-commit hook stage it.

Save a memory when a fact cost real effort to learn and is not obvious from the code or the
git history.
