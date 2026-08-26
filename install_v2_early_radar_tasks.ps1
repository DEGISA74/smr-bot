$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"

$tasks = @(
    @{
        Name = "SMR_V2_Erken_Radar_Sonuclar"
        Time = "17:40"
        Script = Join-Path $projectRoot "run_v2_early_radar_results_hidden.vbs"
    },
    @{
        Name = "SMR_V2_Erken_Radar_Adaylar"
        Time = "17:45"
        Script = Join-Path $projectRoot "run_v2_early_radar_candidates_hidden.vbs"
    }
)

foreach ($task in $tasks) {
    $action = New-ScheduledTaskAction -Execute $wscript -Argument ('"{0}"' -f $task.Script)
    $trigger = New-ScheduledTaskTrigger `
        -Weekly `
        -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
        -At $task.Time
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $task.Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "V2 Erken Radar - resmî V2'den bağımsız" `
        -Force | Out-Null
}

Write-Host "V2 Erken Radar görevleri kuruldu:"
Write-Host "- Hafta içi 17:40 sonuç karnesi"
Write-Host "- Hafta içi 17:45 T+1 adayları"
