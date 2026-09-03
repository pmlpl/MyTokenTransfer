<#
.SYNOPSIS
  MyTokenTransfer dev mode (one-click, hot reload)

.DESCRIPTION
  启动开发态：
    - backend  源码 bind-mount + uvicorn --reload
    - frontend Vite dev server (HMR) on http://127.0.0.1:5173
    - 端口：前端 5173 / 后端 8082 / MySQL 3307（与 prod 共用 .env）
  改前端代码保存即热更新；改后端代码 uvicorn 自动重启。

  Sub-commands:
    dev.ps1           # default: up + wait healthy
    dev.ps1 stop      # stop containers
    dev.ps1 restart   # restart
    dev.ps1 down      # stop and remove containers (keeps DB volume)
    dev.ps1 logs      # tail logs (Ctrl+C to exit)
    dev.ps1 status    # container health status
    dev.ps1 shell-backend   # bash into backend container
    dev.ps1 shell-frontend  # sh into frontend container
#>
param(
    [ValidateSet('','up','stop','restart','down','logs','status','shell-backend','shell-frontend')]
    [string]$Cmd = ''
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
Write-Host "`n=== MyTokenTransfer DEV ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot`n" -ForegroundColor Cyan

# ---- compose detection ----
$Compose = $null
try {
    $null = docker compose version 2>$null
    if ($LASTEXITCODE -eq 0) { $Compose = 'docker compose' }
} catch {}
if (-not $Compose) {
    try {
        $null = & docker-compose --version 2>$null
        if ($LASTEXITCODE -eq 0) { $Compose = 'docker-compose' }
    } catch {}
}
if (-not $Compose) {
    Write-Host "[X] Docker Compose not found." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Using: $Compose" -ForegroundColor Green

# ---- ensure .env exists (reuse deploy.ps1 logic by calling it once) ----
if (-not (Test-Path '.env')) {
    Write-Host "[!] .env missing, generating via deploy.ps1 ..." -ForegroundColor Yellow
    & powershell -ExecutionPolicy Bypass -File .\deploy.ps1 deploy | Out-Null
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-Compose([string[]]$argsList) {
    $fullArgs = @('-f','docker-compose.yml','-f','docker-compose.dev.yml') + $argsList
    Write-Host "`n>> $Compose $($fullArgs -join ' ')" -ForegroundColor DarkGray
    if ($Compose -eq 'docker compose') {
        & docker compose @fullArgs
    } else {
        & docker-compose @fullArgs
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] exit=$LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# ---- dispatch ----
if ($Cmd -eq '' -or $Cmd -eq 'up') {
    Write-Host "==> [1/2] Building dev images (deps only) ..." -ForegroundColor Cyan
    Invoke-Compose @('build', 'mysql')  # backend/frontend 已在 dev overlay 用 image/bind-mount，无需 build
    Write-Host "`n==> [2/2] Starting (detached) and waiting for healthy ..." -ForegroundColor Cyan
    Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', '300')

    Write-Host "`n==> Service status:" -ForegroundColor Cyan
    Invoke-Compose @('ps')

    $port = (Select-String -Path '.env' -Pattern '^WEB_PORT=' | ForEach-Object { ($_.Line -split '=',2)[1].Trim() }) | Select-Object -First 1
    if (-not $port) { $port = '8080' }
    Write-Host "`n================================================" -ForegroundColor Green
    Write-Host "  DEV READY" -ForegroundColor Green
    Write-Host "  Frontend (Vite HMR): http://127.0.0.1:5173" -ForegroundColor Green
    Write-Host "  Backend  (reload)  : http://127.0.0.1:8082  (admin API: /api/...)" -ForegroundColor Green
    Write-Host "  Logs               : powershell -File dev.ps1 logs" -ForegroundColor Green
    Write-Host "  Stop               : powershell -File dev.ps1 stop" -ForegroundColor Green
    Write-Host "  (prod 仍在 $port, 互不影响)" -ForegroundColor DarkGray
    Write-Host "================================================" -ForegroundColor Green
}
elseif ($Cmd -eq 'stop')    { Invoke-Compose @('stop') }
elseif ($Cmd -eq 'restart') { Invoke-Compose @('restart') }
elseif ($Cmd -eq 'down')    { Invoke-Compose @('down') }
elseif ($Cmd -eq 'logs')    { Invoke-Compose @('logs', '-f', '--tail=200') }
elseif ($Cmd -eq 'status')  { Invoke-Compose @('ps') }
elseif ($Cmd -eq 'shell-backend') {
    if ($Compose -eq 'docker compose') { & docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend sh }
    else { & docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec backend sh }
}
elseif ($Cmd -eq 'shell-frontend') {
    if ($Compose -eq 'docker compose') { & docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend sh }
    else { & docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend sh }
}
