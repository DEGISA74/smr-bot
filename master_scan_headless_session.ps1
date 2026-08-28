# 20:00'de Streamlit'i görünmez bir tarayıcı oturumunda açık tutar.
# Böylece mevcut kapanış kapısı, kullanıcı sayfayı yenilemese bile çalışır.
param(
    [string]$AppUrl = 'http://127.0.0.1:8501',
    [int]$MaxMinutes = 180,
    [int]$TargetHour = 20
)

$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$completionPath = Join-Path $base 'logs\kapanis_master_scan_completion.json'
$overridePath = Join-Path $base 'logs\master_scan_schedule_override.json'
$python = Join-Path $base '.venv\Scripts\python.exe'

# Tarihe bağlı istisna yalnız o gün uygulanır; sonraki işlem günleri varsayılan
# 20:00 kapanış kapısına kendiliğinden döner.
if (Test-Path -LiteralPath $overridePath) {
    try {
        $override = Get-Content -LiteralPath $overridePath -Raw | ConvertFrom-Json
        if ($override.day -eq (Get-Date).ToString('yyyy-MM-dd') -and
            [int]$override.target_hour -ge 0 -and [int]$override.target_hour -le 23) {
            $TargetHour = [int]$override.target_hour
        }
    } catch { }
}

function Test-LocalAppReady {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $client.Connect('127.0.0.1', 8501)
        $client.Dispose()
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-LocalAppReady)) {
    if (-not (Test-Path -LiteralPath $python)) { throw 'Streamlit için Python ortamı bulunamadı.' }
    Start-Process -FilePath $python -ArgumentList '-m streamlit run app.py --server.port 8501 --server.headless true' `
        -WorkingDirectory $base -WindowStyle Hidden
    $appDeadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $appDeadline -and -not (Test-LocalAppReady)) {
        Start-Sleep -Seconds 3
    }
    if (-not (Test-LocalAppReady)) { throw 'Streamlit 8501 kapısında zamanında hazır olmadı.' }
}

# Zamanlayıcı 19:55'te uyanır; Streamlit'in 20:00 kapanış kapısını gerçekten
# görmesi için görünmez ekran oturumu tam 20:00'de açılır.
$openAt = (Get-Date).Date.AddHours($TargetHour)
if ((Get-Date) -lt $openAt) {
    # 19 Ağu 2026 — PowerShell argüman modunda [math]::Ceiling(...) METİN sayılır ve
    # "Seconds" parametresine bağlanamaz; $ErrorActionPreference=Stop ile script tam
    # burada ölüyordu (19:55 tetiklemesi hep 20:00 öncesi → her gün çöktü, Master Scan
    # 20:00'de hiç başlamadı, işi 22:15 watchdog kurtarıyordu). Parantez = ifade modu.
    Start-Sleep -Seconds ([math]::Ceiling(($openAt - (Get-Date)).TotalSeconds))
}

$browserCandidates = @(
    'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe',
    'C:\Program Files\Google\Chrome\Application\chrome.exe'
)
$browser = $browserCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $browser) { throw 'Brave veya Google Chrome bulunamadı.' }

$profile = Join-Path $base 'logs\master_scan_headless_profile'
New-Item -ItemType Directory -Path $profile -Force | Out-Null
$args = @(
    '--no-first-run', '--disable-gpu', '--window-position=-32000,-32000', '--window-size=800,600',
    ('--user-data-dir={0}' -f $profile), $AppUrl
)
$browserProcess = Start-Process -FilePath $browser -ArgumentList $args -PassThru
$today = (Get-Date).ToString('yyyy-MM-dd')
$deadline = (Get-Date).AddMinutes($MaxMinutes)

try {
    do {
        Start-Sleep -Seconds 20
        if (Test-Path -LiteralPath $completionPath) {
            try {
                $record = Get-Content -LiteralPath $completionPath -Raw | ConvertFrom-Json
                # 28 Agu 2026 — BAYAT KAYIT TUZAGI KAPATILDI.
                # Eskiden yalnizca 'gun bugun mu' diye bakiliyordu. Her Master Scan
                # (elle olsun otomatik olsun) bitiste mark_scan_completed cagirip
                # dosyaya BUGUNUN tarihini yaziyor. Gun icinde elle bir tur kosulursa
                # aksam otomasyonu tarayiciyi aciyor, ILK 20 SANIYELIK YOKLAMADA o
                # bayat kaydi goruyor, donguden cikiyor ve tarayiciyi olduruyor —
                # tarama hic baslamiyor, ustelik gorev sonuc kodu 0 donuyor (sessiz).
                # Artik kayit, aksam penceresi ($openAt) BASLADIKTAN SONRA yazilmissa
                # gecerli sayilir. completed_at okunamazsa eski davranisa dusulur.
                $kayitGecerli = $true
                if ($record.completed_at) {
                    try {
                        $kayitZamani = [datetimeoffset]::Parse([string]$record.completed_at)
                        if ($kayitZamani.LocalDateTime -lt $openAt) { $kayitGecerli = $false }
                    } catch { }
                }
                if ($kayitGecerli -and $record.day -eq $today -and
                    $record.status -in @('completed', 'partial')) { break }
            } catch { }
        }
    } while ((Get-Date) -lt $deadline -and -not $browserProcess.HasExited)
} finally {
    if (-not $browserProcess.HasExited) {
        Stop-Process -Id $browserProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
