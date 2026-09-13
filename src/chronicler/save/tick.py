"""Pure save-tick kernel (ck3_chronicler-27ov.40 / M-I4).

Snapshot-pair in, ingested events + advanced baseline out. Lifted out of
``save/ingest.py`` so the diff/baseline/event-emit kernel is separable from
the producer/consumer loop and startup seeding that drive it. The live
consumer and the startup backlog drain both call :func:`_advance_baseline`;
one-shot flows (tests, ``import-save``) call :func:`process_save_pair`.

Depends only on leaves (diff, baseline, db, overview, the narrative pause
gate); it never reaches back into the ingest loop, so the dependency edge is
one-directional: ingest -> tick -> narrative.pause.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic as time_monotonic
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    # Runtime import would be a save → api circular; type-check only.
    from chronicler.api.events import EventBus

from chronicler.db.engine import (
    session_scope,
)
from chronicler.db.registry import (
    add_tracked_character,
    get_campaign_by_id,
    get_tracked_character_ids,
    set_campaign_last_tick,
    touch_last_event_at,
)
from chronicler.db.repository import (
    append_coa_history_if_changed,
    upsert_character,
)
from chronicler.ingest_common import ingest_event
from chronicler.narrative.pause import _is_llm_paused
from chronicler.narrative.scheduler import NarrativeScheduler
from chronicler.save.baseline import (
    save_baseline,
)
from chronicler.save.character_columns import hydrate_character_columns
from chronicler.save.diff import DiffEvent, diff_snapshots
from chronicler.save.overview import apply_campaign_overview_from_snap
from chronicler.save.parse import (
    SaveSnapshot,
    auto_track_candidates,
)
from chronicler.save.worldbuilding import detect_great_cause, summarise_region
from chronicler.util.dates import parse_ck3_date

log = logging.getLogger(__name__)


ProcessOutcome = Literal["ingested", "duplicate", "error"]


@dataclass(frozen=True, slots=True)
class IngestResult:
    outcome: ProcessOutcome
    event_id: int | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _tally_ingested_events(
    diffs: list[DiffEvent],
    results: list[IngestResult],
) -> tuple[int, dict[str, int]]:
    """ck3_chronicler-bges: pure tally helper — zip-pair diffs with results
    and count only 'ingested' outcomes. Returns (event_count, event_type_tally).
    Duplicates are excluded (matches per-event frame semantics)."""
    tally: dict[str, int] = {}
    count = 0
    for d, r in zip(diffs, results, strict=True):
        if r.outcome != "ingested":
            continue
        kind = d.event.t
        tally[kind] = tally.get(kind, 0) + 1
        count += 1
    return count, tally


def _publish_safely(
    event_bus: EventBus | None,
    campaign_id: str | None,
    frame: dict,
    *,
    what: str,
) -> None:
    """Publish one SSE frame, swallowing and logging any failure.

    No-op when there's no bus or campaign. The streaming side-channel is
    observability only — it must never break ingest. ck3_chronicler-27ov.76
    (audit L6): collapses the identical guard + try/except/log blocks that
    every publisher in this module repeated. ``what`` labels the frame in
    the failure log.
    """
    if event_bus is None or campaign_id is None:
        return
    try:
        event_bus.publish(campaign_id, frame)
    except Exception:
        log.exception("event_bus.publish failed for %s", what)


def _publish_save_pair_completed(
    *,
    event_bus: EventBus | None,
    campaign_id: str | None,
    save_filename: str,
    in_game_date: str | None,
    event_count: int,
    event_type_tally: dict[str, int],
) -> None:
    """ck3_chronicler-bges: publish the end-of-tick SSE frame. Takes
    pre-computed count + tally (from :func:`_tally_ingested_events`) so
    the caller can persist before publishing and avoid a race where a
    fast subscriber re-fetches /api/campaigns before the registry write
    has committed."""
    _publish_safely(
        event_bus,
        campaign_id,
        {
            "kind": "save_pair_completed",
            "save_filename": save_filename,
            "in_game_date": in_game_date,
            "completed_at": _now_iso(),
            "event_count": event_count,
            "event_type_tally": event_type_tally,
        },
        what=f"save_pair_completed ({save_filename})",
    )


def _publish_save_dropped_foreign(
    *,
    event_bus: EventBus | None,
    campaign_id: str | None,
    snap: SaveSnapshot,
    last_snapshot: SaveSnapshot,
    save_path_name: str,
) -> None:
    """ck3_chronicler-kgqa: announce that a save was dropped because its
    playthrough doesn't match this campaign's pin. The FE derives the
    red 'wrong game — not recording' badge from this."""
    _publish_safely(
        event_bus,
        campaign_id,
        {
            "kind": "save_dropped_foreign",
            "observed_playthrough_id": snap.playthrough_id,
            "campaign_playthrough_id": last_snapshot.playthrough_id,
            "save_filename": save_path_name,
            "observed_at": _now_iso(),
        },
        what=f"save_dropped_foreign ({save_path_name})",
    )


def _publish_tick_complete(
    snap: SaveSnapshot,
    *,
    campaign_id: str,
    save_path_name: str,
    event_count: int,
    event_type_tally: dict[str, int],
    registry_path: Path | None,
    event_bus: EventBus | None,
) -> None:
    """ck3_chronicler-70cc: end-of-tick tail shared by both _advance_baseline
    branches (event-laden success and the 87pi empty-tracked heartbeat).

    Persists last-tick summary to the registry first, then publishes the
    SSE save_pair_completed frame — bges ordering: a fast subscriber that
    re-fetches /api/campaigns on the frame sees the committed fields.
    Centralised so a future change to the SSE schema or last_tick columns
    applies to both code paths.
    """
    set_campaign_last_tick(
        campaign_id,
        save_filename=save_path_name,
        ingested_at=_now_iso(),
        in_game_date=snap.current_date,
        event_count=event_count,
        event_type_tally=event_type_tally,
        registry=registry_path,
    )
    _publish_save_pair_completed(
        event_bus=event_bus,
        campaign_id=campaign_id,
        save_filename=save_path_name,
        in_game_date=snap.current_date,
        event_count=event_count,
        event_type_tally=event_type_tally,
    )


def _upsert_character_from_snapshot(
    session: Session,
    char_id: int,
    snap: SaveSnapshot | None,
    **extra_fields: Any,
) -> None:
    """Upsert ``char_id``, hydrating identity fields from the save snapshot.

    ck3_chronicler-6gs: the diff path used to upsert participants by
    ID alone, leaving newly-referenced characters (e.g. a freshly-born
    child surfacing in a birth event) as faceless rows with NULL name +
    birth_date. Downstream consolidation prompts then read those NULLs
    as "unknown / probably dead", which the LLM rationalises into
    fabricated narrative beats.

    Hydrating from ``snap.characters[id]`` populates first_name,
    nickname, female, birth_date, death_date when the snapshot has the
    character (which it does for every alive + dead-unprunable
    character — exactly the universe the diff can reference). Extra
    keyword args win over snapshot-derived fields; ``upsert_character``
    skips None values, so partial snapshots don't clobber existing data.
    """
    fields: dict[str, Any] = {}
    if snap is not None:
        char_snap = snap.characters.get(char_id)
        if char_snap is not None:
            fields["first_name"] = char_snap.first_name
            fields["nickname"] = char_snap.nickname
            fields["female"] = char_snap.female
            fields["birth_date"] = char_snap.birth_date
            fields["death_date"] = char_snap.death_date
    fields.update(extra_fields)
    upsert_character(session, ck3_id=char_id, **fields)


def _event_ingested_frame(event: Any, event_id: int) -> dict:
    """The minimal ``event_ingested`` SSE payload for one ingested row.

    Single source of truth for the frame shape, shared by the immediate
    publish in :func:`_ingest_diff_event` (the synthetic/import path) and
    the deferred post-commit publish in :func:`_ingest_diff_events` (the
    live watcher path — ck3_chronicler-27ov.76, audit L7)."""
    return {
        "kind": "event_ingested",
        "event_id": event_id,
        "event_type": event.t,
        "event_date": event.d,
        "character_id": event.c,
    }


def _ingest_diff_event(
    diff: DiffEvent,
    *,
    session: Session,
    save_path_name: str,
    snap: SaveSnapshot | None = None,
    scheduler: NarrativeScheduler | None = None,
    event_bus: EventBus | None = None,  # ck3_chronicler-ek2
    bus_campaign_id: str | None = None,
) -> IngestResult:
    """Insert one DiffEvent + its participants. Schedule biography on death.

    When ``event_bus`` + ``bus_campaign_id`` are provided, publishes a
    minimal event dict per ingested row for the SSE pipeline (skipped
    on duplicates). Bus failures are swallowed — the persistence path
    is the source of truth, not the streaming side-channel.
    """
    event = diff.event
    # Diff scope participants arrive as (role, id); flip to (id, role).
    # Diff scope wins ordering (payload_participants_first=False) since
    # the diff is the primary source for save-tail; payload is a backstop.
    diff_pairs = [(char_id, role) for role, char_id in diff.participants]

    def _upsert(char_id: int) -> None:
        # ck3_chronicler-6gs: hydrate every character (incl. newly-born
        # children referenced in birth/marriage events) so the LLM never
        # sees a faceless ID.
        _upsert_character_from_snapshot(session, char_id, snap)

    def _log_paused_skip(char_id: int) -> None:
        # ck3_chronicler-gx7b: while LLM-paused, the death event still
        # lands in the DB — only the biography schedule is skipped. On
        # unpause, drain_for_campaign re-finds dead-without-biography
        # characters and fires them.
        log.info(
            "death event for character %d ingested but biography schedule skipped (LLM paused)",
            char_id,
        )

    def _publish(event_id: int) -> None:
        # ck3_chronicler-ek2: fan out to any SSE subscribers. Minimal
        # payload keeps the bus cheap; the frontend decides what to fetch
        # in detail. Failures don't halt ingest.
        _publish_safely(
            event_bus,
            bus_campaign_id,
            _event_ingested_frame(event, event_id),
            what=f"event_ingested (char={event.c} t={event.t})",
        )

    raw_line = f"save-diff: src={save_path_name} t={event.t} d={event.d} c={event.c}"

    event_id = ingest_event(
        event,
        session=session,
        event_date_iso=parse_ck3_date(event.d),
        wall_clock_at=_now_iso(),
        raw_line=raw_line,
        upsert_character_fn=_upsert,
        scope_participants=diff_pairs,
        scheduler=scheduler,
        should_schedule_death=lambda: not _is_llm_paused(),
        on_death_schedule_skipped=_log_paused_skip,
        on_ingested=_publish,
    )
    if event_id is None:
        return IngestResult(outcome="duplicate")

    return IngestResult(outcome="ingested", event_id=event_id)


def _persist_baseline_safely(path: Path | None, snap: SaveSnapshot) -> None:
    """Persist the snapshot if a path is configured. Failures are logged
    and swallowed so a disk-full or permission glitch can't kill the loop —
    the next successful write picks up the new state.

    ck3_chronicler-kig9: also swallow TypeError/ValueError so a future
    SaveSnapshot field added without a corresponding _snapshot_to_dict
    override warns + drops the persist instead of killing the consumer
    task mid-ingest. The next live tick re-parses from rakaly anyway."""
    if path is None:
        return
    try:
        save_baseline(path, snap)
    except (OSError, TypeError, ValueError) as e:
        log.warning("failed to persist baseline to %s: %s; continuing in-memory", path, e)


def _is_advance_candidate(
    snap: SaveSnapshot,
    last_snapshot: SaveSnapshot,
    save_path_name: str,
) -> Literal["ok", "foreign", "stale"]:
    """Phase 1 guard — drop wrong-playthrough or stale-read saves before
    diffing. Returns ``"ok"`` when the save should be processed, ``"foreign"``
    when the playthrough ID does not match the campaign baseline, and
    ``"stale"`` when the in-game date has not advanced.

    Two checks live here:

    - **ck3_chronicler-7on (playthrough mismatch)**: the save-cache may
      contain saves the watcher picked up from the same directory before
      the campaign DB was pinned (e.g. user played campaign B, then loaded
      campaign A which is what this DB is pinned to). Without this check,
      a wrong-playthrough save with a later in-game date would bypass the
      forward-only stale-read guard, fail diff with a ValueError, and the
      old except handler would install it as the baseline — wedging every
      subsequent right-playthrough save as "stale" because its date is
      earlier than the wrong baseline. Verified live during v0.7 smoke:
      26 cached saves processed, only 1 event reached the DB.

    - **Forward-only progression**: CK3's atomic autosave write produces
      multiple watcher events for one logical save; the OS file cache +
      rakaly's parser sometimes return the *previous* state on a re-read
      inside the same write window, presenting as the current_date going
      backwards. Without this guard, each backward-read triggers another
      full diff against last_snapshot and ingests phantom events.
      Verified live: a fast-forwarding session produced ~10 backward
      jumps per minute, each ingesting ~1500 phantom events. If new <=
      last, treat as a stale read and ignore — keep last_snapshot at the
      high-water mark.
    """
    if (
        snap.playthrough_id
        and last_snapshot.playthrough_id
        and snap.playthrough_id != last_snapshot.playthrough_id
    ):
        log.warning(
            "save %s is from playthrough %s but campaign baseline is %s; "
            "dropping without touching baseline",
            save_path_name,
            snap.playthrough_id,
            last_snapshot.playthrough_id,
        )
        return "foreign"

    new_iso = parse_ck3_date(snap.current_date)
    old_iso = parse_ck3_date(last_snapshot.current_date)
    if new_iso is not None and old_iso is not None and new_iso <= old_iso:
        log.debug(
            "save %s not newer than baseline %s; ignoring (stale read)",
            snap.current_date,
            last_snapshot.current_date,
        )
        return "stale"
    return "ok"


def _safe_diff(
    snap: SaveSnapshot,
    last_snapshot: SaveSnapshot,
    tracked_set: set[int],
    save_path_name: str,
) -> list[DiffEvent] | None:
    """Phase 3 — run :func:`diff_snapshots` with belt-and-suspenders for
    ck3_chronicler-7on. The pre-diff playthrough check in
    :func:`_is_advance_candidate` already drops mismatched saves; this
    handler is reached only if the diff layer raises a ValueError for
    some other reason. Returning None signals the orchestrator to drop
    the save and keep the existing baseline — installing ``snap`` as the
    new baseline would wedge ingest when the next right-playthrough save
    arrived with an earlier date and got swallowed by the stale-read
    guard.
    """
    try:
        return diff_snapshots(last_snapshot, snap, tracked_filter=tracked_set)
    except ValueError as e:
        log.warning(
            "diff failed for %s: %s; dropping save, baseline preserved",
            save_path_name,
            e,
        )
        return None


def _persist_per_save_state(
    snap: SaveSnapshot,
    *,
    campaign_id: str,
    registry_path: Path | None,
    persist_path: Path | None,
) -> None:
    """Phase 6 — refresh registry timestamp + denormalised identity
    columns + flush the baseline to disk after a successful tick.

    ck3_chronicler-cqo: snapshots per-campaign identity onto the registry
    row so the Library page is one query per card and archived campaigns
    retain a frozen byline. Identity comes from the snap directly (not
    the per-campaign DB) to stay consistent with what
    :func:`_refresh_tracked_characters` wrote to the DB this same tick.
    """
    # ck3_chronicler-9xa6: pass snap.current_date so the registry's
    # last_event_in_game_date stays in sync with each tick. The Closing
    # page reads this for "Closed" + Span; the wall-clock last_event_at
    # remains the "last ingested" indicator.
    touch_last_event_at(
        campaign_id,
        in_game_date=snap.current_date or None,
        registry=registry_path,
    )
    apply_campaign_overview_from_snap(campaign_id, snap, registry=registry_path)
    _persist_baseline_safely(persist_path, snap)


def _handle_empty_tracked_tick(
    snap: SaveSnapshot,
    *,
    campaign_id: str,
    save_path_name: str,
    registry_path: Path | None,
    persist_path: Path | None,
    event_bus: EventBus | None,
) -> SaveSnapshot:
    """ck3_chronicler-70cc + 87pi: handle a tick that arrives before any
    characters are opted in.

    Treating an empty tracked set as "no filter, emit everything" is
    exactly the failure the filter exists to prevent — so we advance the
    baseline silently. The baseline still persists so a later restart
    with tracking enabled diffs against the right starting point, and
    the bges heartbeat (event_count=0) still fires so the
    IngestActivityStrip shows the chronicler is alive even before the
    user has opted anyone in.
    """
    log.info(
        "save %s observed but ingested 0 events (no tracked characters); "
        "run `chronicler auto-track --campaign <name>` to opt characters in",
        snap.current_date,
    )
    _persist_baseline_safely(persist_path, snap)
    _publish_tick_complete(
        snap,
        campaign_id=campaign_id,
        save_path_name=save_path_name,
        event_count=0,
        event_type_tally={},
        registry_path=registry_path,
        event_bus=event_bus,
    )
    return snap


def _maybe_notify_foreign_playthrough(
    snap: SaveSnapshot,
    last_snapshot: SaveSnapshot,
    *,
    on_foreign_playthrough: Callable[[str, Path], None] | None,
    registry_path: Path | None,
    save_path_name: str,
    event_bus: EventBus | None = None,
    current_campaign_id: str | None = None,
) -> None:
    """ck3_chronicler-3v0s: when save-tail drops a save due to a
    playthrough mismatch (NOT a stale-read), look up the foreign
    playthrough_id in the registry; if it matches a known-active
    campaign, fire ``on_foreign_playthrough(camp_id, db_path)`` so the
    orchestrator can spawn a concurrent ingest for it.

    ck3_chronicler-n52f: also publish a ``campaign_auto_resumed`` SSE
    frame on the CURRENT campaign's bus (the one the SPA is subscribed
    to) so the AppShell can render a toast with a one-click [Switch]
    action. The frame fires AFTER the on_foreign_playthrough callback
    so the spawn is in flight by the time the SPA hears about it.

    No-ops when no callback is wired (headless tests, ``chronicler
    save-tail`` standalone). Lookups against unknown / archived
    playthroughs return None and we stay silent — auto-adopting an
    unknown save is a user decision.
    """
    if on_foreign_playthrough is None:
        return
    if not snap.playthrough_id or not last_snapshot.playthrough_id:
        return
    if snap.playthrough_id == last_snapshot.playthrough_id:
        return
    # Lazy import — chronicler.save.ingest is imported during registry
    # bootstrap in some test fixtures; module-level import would cycle.
    from chronicler.db.registry import find_active_campaign_for_playthrough

    match = find_active_campaign_for_playthrough(snap.playthrough_id, registry=registry_path)
    if match is None:
        return
    # ck3_chronicler-27ov.2 (audit H4): never auto-resume/cancel against
    # ourselves. find_active_campaign_for_playthrough has no self-exclusion,
    # so if a hijacked baseline makes our OWN current-playthrough saves look
    # "foreign", the lookup resolves to this very campaign. Firing the callback
    # would cancel our own ingest (self-cancel wedge) and publish a spurious
    # campaign_auto_resumed frame pointing at the campaign already on screen.
    if current_campaign_id is not None and match.id == current_campaign_id:
        log.debug(
            "foreign-playthrough lookup for save %s resolved to the current "
            "campaign %s; not auto-resuming against ourselves",
            save_path_name,
            current_campaign_id,
        )
        return
    log.warning(
        "save %s belongs to active campaign %r (id=%s) — auto-resuming "
        "its ingest alongside the current one",
        save_path_name,
        match.name,
        match.id,
    )
    try:
        on_foreign_playthrough(match.id, Path(match.db_path))
    except Exception:
        log.exception(
            "on_foreign_playthrough callback raised for campaign %s",
            match.id,
        )

    # ck3_chronicler-n52f: surface the swap to the SPA via the current
    # campaign's bus. The SPA listens on /api/sse/ingest/{current} so
    # we publish there; the toast offers a one-click switch into the
    # resumed campaign.
    _publish_safely(
        event_bus,
        current_campaign_id,
        {
            "kind": "campaign_auto_resumed",
            "campaign_id": match.id,
            "campaign_name": match.name,
            "save_path_name": save_path_name,
            "in_game_date": snap.current_date,
        },
        what=f"campaign_auto_resumed (campaign {match.id})",
    )


def _advance_baseline(
    snap: SaveSnapshot,
    *,
    last_snapshot: SaveSnapshot,
    factory,
    scheduler: NarrativeScheduler | None,
    registry_path: Path | None,
    campaign_id: str,
    save_path_name: str,
    persist_path: Path | None,
    raw_save_data: dict[str, Any] | None = None,
    event_bus: EventBus | None = None,  # ck3_chronicler-ek2
    on_foreign_playthrough: Callable[[str, Path], None] | None = None,
) -> SaveSnapshot:
    """Diff ``snap`` against ``last_snapshot``, ingest the events, and
    persist the new baseline. Returns the new ``last_snapshot`` (which is
    ``snap`` on advance, or ``last_snapshot`` unchanged on a drop).

    Used both by the live watcher loop and by save-tail startup catch-up
    (ck3_chronicler-96m): when a persisted baseline is loaded from disk, the
    startup backlog (prior-run cached saves + the current on-disk save) is
    seeded into the pipeline queue and drained through this same kernel
    (ck3_chronicler-27ov.12), so the multi-campaign-restart gap emits
    state-diff events instead of being silently swallowed.

    Orchestrator pattern (matches the sibling startup phase
    :func:`_verify_current_save` / :func:`_select_starting_baseline`):

    1. :func:`_is_advance_candidate` — drop wrong-playthrough / stale reads.
    2. :func:`_handle_empty_tracked_tick` — silent baseline + bges heartbeat
       when the user has no tracked characters yet.
    3. :func:`_safe_diff` — diff or drop on ValueError.
    4. :func:`_refresh_tracked_characters` + :func:`_ingest_diff_events` +
       :func:`_auto_track_new_candidates`.
    5. :func:`_persist_per_save_state` — registry + baseline to disk.
    6. :func:`_publish_tick_complete` — last_tick + save_pair_completed SSE.
    """
    # 1. Drop wrong-playthrough or stale-read saves untouched.
    candidate = _is_advance_candidate(snap, last_snapshot, save_path_name)
    if candidate != "ok":
        if candidate == "foreign":
            _publish_save_dropped_foreign(
                event_bus=event_bus,
                campaign_id=campaign_id,
                snap=snap,
                last_snapshot=last_snapshot,
                save_path_name=save_path_name,
            )
        # ck3_chronicler-3v0s: if the drop reason is a known foreign
        # playthrough, kick off its ingest alongside ours.
        # ck3_chronicler-n52f: also publish a campaign_auto_resumed
        # frame so the SPA can toast + offer one-click switch.
        _maybe_notify_foreign_playthrough(
            snap,
            last_snapshot,
            on_foreign_playthrough=on_foreign_playthrough,
            registry_path=registry_path,
            save_path_name=save_path_name,
            event_bus=event_bus,
            current_campaign_id=campaign_id,
        )
        return last_snapshot

    # 2. Resolve tracked-character set once per autosave so diff_snapshots
    # can prune untracked NPCs at source. Without this, a 40k-character
    # bookmark produces ~2000 events per in-game month — most of them
    # travel deltas for NPCs the user will never look at, polluting the
    # DB and burning candidacy checks. import-save remains unfiltered
    # (it's the explicit "backfill everything" command).
    tracked_set = get_tracked_character_ids(campaign_id, registry=registry_path)
    if not tracked_set:
        return _handle_empty_tracked_tick(
            snap,
            campaign_id=campaign_id,
            save_path_name=save_path_name,
            registry_path=registry_path,
            persist_path=persist_path,
            event_bus=event_bus,
        )

    # 3. Compute diffs; ValueError preserves last_snapshot.
    t_diff0 = time_monotonic()
    diffs = _safe_diff(snap, last_snapshot, tracked_set, save_path_name)
    if diffs is None:
        return last_snapshot
    # ck3_chronicler-0fi5: diff cost is the other half of ingestion next
    # to parse — log it so the baseline covers the full snapshot->events path.
    log.info("diffed %s in %.2fs", snap.current_date, time_monotonic() - t_diff0)

    # 4. Refresh tracked-character rows + ingest events.
    _refresh_tracked_characters(
        snap=snap,
        tracked_set=tracked_set,
        factory=factory,
    )
    log.info("save %s -> %d events to ingest", snap.current_date, len(diffs))
    chars_with_new_events, results = _ingest_diff_events(
        diffs,
        factory=factory,
        save_path_name=save_path_name,
        scheduler=scheduler,
        event_bus=event_bus,
        campaign_id=campaign_id,
        snap=snap,
    )

    # ck3_chronicler-44x5: pick up new family members (spouse on
    # marriage, child on birth) that the player gained mid-campaign.
    # Without this, the marriage diff event lands but the new spouse
    # is never added to tracked_characters, so every subsequent event
    # about her is filtered out at diff-time.
    _auto_track_new_candidates(
        snap=snap,
        tracked_set=tracked_set,
        campaign_id=campaign_id,
        registry_path=registry_path,
    )

    # Memory consolidation sweep removed (plan: cozy-coalescing-shannon).
    # Events still accumulate in the DB on each tick (above); the
    # boundary-firing sweep is gone. Biography still fires at death.
    _ = chars_with_new_events  # touched here for future-proof gate reuse

    # 6. Flush registry overview + baseline to disk.
    _persist_per_save_state(
        snap,
        campaign_id=campaign_id,
        registry_path=registry_path,
        persist_path=persist_path,
    )

    # 7. Persist last-tick summary + publish end-of-tick SSE frame.
    event_count, event_type_tally = _tally_ingested_events(diffs, results)
    _publish_tick_complete(
        snap,
        campaign_id=campaign_id,
        save_path_name=save_path_name,
        event_count=event_count,
        event_type_tally=event_type_tally,
        registry_path=registry_path,
        event_bus=event_bus,
    )
    return snap


def _auto_track_new_candidates(
    *,
    snap: SaveSnapshot,
    tracked_set: set[int],
    campaign_id: str,
    registry_path: Path | None,
) -> set[int]:
    """ck3_chronicler-44x5: pick up family members the player gained
    mid-campaign (new spouse on marriage, new child on birth).

    The diff layer already detects + emits the marriage / birth event,
    but the participants weren't being added to ``tracked_characters``
    — so subsequent events about the new family member were dropped by
    ``tracked_filter`` until the user manually ran auto-track.

    Re-running :func:`auto_track_candidates` per tick is cheap (it walks
    the player's immediate family only) and reuses the same rules-gate
    surface as the adopt-time and manual-endpoint paths. ``rules`` come
    from the campaign's saved ``auto_track_rules`` JSON; malformed JSON
    falls back to the legacy default (all categories on), matching
    :mod:`chronicler.api.routes.tracked` behaviour. Returns the set of
    newly-added IDs so the caller can log + extend ``tracked_set`` for
    the rest of the tick.
    """
    campaign = get_campaign_by_id(campaign_id, registry=registry_path)
    rules: dict[str, bool] | None = None
    if campaign is not None and campaign.auto_track_rules:
        try:
            parsed = json.loads(campaign.auto_track_rules)
            if isinstance(parsed, dict):
                rules = {k: bool(v) for k, v in parsed.items() if isinstance(k, str)}
        except json.JSONDecodeError:
            rules = None
    candidates = auto_track_candidates(snap, rules=rules)
    added: set[int] = set()
    for cid, note in candidates:
        if cid in tracked_set:
            continue
        add_tracked_character(campaign_id, cid, note=note, role=note, registry=registry_path)
        added.add(cid)
    if added:
        log.info(
            "save %s: auto-tracked %d new family member(s): %s",
            snap.current_date,
            len(added),
            sorted(added),
        )
    return added


def _refresh_tracked_characters(
    *,
    snap: SaveSnapshot,
    tracked_set: set[int],
    factory,
) -> None:
    """Refresh persisted Character rows for every tracked character.

    The diff layer only emits events on specific transitions (death,
    marriage, etc.); other live state — current first_name in case CK3
    renamed, nickname, culture/faith if converted — should track the
    live save without waiting on a transition event. Cheap: tracked_set
    is small (player + family) and the upsert is idempotent.
    """
    # ck3_chronicler-60m: build a character ID → first_name lookup from
    # the snapshot so extract_character_record can resolve family-member
    # IDs in the raw record. Built once per save-tick rather than per
    # character to keep the cost O(snapshot) not O(snapshot * tracked).
    name_lookup: dict[int, str] = {
        cid: char.first_name for cid, char in snap.characters.items() if char.first_name
    }
    with session_scope(factory) as session:
        for cid in tracked_set:
            char_snap = snap.characters.get(cid)
            if char_snap is None:
                continue
            # ck3_chronicler-vbfa: column derivation shared with the
            # offline import path via save/character_columns.py. The
            # save-tail extras (region_summary_json, great_cause_json,
            # CoA history append) layer on after the shared core.
            # ck3_chronicler-j86v: the save-tail path passes raw_save_data=None;
            # the per-character extractions (save_snapshot_json / coa_json) were
            # resolved INSIDE the parse worker and ride on snap.tracked_* so the
            # consumer never needs the 127 MB raw dict back over the pool boundary.
            columns = hydrate_character_columns(
                snap=snap,
                char=char_snap,
                cid=cid,
                raw_save_data=None,
                name_lookup=name_lookup,
            )
            coa_json = columns["coa_json"]
            # ck3_chronicler-8ek: persist the resolved region summary so
            # the death-biography pipeline can thread world-context into
            # the prompt without re-parsing the save.
            region_summary = summarise_region(snap, cid)
            region_summary_json = (
                json.dumps(region_summary, separators=(",", ":"))
                if region_summary is not None
                else None
            )
            # ck3_chronicler-7md7: persist any active great cause.
            great_cause = detect_great_cause(snap, cid)
            great_cause_json = (
                json.dumps(great_cause, separators=(",", ":")) if great_cause is not None else None
            )
            upsert_character(
                session,
                **columns,
                region_summary_json=region_summary_json,
                great_cause_json=great_cause_json,
            )
            # ck3_chronicler-7b8d: append a CoA history row when the
            # resolved coa_json differs from the most recent recorded
            # one. Cheap no-op when unchanged. The latest live value is
            # already on Character.coa_json (above); this table is the
            # audit trail surfaced as a medallion timeline in the folio.
            if coa_json is not None:
                append_coa_history_if_changed(
                    session,
                    character_id=cid,
                    coa_json=coa_json,
                    observed_at=datetime.now(UTC).isoformat(),
                )


def _ingest_diff_events(
    diffs: list[DiffEvent],
    *,
    factory,
    save_path_name: str,
    scheduler: NarrativeScheduler | None,
    event_bus: EventBus | None,
    campaign_id: str,
    snap: SaveSnapshot | None = None,
) -> tuple[set[int], list[IngestResult]]:
    """Apply each diff event in one session; log + count outcomes.

    Returns ``(chars_with_new_events, results)`` — the set of primary
    character IDs that gained at least one new event (used by the
    consolidation scheduler) and the per-event IngestResult list (used
    by _publish_save_pair_completed to tally event types). Per-event
    failures are caught and logged so a malformed payload doesn't break
    the rest of the batch (chronicler's "never stop on bad data" rule).
    Failed events get an ``outcome="error"`` placeholder so the list
    stays parallel to diffs for the zip in the tally helper without
    inflating the duplicate count (ck3_chronicler-zopa).
    """
    ingested = 0
    duplicate = 0
    errors = 0
    chars_with_new_events: set[int] = set()
    results: list[IngestResult] = []
    # ck3_chronicler-27ov.76 (audit L7): buffer event_ingested SSE frames
    # and publish them only AFTER the session_scope below commits. The
    # frame used to fire inside _ingest_diff_event (pre-commit); if the
    # tick's final commit then failed, every published frame became a
    # phantom feed entry for a row that never persisted. Note we pass NO
    # event_bus into _ingest_diff_event here so it can't publish early.
    pending_frames: list[dict] = []
    with session_scope(factory) as session:
        for d in diffs:
            try:
                # ck3_chronicler-27ov.20 (audit M-I1): wrap each event in its
                # own SAVEPOINT so a DB error rolls back THIS event alone. Before
                # this, one flush error (e.g. an IntegrityError) put the shared
                # session into pending-rollback — every later event then failed
                # with PendingRollbackError, the tick's final commit raised
                # (rolling back ALL of the tick's events), and the exception
                # propagated into the unguarded consumer (audit H5), stopping
                # ingestion until restart. begin_nested() recovers the session
                # to a clean state on rollback, so good events still commit.
                with session.begin_nested():
                    result = _ingest_diff_event(
                        d,
                        session=session,
                        save_path_name=save_path_name,
                        snap=snap,
                        scheduler=scheduler,
                    )
                results.append(result)
                if result.outcome == "ingested":
                    ingested += 1
                    chars_with_new_events.add(d.event.c)
                    if result.event_id is not None:
                        pending_frames.append(_event_ingested_frame(d.event, result.event_id))
                else:
                    duplicate += 1
            except Exception:
                errors += 1
                # ck3_chronicler-zopa: outcome="error" keeps results
                # parallel to diffs without inflating the duplicate count.
                results.append(IngestResult(outcome="error"))
                log.exception(
                    "ingest failed for diff event char=%d t=%s",
                    d.event.c,
                    d.event.t,
                )
    # The session_scope has committed (or rolled back on error) by here.
    # Publish the buffered frames now so a subscriber that re-fetches on
    # receipt always finds the row durably persisted (bges ordering).
    for frame in pending_frames:
        _publish_safely(
            event_bus,
            campaign_id,
            frame,
            what=f"event_ingested (event_id={frame.get('event_id')})",
        )
    # ck3_chronicler-6yzg: surface error count alongside ingested/duplicate so
    # any per-event exception (caught by the inner try/except above) is
    # visible in the steady-state ingest log. Without this, a malformed
    # payload's exception is logged via log.exception once but the
    # per-save summary still reads as if everything succeeded.
    log.info(
        "save %s: %d ingested, %d duplicate, %d errors (of %d diff events)",
        save_path_name,
        ingested,
        duplicate,
        errors,
        len(diffs),
    )
    return chars_with_new_events, results


def process_save_pair(
    prev: SaveSnapshot,
    curr: SaveSnapshot,
    *,
    session: Session,
    save_path_name: str = "(synthetic)",
    scheduler: NarrativeScheduler | None = None,
    event_bus: EventBus | None = None,  # ck3_chronicler-ek2
    bus_campaign_id: str | None = None,
) -> list[IngestResult]:
    """Synchronous batch helper: diff a snapshot pair and ingest all events.

    Useful for tests and one-shot ``chronicler import-save`` flows where
    we have two SaveSnapshots in memory without running the watcher.

    ck3_chronicler-bges: publishes ``save_pair_completed`` at the end of
    the tick (even when N=0) so the IngestActivityStrip + Library card
    have a heartbeat signal.
    """
    diffs: list[DiffEvent] = list(diff_snapshots(prev, curr))
    results = [
        _ingest_diff_event(
            d,
            session=session,
            save_path_name=save_path_name,
            snap=curr,
            scheduler=scheduler,
            event_bus=event_bus,
            bus_campaign_id=bus_campaign_id,
        )
        for d in diffs
    ]
    event_count, event_type_tally = _tally_ingested_events(diffs, results)
    _publish_save_pair_completed(
        event_bus=event_bus,
        campaign_id=bus_campaign_id,
        save_filename=save_path_name,
        in_game_date=curr.current_date,
        event_count=event_count,
        event_type_tally=event_type_tally,
    )
    return results
