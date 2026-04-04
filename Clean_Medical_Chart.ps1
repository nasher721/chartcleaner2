# Clipboard-only run (same as clean-chart.cmd with no args). Double-click or pin to taskbar.
# If double-click fails with "running scripts is disabled", run:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# Or create a shortcut targeting:
#   powershell.exe -ExecutionPolicy Bypass -File "C:\path\to\Clean_Medical_Chart.ps1"

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$script = Join-Path $Root "medical_cleaner.py"

if (-not (Test-Path $py)) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "Run install.ps1 once in this folder (right-click → Run with PowerShell).",
        "Medical Chart Cleaner"
    ) | Out-Null
    exit 1
}

Push-Location $Root
try {
    & $py $script
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($exitCode -eq 0) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "Cleaned text is on the clipboard.",
        "Medical Chart Cleaner"
    ) | Out-Null
}
