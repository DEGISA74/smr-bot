# patron.db haftalık yedek — Windows Task Scheduler için
# Schedule: her Pazar 21:00 (SMR_DB_Weekly_Backup)
# Yedek konumu: backups/ klasörü, 84 gün (12 hafta) sonra eski yedekler silinir

$ErrorActionPreference = 'Stop'
$base = 'C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal'
$bdir = Join-Path $base 'backups'
if (-not (Test-Path $bdir)) { New-Item -ItemType Directory -Path $bdir | Out-Null }

$stamp = Get-Date -Format 'yyyy-MM-dd'
$src   = Join-Path $base 'patron.db'
$dst   = Join-Path $bdir ("patron_$stamp.db")

Copy-Item -Path $src -Destination $dst -Force
$size_mb = [math]::Round((Get-Item $dst).Length / 1MB, 2)
$logline = "[{0}] {1} ({2} MB)" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $dst, $size_mb
Add-Content -Path (Join-Path $bdir 'backup_history.log') -Value $logline

# 84 gün üstü eski yedekleri sil
Get-ChildItem -Path $bdir -Filter 'patron_*.db' |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-84) } |
    Remove-Item -Force

Write-Output "OK: $dst ($size_mb MB)"
