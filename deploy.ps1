<#
.SYNOPSIS
  MyTokenTransfer one-click deploy script (Windows PowerShell)

.DESCRIPTION
  Sub-commands:
    deploy.ps1           # default: first deploy / upgrade (build + up + wait healthy)
    deploy.ps1 start     # start existing containers
    deploy.ps1 stop      # stop containers
    deploy.ps1 restart   # restart containers
    deploy.ps1 down      # stop and remove containers (keeps DB volume)
    deploy.ps1 logs      # tail logs (Ctrl+C to exit)
    deploy.ps1 status    # show container health status
    deploy.ps1 migrate   # run alembic upgrade manually (usually auto-run on start)
#>
param(
    [ValidateSet('','deploy','start','stop','restart','down','logs','status','migrate')]
    [string]$Cmd = ''
)

$ErrorActionPreference = 'Stop'

# ---- switch to project root ----
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
Write-Host "`n=== MyTokenTransfer Docker Deploy ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot`n" -ForegroundColor Cyan

# ---- docker compose detection ----
function Test-DockerCompose {
    try {
        $null = docker compose version 2>$null
        if ($LASTEXITCODE -eq 0) { return 'docker compose' }
    } catch {}
    try {
        $null = & docker-compose --version 2>$null
        if ($LASTEXITCODE -eq 0) { return 'docker-compose' }
    } catch {}
    Write-Host "[X] Docker Compose not found. Please install & start Docker Desktop first." -ForegroundColor Red
    Write-Host "    Download: https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
    exit 1
}
$Compose = Test-DockerCompose
Write-Host "[OK] Using compose command: $Compose" -ForegroundColor Green

# ---- helpers ----
function New-RandomHex([int]$len = 32) {
    $bytes = New-Object byte[] $len
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
}

function Ensure-EnvFile {
    $envPath = Join-Path $ProjectRoot '.env'
    $examplePath = Join-Path $ProjectRoot '.env.example'
    if (Test-Path $envPath) {
        # 既存 .env：拒绝占位值，否则会用公开示例值起服务（严重安全风险 + 历史密文全解不开）
        $cur = Get-Content $envPath | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' }
        $bad = @()
        foreach ($line in $cur) {
            if ($line -match '^(MASTER_PASSWORD|SECRET_KEY|GATEWAY_TOKEN)\s*=\s*(.+)$') {
                $val = $Matches[2].Trim().Trim('"').Trim("'")
                if ($val -match '^change-me' -or $val -match 'please-change' -or $val.Length -lt 16) {
                    $bad += $Matches[1]
                }
            }
        }
        if ($bad.Count -gt 0) {
            Write-Host "" -ForegroundColor Red
            Write-Host "==============================================================" -ForegroundColor Red
            Write-Host "  [X] .env 包含占位/弱凭据，拒绝继续：" -ForegroundColor Red
            Write-Host "      $($bad -join ', ')" -ForegroundColor Red
            Write-Host "      请编辑 $envPath，替换为强随机值后重试。" -ForegroundColor Red
            Write-Host "      生成示例：" -ForegroundColor Red
            Write-Host "        MASTER_PASSWORD  : MTT- + 24 hex" -ForegroundColor Red
            Write-Host "        SECRET_KEY       : 96 hex (openssl rand -hex 48)" -ForegroundColor Red
            Write-Host "        GATEWAY_TOKEN    : mtt-gw- + 48 hex" -ForegroundColor Red
            Write-Host "==============================================================" -ForegroundColor Red
            Write-Host "" -ForegroundColor Red
            exit 1
        }
        Write-Host "[OK] .env already exists, skip generation (delete .env to force reset)." -ForegroundColor Green
        return $envPath
    }
    if (-not (Test-Path $examplePath)) {
        Write-Host "[X] Missing .env.example in project root." -ForegroundColor Red
        exit 1
    }
    Copy-Item $examplePath $envPath -Force

    $masterPwd = 'MTT-' + (New-RandomHex 12)
    $secretKey = New-RandomHex 48
    $gatewayTk = 'mtt-gw-' + (New-RandomHex 24)
    $redisPwd  = New-RandomHex 32

    $content = Get-Content $envPath -Raw -Encoding UTF8
    $content = $content -replace 'MASTER_PASSWORD=change-me-to-a-strong-password',   "MASTER_PASSWORD=$masterPwd"
    $content = $content -replace 'SECRET_KEY=change-me-to-a-long-random-secret.*$', "SECRET_KEY=$secretKey"
    $content = $content -replace 'GATEWAY_TOKEN=change-me-gateway-token',           "GATEWAY_TOKEN=$gatewayTk"
    $content = $content -replace 'REDIS_PASSWORD=change-me-redis-password',         "REDIS_PASSWORD=$redisPwd"
    $content = $content -replace 'redis://:change-me-redis-password@redis:6379/0',  "redis://:$redisPwd@redis:6379/0"
    Set-Content $envPath -Value $content -Encoding UTF8 -NoNewline

    Write-Host "" -ForegroundColor Yellow
    Write-Host "==============================================================" -ForegroundColor Yellow
    Write-Host "  Generated .env (FIRST deploy). SAVE THESE CREDENTIALS:" -ForegroundColor Yellow
    Write-Host "    Admin login password  (MASTER_PASSWORD): $masterPwd" -ForegroundColor Yellow
    Write-Host "    Gateway token         (GATEWAY_TOKEN)  : $gatewayTk" -ForegroundColor Yellow
    Write-Host "    Signing key           (SECRET_KEY)     : written to .env (96 hex chars)" -ForegroundColor Yellow
    Write-Host "    Redis password        (REDIS_PASSWORD) : $redisPwd" -ForegroundColor Yellow
    Write-Host "    To customize, edit $envPath then re-run this script." -ForegroundColor Yellow
    Write-Host "==============================================================" -ForegroundColor Yellow
    Write-Host "" -ForegroundColor Yellow
    return $envPath
}

