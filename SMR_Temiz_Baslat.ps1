# SMR Temiz Baslat - once TUM app.py kopyalarini kapatir, sonra TEK temiz kopya acar.
# "Kopya ustune kopya + 8501 tikanmasi" dertini bitirir. (ASCII-only: PS 5.1 uyumu)
$ErrorActionPreference = "SilentlyContinue"
$proj = "C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal"
Set-Location $proj

Write-Host "=== SMR TEMIZ BASLAT ===" -ForegroundColor Cyan

# 1) Calisan tum app.py streamlit kopyalarini bul ve kapat
$app = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
       Where-Object { $_.CommandLine -match 'streamlit.exe.*run app.py' }
if ($app) {
    Write-Host ("[1] {0} eski kopya bulundu, kapatiliyor..." -f @($app).Count) -ForegroundColor Yellow
    $app | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
} else {
    Write-Host "[1] Calisan eski kopya yok." -ForegroundColor Gray
}

# 2) 8501 portu bosalana kadar bekle (max 15sn)
for ($i=0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    if (-not (Get-NetTCPConnection -LocalPort 8501 -State Listen)) { break }
}
if (Get-NetTCPConnection -LocalPort 8501 -State Listen) {
    Write-Host "[2] UYARI: 8501 hala dolu. Baska bir sey tutuyor olabilir." -ForegroundColor Red
} else {
    Write-Host "[2] Port 8501 bosaldi." -ForegroundColor Green
}

# 3) Tek temiz kopya baslat
Write-Host "[3] Temiz kopya baslatiliyor..." -ForegroundColor Cyan
Start-Process -FilePath "$proj\.venv\Scripts\streamlit.exe" `
    -ArgumentList "run app.py --server.port 8501 --server.headless true" `
    -RedirectStandardOutput "$proj\logs\streamlit_local.log" `
    -RedirectStandardError  "$proj\logs\streamlit_local_err.log" `
    -WindowStyle Hidden

# 4) Acilmasini bekle, saglik kontrolu, Brave'i ac
$ok = $false
for ($i=0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    try {
        $h = Invoke-WebRequest "http://localhost:8501/_stcore/health" -UseBasicParsing -TimeoutSec 5
        if ($h.StatusCode -eq 200) { $ok = $true; break }
    } catch {}
}
if ($ok) {
    Write-Host "[4] HAZIR - site ayakta. Brave aciliyor..." -ForegroundColor Green
    $brave = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    if (Test-Path $brave) {
        Start-Process $brave "http://localhost:8501"
    } else {
        Start-Process "http://localhost:8501"
    }
} else {
    Write-Host "[4] Acilmadi. Hata logu:" -ForegroundColor Red
    Get-Content "$proj\logs\streamlit_local_err.log" -Tail 15
    Write-Host ""
    Write-Host "Kapatmak icin bir tusa bas..."
    [void][System.Console]::ReadKey($true)
}
