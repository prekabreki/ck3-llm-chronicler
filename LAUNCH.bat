@echo off
REM Start the FastAPI backend (:8000) and Vite frontend (:5173), then open
REM the app in a chromeless window (no tabs/URL bar).

setlocal
set "BROWSER="
for %%P in (
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%LocalAppData%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
  "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
) do if not defined BROWSER if exist %%P set "BROWSER=%%~P"

REM Backend in its own window so logs are visible. cmd /k keeps the window
REM open after the process exits, which makes crashes/tracebacks readable.
REM Boot can take ~10s on cold start because chronicler parses the latest
REM autosave to establish a baseline before uvicorn binds :8000.
start "chronicler backend" /D "%~dp0" cmd /k ".venv\Scripts\chronicler.exe dev"

REM Open the app in --app mode (chromeless window) as soon as Vite is up.
REM We deliberately don't wait for the backend (~10s baseline parse) — the
REM React Query client retries until the proxy stops 502'ing.
if defined BROWSER (
  start "" powershell -NoProfile -WindowStyle Hidden -Command "$d=(Get-Date).AddSeconds(15); while((Get-Date) -lt $d){ try{ $c=New-Object Net.Sockets.TcpClient; $c.Connect('localhost',5173); $c.Close(); break } catch { Start-Sleep -Milliseconds 200 } }; Start-Process -FilePath $env:BROWSER -ArgumentList '--app=http://localhost:5173'"
) else (
  echo Chrome/Edge not found - open http://localhost:5173 manually after Vite starts.
)

REM Frontend stays in this window so npm logs / Ctrl+C land here.
cd /d "%~dp0frontend"
call npm run dev
endlocal