function Get-EnvValue([string]$key, [string]$default = '') {
    $envPath = Join-Path $ProjectRoot '.env'
    if (-not (Test-Path $envPath)) { return $default }
    foreach ($line in Get-Content $envPath) {
        if ($line -match "^\s*$key\s*=\s*(.*)$") {
            $v = $Matches[1].Trim().Trim('"').Trim("'")
            if ($v -ne '') { return $v }
        }
    }
    return $default
}

function Invoke-Compose([string[]]$argsList) {
    Write-Host "`n>> $Compose $($argsList -join ' ')" -ForegroundColor DarkGray
    if ($Compose -eq 'docker compose') {
        & docker compose @argsList
    } else {
        & docker-compose @argsList
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] Command failed with exit code $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# ---- sub-command dispatch ----
if ($Cmd -eq '' -or $Cmd -eq 'deploy') {
    Ensure-EnvFile | Out-Null
    Write-Host "==> [1/3] Building images ..." -ForegroundColor Cyan
    Invoke-Compose @('build', '--pull')

    Write-Host "`n==> [2/3] Starting containers (detached) and waiting for healthy ..." -ForegroundColor Cyan
    Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', '300')

    Write-Host "`n==> [3/3] Service status ..." -ForegroundColor Cyan
    Invoke-Compose @('ps')

    $port = Get-EnvValue 'WEB_PORT' '8080'
    Write-Host "`n================================================" -ForegroundColor Green
    Write-Host "  DEPLOY COMPLETE" -ForegroundColor Green
    Write-Host "  Admin UI   : http://127.0.0.1:$port"
    Write-Host "  Gateway    : http://127.0.0.1:$port/v1/"
    Write-Host "  Logs       : powershell -File deploy.ps1 logs"
    Write-Host "  Stop       : powershell -File deploy.ps1 stop"
    Write-Host "================================================" -ForegroundColor Green
}
elseif ($Cmd -eq 'start')   { Ensure-EnvFile | Out-Null; Invoke-Compose @('start') }
elseif ($Cmd -eq 'stop')    { Invoke-Compose @('stop') }
elseif ($Cmd -eq 'restart') { Invoke-Compose @('restart') }
elseif ($Cmd -eq 'down') {
    Write-Host "[!] Removing containers & network. MySQL volume (mysql_data) is KEPT. Continue? (Y/N)" -ForegroundColor Yellow
    $ans = Read-Host
    if ($ans -match '^[Yy]') { Invoke-Compose @('down') }
}
elseif ($Cmd -eq 'logs')    { Invoke-Compose @('logs', '-f', '--tail=200') }
elseif ($Cmd -eq 'status')  { Invoke-Compose @('ps') }
elseif ($Cmd -eq 'migrate') {
    Ensure-EnvFile | Out-Null
    Invoke-Compose @('exec', 'backend', 'sh', '-c', 'alembic upgrade head && echo OK')
}