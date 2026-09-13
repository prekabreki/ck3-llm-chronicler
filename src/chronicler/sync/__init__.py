"""Cross-machine sync of archived campaigns (ck3_chronicler-txuo).

Keeps sealed campaigns following the user's main repo so dynasties +
biographies + family trees are readable from any machine that pulls
the repo. Active campaigns are explicitly NOT synced — save-tail
stays on the primary machine.

Deletions propagate too (ck3_chronicler-wvrm): a Library hard-delete
writes an ``<archive dir>/<id>.deleted`` tombstone; other machines prune
their matching registry row + local DB on the next startup, instead of
resurrecting the campaign.

Issue #24: the archive dir is configurable and defaults to
``<data dir>/archived`` rather than living inside this checkout. When it
happens to sit in a git repo, chronicler commits and pushes there; when it
does not, it writes the files and the user's own sync vehicle carries them.
See ``docs/archived-campaigns.md``.
"""

from __future__ import annotations

from chronicler.sync.archive_export import (
    ExportResult,
    archive_sync_enabled,
    archived_campaigns_dir,
    export_sealed_campaign,
    legacy_in_tree_archive_dir,
    resolve_archive_git_root,
    tombstone_campaign,
    warn_if_legacy_archive_unmigrated,
)
from chronicler.sync.bootstrap import (
    backfill_archived_campaigns,
    bootstrap_archived_snapshots,
)

__all__ = [
    "ExportResult",
    "archive_sync_enabled",
    "archived_campaigns_dir",
    "backfill_archived_campaigns",
    "bootstrap_archived_snapshots",
    "export_sealed_campaign",
    "legacy_in_tree_archive_dir",
    "resolve_archive_git_root",
    "tombstone_campaign",
    "warn_if_legacy_archive_unmigrated",
]
