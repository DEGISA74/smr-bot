# Master Scan gece bekçisi: her işlem günü 22:15'te yedek + eksik getiri onarımı + karne.
# Tarama üretmez; mevcut patron.db verisini silmez veya değiştirmez.
$ErrorActionPreference = 'Stop'
$base = 'C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal'
$python = Join-Path $base '.venv\Scripts\python.exe'
$script = Join-Path $base 'master_scan_watchdog.py'
$hiddenLauncher = Join-Path $base 'run_hidden.vbs'
$wscript = Join-Path $env:WINDIR 'System32\wscript.exe'
$taskName = 'SMR_MasterScan_Watchdog'
$action = New-ScheduledTaskAction -Execute $wscript -Argument ('"{0}" "{1}" "{2}" "--max-batches" "30"' -f $hiddenLauncher, $python, $script) -WorkingDirectory $base
$trigger = New-ScheduledTaskTrigger -Daily -At 22:15
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description 'Master Scan DB yedeği, eksik getiri onarımı ve günlük karne' -Force | Out-Null
Write-Output "OK: $taskName 22:15'te kuruldu"
