$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Proje Python ortami bulunamadi: $python"
}

Set-Location -LiteralPath $projectRoot
& $python -m streamlit run app_us200.py --server.port 8502 --server.address localhost
