# Run once (as your normal Windows user) to register the recurring local scrape.
# Re-run to update it. Unregister with:
#   Unregister-ScheduledTask -TaskName 'GermanyRealEstate-Scrape' -Confirm:$false

$ErrorActionPreference = 'Stop'
$script = Join-Path (Split-Path -Parent $PSCommandPath) 'scrape_local.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""

# Mon + Thu; "start when available" so it catches up whenever the PC is next on
# (per the same preference as the turkey-food-inflation local scraper).
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Thursday -At 4am

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -DontStopOnIdleEnd -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName 'GermanyRealEstate-Scrape' -Force `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Scrape Immowelt -> VPS Postgres over an SSH tunnel'

Write-Host "registered. Run now with:  Start-ScheduledTask -TaskName 'GermanyRealEstate-Scrape'"
