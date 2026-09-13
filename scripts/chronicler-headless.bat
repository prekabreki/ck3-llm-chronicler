@echo off
REM ck3_chronicler-yrv3: launch chronicler with no visible console.
REM
REM This .bat is invoked indirectly through chronicler-headless.vbs,
REM which runs it via WScript.Shell.Run(cmd, 0, False) — the `0` hides
REM cmd.exe's window for the lifetime of this script. Anything we run
REM here inherits that hidden console.
REM
REM Critically, we DO NOT use `start "" python...` to detach the
REM backend: `start` creates a new window for console-subsystem
REM executables, and Python on uv-created venvs is console-subsystem
REM even for the `pythonw.exe` name (the launcher stub doesn't actually
REM flip the PE subsystem byte). Instead we run python directly so it
REM stays in the hidden console.
REM
REM Total visible windows during normal operation: 0. The .bat blocks
REM until chronicler exits, but the .vbs/wscript wrapper makes that
REM invisible to the user. Close chronicler by killing python.exe in
REM Task Manager or by closing the browser and signing out.

setlocal

REM Resolve relative to this script's directory so it works from any
REM CWD (Start Menu, Desktop shortcut, double-click).
set "REPO_ROOT=%~dp0.."
set "PYEXE=%REPO_ROOT%\.venv\Scripts\python.exe"
set "BUNDLE=%REPO_ROOT%\src\chronicler\api\static\app\index.html"
set "SPLASH=%REPO_ROOT%\scripts\headless-splash.html"
REM Forward-slash form for the splash URL — Chrome --app=file:/// needs
REM URL-style paths even on Windows. Substitute \ -> / at top level so
REM the variable can be safely referenced inside parens later (cmd's
REM block-parse semantics expand %SPLASH% before any in-block set runs).
set "SPLASH_URL=file:///%SPLASH:\=/%"

if not exist "%PYEXE%" (
  REM No visible console to print to under wscript — but if a user
  REM ran this .bat directly with `cmd /k`, the message is useful.
  echo python.exe not found at %PYEXE%
  echo Run the project venv setup first ^(see docs/running-headless.md^).
  pause
  exit /b 1
)

REM Auto-rebuild the FE bundle when frontend/src/ has changed since the
REM last build. Without this check, the headless shortcut silently
REM serves a stale SPA after every FE edit until the user remembers to
REM run `npm run build`. The PowerShell one-liner exits 1 when the
REM newest source-tree mtime is later than the bundle's index.html
REM mtime; on exit 1 we rebuild. Failures during rebuild fall through
REM to launching with the existing (stale) bundle — better stale than
REM crashed.
if exist "%BUNDLE%" (
  powershell -NoProfile -Command "$b=(Get-Item '%BUNDLE%').LastWriteTime; $n=(Get-ChildItem -Recurse '%REPO_ROOT%\frontend\src' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime; if($n -gt $b){exit 1}else{exit 0}"
  if errorlevel 1 (
    pushd "%REPO_ROOT%\frontend"
    call npm run build
    popd
  )
) else (
  REM No bundle at all — must build before serve, otherwise / serves 404.
  pushd "%REPO_ROOT%\frontend"
  call npm run build
  popd
)

REM Open Chrome / Edge IMMEDIATELY on a local splash file. The splash
REM HTML polls localhost:8000 (no-cors fetch) every 500ms and redirects
REM to the SPA the moment uvicorn binds. The user sees a spinner +
REM "The chronicler stirs..." within a second of clicking instead of
REM the previous 20-60s of nothing happening.
REM
REM Previous design used a PowerShell wait-then-launch loop that
REM blocked browser open until :8000 responded; the spinner-from-file
REM design replaces that with client-side polling.
set "BROWSER="
for %%P in (
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%LocalAppData%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
  "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
) do if not defined BROWSER if exist %%P set "BROWSER=%%~P"

if defined BROWSER (
  start "" "%BROWSER%" --app="%SPLASH_URL%"
)

REM Foreground run: blocks here until chronicler exits. Output goes
REM to the hidden console — invisible to the user. The In-app Logs
REM tab is the primary observability surface.
REM
REM Uses `dev` (web app + save-tail combined), NOT `serve` (web app
REM only). The original yrv3 .bat shipped with `serve`, which silently
REM disabled autosave ingestion — autosaves piled up on disk while
REM the FE showed stale data (no error, no FE indicator, just silence).
REM `dev` with no --campaign auto-detects from the latest save in the
REM CK3 save dir (ck3_chronicler-cqo / -nji); if no save is present
REM yet, it boots the Library in adoption mode and the user picks via
REM '+ Adopt save'. Either path wires save-tail correctly.
"%PYEXE%" -m chronicler.cli.main dev %*

endlocal
