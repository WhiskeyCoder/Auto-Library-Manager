# Write a category-folder inventory report after a categorize run.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "python"
}
if ($args.Count -lt 1) {
    Write-Error "Usage: .\write-category-inventory.ps1 <output-library-root> [-o report.txt]"
    exit 1
}
Set-Location $here
& $py -m library_manager.inventory_cli @args
