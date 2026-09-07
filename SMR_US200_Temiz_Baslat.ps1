# 8502 (S&P 200) Temiz Baslat - once SADECE 8502 kopyalarini kapatir, sonra TEK temiz kopya acar.
# 8501 (BIST) surecine ASLA dokunmaz: yalniz komut satirinda 'app_us200.py' gecen surecleri hedefler.
# (ASCII-only: PS 5.1 uyumu)
# -Browser brave (varsayilan) veya chrome -> hangi tarayicida acilacagini secer.
param([ValidateSet('brave','chrome')][string]$Browser = 'brave')
$ErrorActionPreference = "SilentlyContinue"
$proj = "C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal"
Set-Location $proj

Write-Host "=== 8502 (S&P 200) TEMIZ BASLAT ===" -ForegroundColor Cyan

# 1) Calisan tum app_us200.py (8502) kopyalarini bul ve kapat. 8501/app.py'ye DOKUNMAZ.
$app = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
       Where-Object { $_.CommandLine -match 'app_us200\.py' }
if ($app) {
    Write-Host ("[1] {0} eski 8502 kopyasi bulundu, kapatiliyor..." -f @($app).Count) -ForegroundColor Yellow
    $app | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
} else {
    Write-Host "[1] Calisan eski 8502 kopyasi yok." -ForegroundColor Gray
}

# 2) 8502 portu bosalana kadar bekle (max 15sn)
for ($i=0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    if (-not (Get-NetTCPConnection -LocalPort 8502 -State Listen)) { break }
}
if (Get-NetTCPConnection -LocalPort 8502 -State Listen) {
    Write-Host "[2] UYARI: 8502 hala dolu. Baska bir sey tutuyor olabilir." -ForegroundColor Red
} else {
    Write-Host "[2] Port 8502 bosaldi." -ForegroundColor Green
}

# 3) Tek temiz 8502 kopyasi baslat (app_us200.py: US200 env'i kurar, sonra app.py'yi calistirir)
Write-Host "[3] Temiz 8502 kopyasi baslatiliyor (yalniz S&P 200)..." -ForegroundColor Cyan
if (-not (Test-Path "$proj\logs")) { New-Item -ItemType Directory -Path "$proj\logs" | Out-Null }
Start-Process -FilePath "$proj\.venv\Scripts\streamlit.exe" `
    -ArgumentList "run app_us200.py --server.port 8502 --server.address localhost --server.headless true" `
    -RedirectStandardOutput "$proj\logs\streamlit_us200.log" `
    -RedirectStandardError  "$proj\logs\streamlit_us200_err.log" `
    -WindowStyle Hidden

# 4) Acilmasini bekle, saglik kontrolu, Brave'i ac
$ok = $false
for ($i=0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    try {
        $h = Invoke-WebRequest "http://localhost:8502/_stcore/health" -UseBasicParsing -TimeoutSec 5
        if ($h.StatusCode -eq 200) { $ok = $true; break }
    } catch {}
}
if ($ok) {
    $chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
    $brave  = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    $exe = if ($Browser -eq 'chrome') { $chrome } else { $brave }
    Write-Host ("[4] HAZIR - 8502 ayakta. {0} aciliyor..." -f $Browser) -ForegroundColor Green
    if (Test-Path $exe) {
        Start-Process $exe "http://localhost:8502"
    } else {
        Start-Process "http://localhost:8502"
    }
} else {
    Write-Host "[4] Acilmadi. Hata logu:" -ForegroundColor Red
    Get-Content "$proj\logs\streamlit_us200_err.log" -Tail 15
    Write-Host ""
    Write-Host "Kapatmak icin bir tusa bas..."
    [void][System.Console]::ReadKey($true)
}
