# Run the Immowelt scrape from this PC and write into the VPS Postgres.
#
# Immowelt's DataDome blocks the VPS's datacenter IP (it serves stripped pages
# then 403s), so the scraper runs from a residential connection instead. This
# opens an SSH tunnel to the VPS Postgres, runs `realestate-scrape run`, and
# closes the tunnel. Register it with register_scrape_task.ps1, or run by hand.

[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot '.scrape_local.env'),
    [int]$LocalPort = 15432
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$logFile = Join-Path $PSScriptRoot 'scrape_local.log'

function Log($msg) {
    $line = ('{0}  {1}' -f (Get-Date -Format 'u'), $msg)
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

if (-not (Test-Path $EnvFile)) {
    Log "missing $EnvFile  (copy .scrape_local.env.example and fill it in)"
    exit 1
}

$cfg = @{}
foreach ($raw in Get-Content $EnvFile) {
    if ($raw -match '^\s*#' -or $raw -notmatch '=') { continue }
    $k, $v = $raw -split '=', 2
    $cfg[$k.Trim()] = $v.Trim()
}
foreach ($required in 'VPS_SSH', 'RE_DB_PASSWORD') {
    if (-not $cfg[$required]) { Log "$required is required in $EnvFile"; exit 1 }
}

Log "opening tunnel 127.0.0.1:$LocalPort -> $($cfg['VPS_SSH']):5432"
$ssh = Start-Process ssh -PassThru -WindowStyle Hidden -ArgumentList @(
    '-N',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30',
    '-L', "${LocalPort}:127.0.0.1:5432",
    $cfg['VPS_SSH']
)

try {
    $up = $false
    foreach ($attempt in 1..20) {
        Start-Sleep -Milliseconds 500
        try {
            $probe = [Net.Sockets.TcpClient]::new('127.0.0.1', $LocalPort)
            $probe.Close(); $up = $true; break
        } catch { }
    }
    if (-not $up) { throw "tunnel did not come up on port $LocalPort" }
    Log 'tunnel up'

    $env:RE_DATABASE_URL = "postgresql+psycopg://realestate:$($cfg['RE_DB_PASSWORD'])@127.0.0.1:$LocalPort/realestate"
    foreach ($k in 'RE_SMTP_HOST', 'RE_SMTP_PORT', 'RE_SMTP_USER', 'RE_SMTP_PASSWORD', 'RE_EMAIL_TO',
        'RE_SCRAPE_CITIES', 'RE_SCRAPE_MAX_SEARCH_URLS_PER_CITY') {
        if ($cfg.ContainsKey($k)) { Set-Item "env:$k" $cfg[$k] }
    }

    Log 'scraping'
    & uv run --project $repo realestate-scrape run --email 2>&1 | ForEach-Object { Log $_ }
    Log "scrape finished (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}
finally {
    if ($ssh -and -not $ssh.HasExited) { Stop-Process -Id $ssh.Id -Force; Log 'tunnel closed' }
    $env:RE_DATABASE_URL = $null
    $env:RE_SMTP_PASSWORD = $null
}
