# Requires Python 3.10+ (recommended 3.11–3.14) for presidio-analyzer / spaCy wheels.
# No admin: use per-user Python + this folder; venv stays local. Prefer install.bat if PowerShell is locked down.
# If execution is blocked:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# Or run once:  powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) {
        & py -3 -m venv .venv
    } else {
        & python -m venv .venv
    }
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Error "Could not create .venv. Install Python 3.10+ from python.org and ensure 'py' or 'python' is on PATH."
    }
}

& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r (Join-Path $Root "requirements.txt")

Write-Host ""
Write-Host "Setup complete. From this folder run:"
Write-Host '  ."Run Chart Cleaner.bat"       # the app (double-click, opens in browser)'
Write-Host "  .\clean-chart.cmd              # clipboard in -> cleaned out"
Write-Host "  .\clean-chart.cmd -h           # file / folder options"
Write-Host ""
