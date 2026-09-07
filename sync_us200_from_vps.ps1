param(
    [string]$Vps = "wm11tr@34.153.19.220",
    [string]$ProjectRoot = $PSScriptRoot
)

$ErrorActionPreference = "Stop"
$dataRoot = Join-Path $ProjectRoot "us200_data"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$incoming = Join-Path $dataRoot "_incoming_$stamp"
$remoteBase = "${Vps}:~/smr/us200_data"
$folders = @("daily", "hourly", "four_hour", "manifests")

New-Item -ItemType Directory -Path $incoming -Force | Out-Null

try {
    foreach ($folder in $folders) {
        & scp -r "$remoteBase/$folder" $incoming
        if ($LASTEXITCODE -ne 0) {
            throw "VPS'ten $folder klasoru alinamadi."
        }
    }

    $newDaily = @(Get-ChildItem (Join-Path $incoming "daily") -Filter "*_1d.parquet").Count
    $oldDailyPath = Join-Path $dataRoot "daily"
    $oldDaily = if (Test-Path $oldDailyPath) { @(Get-ChildItem $oldDailyPath -Filter "*_1d.parquet").Count } else { 0 }
    if ($newDaily -lt $oldDaily) {
        throw "VPS paketi daha eksik gorunuyor ($newDaily < $oldDaily gunluk dosya); yerel ayna degistirilmedi."
    }
    if (-not (Test-Path (Join-Path $incoming "daily\^GSPC_1d.parquet"))) {
        throw "S&P 500 referans dosyasi yok; yerel ayna degistirilmedi."
    }

    foreach ($folder in $folders) {
        $target = Join-Path $dataRoot $folder
        $backup = Join-Path $dataRoot "${folder}_onceki_$stamp"
        if (Test-Path $target) {
            Move-Item -LiteralPath $target -Destination $backup
        }
        try {
            Move-Item -LiteralPath (Join-Path $incoming $folder) -Destination $target
        }
        catch {
            if (Test-Path $backup) { Move-Item -LiteralPath $backup -Destination $target }
            throw
        }
    }

    Remove-Item -LiteralPath $incoming -Force
    Write-Host "S&P 200 aynasi guncellendi: $newDaily gunluk dosya. Eski paketler geri donus icin korundu."
}
catch {
    if (Test-Path $incoming) {
        Rename-Item -LiteralPath $incoming -NewName "_failed_$stamp"
    }
    throw
}
