---
description: The save-tail's _advance_baseline runs under asyncio.to_thread, so anything it calls that needs the event loop finds none and must hand off threadsafe
type: gotcha
---

`run_save_ingest` runs `_advance_baseline` (diff, inserts, death scheduling) in `asyncio.to_thread` to keep the API responsive.
Inside it, `asyncio.get_running_loop()` raises, so loop-bound work silently does nothing unless it is handed back with `loop.call_soon_threadsafe`.

`NarrativeScheduler.schedule` skipped at debug level in exactly this case, so no live death ever auto-queued a biography.
The startup `drain_for_campaign` sweep ran on the loop and hid the bug, but only for deaths ingested before a restart.
The Godwin campaign lost Alcuin #16851954, Harold #16853749 and Sodelb #65505 this way, and none of them showed as dead on the Tracked page.

The scheduler now remembers its owning loop and re-dispatches off-thread calls onto it.
`EventBus.publish` does the same thing via `attach_loop`.
Test new loop-bound callees of the tick from a worker thread (`await asyncio.to_thread(...)`), not from the test's own loop.
