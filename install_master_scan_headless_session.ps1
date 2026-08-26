# PC açıkken, kullanıcı ekranı kapalı olsa da 20:00 otomatik Master Scan başlatıcısını kurar.
$ErrorActionPreference = 'Stop'
$base = 'C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal'
$script = Join-Path $base 'master_scan_headless_session.ps1'
$hiddenLauncher = Join-Path $base 'run_hidden.vbs'
$wscript = Join-Path $env:WINDIR 'System32\wscript.exe'
$taskName = 'SMR_MasterScan_AutoStart'
$action = New-ScheduledTaskAction -Execute $wscript -Argument ('"{0}" "powershell.exe" "-NoProfile" "-ExecutionPolicy" "Bypass" "-File" "{1}"' -f $hiddenLauncher, $script) -WorkingDirectory $base
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 19:55
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 190) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description '20:00 BIST veri kapısı ve görünmez Master Scan ekran oturumu' -Force | Out-Null
Write-Output "OK: $taskName işlem günleri 19:55'te kuruldu"
