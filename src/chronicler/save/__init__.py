"""CK3 save-file parsing for the v0.6 architectural pivot.

Subpackage that lets chronicler operate without ``-debug_mode`` by
parsing CK3 ``.ck3`` save files instead of tailing ``debug.log``. Save
files are emitted by CK3 on each autosave (default 5-yearly; user
should set monthly for v0.6's real-time-enough latency target).

Components:

- :mod:`chronicler.save.rakaly` — subprocess wrapper around the
  ``rakaly`` CLI (https://github.com/rakaly/cli) which converts a
  ``.ck3`` file to JSON. Pre-built binary, no Rust toolchain needed.
- :mod:`chronicler.save.parse` — extract characters, family, vanilla
  memories, played_character from the JSON. (V06-P01.)
- :mod:`chronicler.save.diff` — emit events from snapshot deltas.
  (V06-P02.)
- :mod:`chronicler.save.watcher` — watch the autosave directory.
  (V06-P03.)
- :mod:`chronicler.save.ingest` — full async ingest loop. (V06-P04.)

Limitation: ironman saves require external token data not distributed
with rakaly (per PDS counsel). chronicler scope explicitly excludes
ironman per docs/architecture.md, so this isn't addressed.
"""

from chronicler.save.cache import (
    DEFAULT_MAX_CACHE_BYTES,
    CachedSave,
    SaveCache,
    cache_dir_for,
)
from chronicler.save.diff import DiffEvent, diff_snapshots
from chronicler.save.ingest import (
    DEFAULT_SAVE_PATTERN,
    IngestResult,
    latest_save,
    process_save_pair,
    run_save_ingest,
    saves_by_recency,
)
from chronicler.save.parse import (
    AUTO_TRACK_RULES_DEFAULT,
    CharacterSnapshot,
    FamilySnapshot,
    MemorySnapshot,
    SaveSnapshot,
    auto_track_candidates,
    extract_character_record,
    parse_save,
    resolve_auto_track_rules,
)
from chronicler.save.rakaly import (
    DEFAULT_RAKALY_TIMEOUT_SECONDS,
    RakalyError,
    RakalyNotFoundError,
    convert_save_to_json,
    convert_save_to_json_async,
    find_rakaly,
)
from chronicler.save.watcher import (
    SaveDirectoryState,
    SaveFileEvent,
    watch_saves,
)

__all__ = [
    "CachedSave",
    "CharacterSnapshot",
    "DEFAULT_MAX_CACHE_BYTES",
    "DEFAULT_RAKALY_TIMEOUT_SECONDS",
    "DEFAULT_SAVE_PATTERN",
    "saves_by_recency",
    "DiffEvent",
    "FamilySnapshot",
    "IngestResult",
    "MemorySnapshot",
    "RakalyError",
    "RakalyNotFoundError",
    "SaveCache",
    "SaveDirectoryState",
    "SaveFileEvent",
    "AUTO_TRACK_RULES_DEFAULT",
    "resolve_auto_track_rules",
    "SaveSnapshot",
    "auto_track_candidates",
    "cache_dir_for",
    "convert_save_to_json",
    "convert_save_to_json_async",
    "diff_snapshots",
    "extract_character_record",
    "find_rakaly",
    "latest_save",
    "parse_save",
    "process_save_pair",
    "run_save_ingest",
    "watch_saves",
]
