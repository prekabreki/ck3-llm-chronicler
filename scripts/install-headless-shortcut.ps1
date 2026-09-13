# ck3_chronicler-yrv3: install a Start Menu shortcut that launches
# chronicler-headless.bat. Optionally also creates a Desktop shortcut.
#
# Idempotent — re-running overwrites any existing shortcut at the same
# path. Run from PowerShell:
#
#     .\scripts\install-headless-shortcut.ps1                 (Start Menu only)
#     .\scripts\install-headless-shortcut.ps1 -IncludeDesktop (also Desktop)

[CmdletBinding()]
param(
    [switch]$IncludeDesktop
)

$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
# Target the .vbs wrapper, not the .bat. The .bat unavoidably opens a
# cmd window when launched directly from a shortcut; the .vbs hides it
# via WScript.Shell.Run intWindowStyle=0.
$VbsPath = Join-Path $RepoRoot 'scripts\chronicler-headless.vbs'

if (-not (Test-Path $VbsPath)) {
    Write-Error "Launcher .vbs not found at: $VbsPath"
    exit 1
}

function New-ChroniclerShortcut {
    param(
        [Parameter(Mandatory=$true)][string]$LinkPath
    )
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($LinkPath)
    $sc.TargetPath       = $VbsPath
    $sc.WorkingDirectory = $RepoRoot
    $sc.WindowStyle      = 1   # wscript.exe itself has no window; this is moot
    $sc.Description      = 'Launch Chronicler (headless, no console window)'
    $sc.Save()
    Write-Host "Created shortcut: $LinkPath"
}

$startMenu = [Environment]::GetFolderPath('Programs')
$startLink = Join-Path $startMenu 'Chronicler.lnk'
New-ChroniclerShortcut -LinkPath $startLink

if ($IncludeDesktop) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $desktopLink = Join-Path $desktop 'Chronicler.lnk'
    New-ChroniclerShortcut -LinkPath $desktopLink
}

Write-Host ''
Write-Host 'Done. Launch Chronicler from the Start Menu (or Desktop if -IncludeDesktop was passed).'
