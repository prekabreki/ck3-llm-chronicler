# Running Chronicler headlessly

Chronicler can run with no visible console window. Open it like a desktop
app, watch the in-app Logs tab for what's happening, and close it from
the Start Menu shortcut or Task Manager when you're done.

This complements `LAUNCH.bat` (the dev-mode launcher that keeps two
terminals visible — backend + Vite — for active FE development). For
daily play, use the headless launcher; for FE iteration, use `LAUNCH.bat`.

## One-time setup

Once you've cloned the repo and have the `.venv` populated:

```powershell
# Create a Start Menu shortcut (Chronicler.lnk)
.\scripts\install-headless-shortcut.ps1

# Or, also put a copy on the Desktop
.\scripts\install-headless-shortcut.ps1 -IncludeDesktop
```

The shortcut points at `scripts/chronicler-headless.bat`. Re-run the
script after moving the repo — the shortcut hardcodes the launcher path.

## Daily use

1. Click the **Chronicler** Start Menu (or Desktop) shortcut.
2. A small **"The chronicler stirs…"** splash opens immediately. The
   backend is parsing the latest autosave in the background; this
   typically takes 20–60 seconds on first start after a CK3 session.
   The splash auto-redirects to the SPA the moment `:8000` binds —
   nothing to click.
3. Use the in-app **Logs** tab (top-right `☷`) to see what the backend
   is doing. Logs stream live; cold-load shows the last 500 lines.
4. To stop: open the **Logs** tab (top-right `☷`) and click **Halt
   backend**. The browser window closes itself and chronicler exits
   after a short delay. Closing only the browser leaves chronicler
   running — use Halt backend to actually stop the process.

## Auto-rebuild on stale bundle

The launcher checks the mtime of `src/chronicler/api/static/app/index.html`
against the newest file under `frontend/src/`. If the source tree is newer,
it runs `npm run build` before launching python. First click after a FE
edit therefore takes ~5-10s longer than a normal start; subsequent clicks
are fast again. If `npm run build` fails (e.g. tsc error from a half-
written change), the launcher falls through to serving the existing
bundle — better stale than crashed.

## What the launcher does

Two files cooperate so no console window is ever visible:

- `scripts/chronicler-headless.vbs` — the shortcut's actual target. A
  `.vbs` is invoked by `wscript.exe`, which has no console of its own.
  The .vbs runs the .bat with `WScript.Shell.Run(cmd, 0, False)` —
  `0` hides the cmd window, `False` makes it fire-and-forget.
- `scripts/chronicler-headless.bat` — does the real work:
  - Resolves the repo root from the script's own location (and runs
    `npm run build` first if the bundle is missing or stale).
  - Opens Chrome/Edge `--app` mode *immediately* on a local splash
    page (`scripts/headless-splash.html`). The splash polls
    `localhost:8000` client-side every 500ms and redirects to the SPA
    the moment uvicorn binds — no probe-then-open wait.
  - Runs `python.exe -m chronicler.cli.main dev` *in the foreground*,
    so the python process inherits the .vbs's hidden console. The
    .bat blocks here until chronicler exits. It deliberately runs
    `dev` (web app + save-tail), NOT `serve` (web app only): `serve`
    silently disables autosave ingestion — autosaves pile up on disk
    while the FE shows stale data, with no error surfaced.

If neither Chrome nor Edge is installed, you can open
`http://localhost:8000` in any browser yourself.

### Why the .vbs is required

A Windows shortcut that targets a `.bat` directly opens a `cmd.exe`
console while the .bat runs — even with `WindowStyle = 7` (minimised)
on the shortcut, the window appears (minimized, or flashing). The .vbs
wrapper is the standard Windows trick for hiding a .bat entirely.

### Why python.exe, not pythonw.exe

On most Python installs, `pythonw.exe` is GUI-subsystem and runs with
no console. `uv`-created venvs ship a stub launcher where *both*
`python.exe` and `pythonw.exe` are console-subsystem — the names are
preserved for compatibility but neither actually suppresses a console.
Running `pythonw -m chronicler` via `start ""` therefore opens a
visible console window despite the name.

The fix is structural: the .bat runs `python.exe` directly (no
`start`) inside the .vbs's hidden console, so the console exists but
is invisible. The .bat blocks until chronicler exits, which is fine —
the .vbs/wscript wrapper makes the wait invisible to the user.

## Bypassing the .bat

For scripted use or non-default ports, invoke python directly (use
`dev` unless you genuinely want the web app without save-tail):

```powershell
& .venv\Scripts\python.exe -m chronicler.cli.main dev --port 8001
```

The Logs tab works the same regardless of how the backend was launched —
it ships with the backend.

## Troubleshooting

- **I still see a console window when I click the shortcut.** Re-run
  `install-headless-shortcut.ps1`. The installer overwrites the
  shortcut to point at the new `.vbs` wrapper (older installs pointed
  directly at the `.bat`, which Windows can't hide). After re-running,
  delete and re-pin any old taskbar/start-menu pins so the pin picks
  up the new target.
- **The shortcut clicks but nothing happens.** Open Task Manager and look
  for `python.exe` (the launcher runs the venv's `python.exe` inside
  the .vbs's hidden console — `pythonw.exe` is not involved; see "Why
  python.exe, not pythonw.exe" above). If it's there, the backend is
  up — try `http://localhost:8000` manually. If it isn't, run the .bat
  from PowerShell to see the actual error message (the .bat itself
  prints errors before `start`-ing).
- **`python.exe not found at …\.venv\…`**. The `.venv` is missing or
  incomplete. Re-create it with the project's documented setup
  commands, then re-run the .bat.
- **Logs tab says "Stream unavailable"**. The /api/sse/logs channel hit
  its 3-error threshold. The cold-start grace window (ck3_chronicler-jjqf)
  ignores the first 5 seconds of errors, so this usually means the
  backend isn't running — check `python.exe` in Task Manager.
