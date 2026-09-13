"""Registry DB — tracks campaigns, tracked-character lifecycle, and
suppressed event kinds.

One small SQLite file at ``~/Documents/chronicler/registry.db`` (override
with ``CHRONICLER_DATA_DIR``). Three tables, no Alembic; tables are
created lazily on first connection and any column drift is closed via
``ALTER TABLE ADD COLUMN`` — see :mod:`chronicler.db.registry._core` for
the schema-init machinery and the F-49 cross-process race fix.

Audit F-23 split this from a 928-line monolith into four modules:

- :mod:`_core` — :func:`get_data_dir`, :func:`registry_path`,
  :func:`campaign_db_path`, :func:`_connect`, the per-process schema-init
  bookkeeping, and the F-49-hardened :func:`_ensure_columns` helper.
- :mod:`campaigns` — the ``campaigns`` table: create / list / lookup,
  ck3_chronicler-cqo identity denormalisation, ck3_chronicler-z7l
  closing-chronicle persistence.
- :mod:`tracked` — the ``tracked_characters`` table: V02 biography
  opt-in list + vysp.10 pause/resume/bump lifecycle.
- :mod:`suppression` — the ``suppressed_event_kinds`` table:
  ck3_chronicler-fkw per-campaign quarantine silencing.

Every public symbol re-exports from this ``__init__`` for backwards
compatibility — callers should keep writing
``from chronicler.db.registry import Campaign, list_campaigns`` and
not reach into the sub-modules.
"""

from __future__ import annotations

# fmt: off
# Public re-exports use the redundant-alias form so ruff/pyright recognise
# them as explicit re-exports rather than unused imports. Schema-init
# internals (_connect, _INIT_LOCK, _INITIALIZED_PATHS, _ensure_columns, the
# *_REQUIRED_COLUMNS sets, row mappers, ...) are deliberately NOT surfaced
# on the package here (audit L22 / ck3_chronicler-27ov.79): migrate/* and
# white-box tests import them straight from the submodule (._core /
# .campaigns / .tracked). The public schema seams are ``connect`` and
# ``ensure_schema``.
from chronicler.db.registry._core import DATA_DIR_ENV as DATA_DIR_ENV
from chronicler.db.registry._core import campaign_db_path as campaign_db_path
from chronicler.db.registry._core import connect as connect
from chronicler.db.registry._core import ensure_schema as ensure_schema
from chronicler.db.registry._core import get_data_dir as get_data_dir
from chronicler.db.registry._core import registry_path as registry_path
from chronicler.db.registry.campaigns import ArchivedCampaignConflict as ArchivedCampaignConflict
from chronicler.db.registry.campaigns import Campaign as Campaign
from chronicler.db.registry.campaigns import archive_campaign as archive_campaign
from chronicler.db.registry.campaigns import create_campaign as create_campaign
from chronicler.db.registry.campaigns import delete_campaign as delete_campaign
from chronicler.db.registry.campaigns import (
    find_active_campaign_for_playthrough as find_active_campaign_for_playthrough,
)
from chronicler.db.registry.campaigns import (
    find_archived_match_for_playthrough as find_archived_match_for_playthrough,
)
from chronicler.db.registry.campaigns import get_campaign_by_id as get_campaign_by_id
from chronicler.db.registry.campaigns import get_campaign_by_name as get_campaign_by_name
from chronicler.db.registry.campaigns import get_tail_offset as get_tail_offset
from chronicler.db.registry.campaigns import list_campaigns as list_campaigns
from chronicler.db.registry.campaigns import rename_campaign as rename_campaign
from chronicler.db.registry.campaigns import resolve_campaign_for_save as resolve_campaign_for_save
from chronicler.db.registry.campaigns import set_auto_track_rules as set_auto_track_rules
from chronicler.db.registry.campaigns import (
    set_campaign_closing_chronicle as set_campaign_closing_chronicle,
)
from chronicler.db.registry.campaigns import set_campaign_last_tick as set_campaign_last_tick
from chronicler.db.registry.campaigns import set_tail_offset as set_tail_offset
from chronicler.db.registry.campaigns import touch_last_event_at as touch_last_event_at
from chronicler.db.registry.campaigns import unarchive_campaign as unarchive_campaign
from chronicler.db.registry.campaigns import update_campaign_overview as update_campaign_overview
from chronicler.db.registry.suppression import SuppressedKind as SuppressedKind
from chronicler.db.registry.suppression import add_suppressed_kind as add_suppressed_kind
from chronicler.db.registry.suppression import is_kind_suppressed as is_kind_suppressed
from chronicler.db.registry.suppression import list_suppressed_kinds as list_suppressed_kinds
from chronicler.db.registry.suppression import remove_suppressed_kind as remove_suppressed_kind
from chronicler.db.registry.tracked import TrackedCharacter as TrackedCharacter
from chronicler.db.registry.tracked import add_tracked_character as add_tracked_character
from chronicler.db.registry.tracked import bump_tracked_character as bump_tracked_character
from chronicler.db.registry.tracked import get_tracked_character as get_tracked_character
from chronicler.db.registry.tracked import get_tracked_character_ids as get_tracked_character_ids
from chronicler.db.registry.tracked import get_tracked_status as get_tracked_status
from chronicler.db.registry.tracked import is_character_tracked as is_character_tracked
from chronicler.db.registry.tracked import list_tracked_characters as list_tracked_characters
from chronicler.db.registry.tracked import pause_tracked_character as pause_tracked_character
from chronicler.db.registry.tracked import remove_tracked_character as remove_tracked_character
from chronicler.db.registry.tracked import resume_tracked_character as resume_tracked_character
from chronicler.db.registry.tracked import update_tracked_character as update_tracked_character

# fmt: on

__all__ = [
    # _core (public)
    "DATA_DIR_ENV",
    "campaign_db_path",
    "get_data_dir",
    "registry_path",
    # campaigns (public)
    "ArchivedCampaignConflict",
    "Campaign",
    "archive_campaign",
    "create_campaign",
    "delete_campaign",
    "find_active_campaign_for_playthrough",
    "find_archived_match_for_playthrough",
    "get_campaign_by_id",
    "get_campaign_by_name",
    "get_tail_offset",
    "list_campaigns",
    "rename_campaign",
    "resolve_campaign_for_save",
    "set_auto_track_rules",
    "set_campaign_closing_chronicle",
    "set_campaign_last_tick",
    "set_tail_offset",
    "touch_last_event_at",
    "unarchive_campaign",
    "update_campaign_overview",
    # suppression (public)
    "SuppressedKind",
    "add_suppressed_kind",
    "is_kind_suppressed",
    "list_suppressed_kinds",
    "remove_suppressed_kind",
    # tracked (public)
    "TrackedCharacter",
    "add_tracked_character",
    "bump_tracked_character",
    "get_tracked_character",
    "get_tracked_character_ids",
    "get_tracked_status",
    "is_character_tracked",
    "list_tracked_characters",
    "pause_tracked_character",
    "remove_tracked_character",
    "resume_tracked_character",
    "update_tracked_character",
]
