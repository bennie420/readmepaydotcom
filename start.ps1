<#
.SYNOPSIS
    PowerShell Startup Script for Open-Source README Sponsorship Platform.
.DESCRIPTION
    Automates virtual environment activation, preflight validation,
    and runs start.py with forwarded command-line arguments.
.PARAMETER HostName
    Bind host interface (default: 0.0.0.0 or from .env).
.PARAMETER Port
    Bind port number (default: 8080 or from .env).
.PARAMETER Reload
    Enable auto-reload on code modifications.
.PARAMETER Seed
    Force execution of database seeders.
.PARAMETER NoSeed
    Skip automated seeding checks.
.PARAMETER ResetAds
    Reset existing sponsor ad budgets to original amounts.
.PARAMETER CheckOnly
    Run preflight verification only without starting server.
.PARAMETER LogLevel
    Uvicorn logging verbosity (default: info).
.EXAMPLE
    .\start.ps1
    .\start.ps1 -Port 9000 -Reload
    .\start.ps1 -Seed -CheckOnly
#>

[CmdletBinding()]
param(
    [string]$HostName,
    [int]$Port,
    [switch]$Reload,
    [switch]$Seed,
    [switch]$NoSeed,
    [switch]$ResetAds,
    [switch]$CheckOnly,
    [string]$LogLevel = "info",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

# Determine script root directory and enter it
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ScriptDir) {
    $ScriptDir = (Get-Location).Path
}
Set-Location $ScriptDir

Write-Host ">>> Initializing Open-Source Sponsorship Platform (PowerShell)..." -ForegroundColor Cyan

# 1. Discover and activate virtual environment if present
if (-not $env:VIRTUAL_ENV) {
    if (Test-Path "$ScriptDir\.venv\Scripts\Activate.ps1") {
        Write-Host ">>> Activating virtual environment from .venv..." -ForegroundColor DarkGray
        . "$ScriptDir\.venv\Scripts\Activate.ps1"
    } elseif (Test-Path "$ScriptDir\venv\Scripts\Activate.ps1") {
        Write-Host ">>> Activating virtual environment from venv..." -ForegroundColor DarkGray
        . "$ScriptDir\venv\Scripts\Activate.ps1"
    }
}

# 2. Locate Python executable
$pythonCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
} else {
    Write-Host "[ERROR] Python executable not found in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.12+ from https://www.python.org/" -ForegroundColor Yellow
    exit 1
}

# 3. Assemble arguments for start.py
$pyArgs = @()

if ($PSBoundParameters.ContainsKey('HostName') -and $HostName) {
    $pyArgs += "--host", $HostName
}
if ($PSBoundParameters.ContainsKey('Port') -and $Port) {
    $pyArgs += "--port", $Port
}
if ($Reload) {
    $pyArgs += "--reload"
}
if ($Seed) {
    $pyArgs += "--seed"
}
if ($NoSeed) {
    $pyArgs += "--no-seed"
}
if ($ResetAds) {
    $pyArgs += "--reset-ads"
}
if ($CheckOnly) {
    $pyArgs += "--check-only"
}
if ($PSBoundParameters.ContainsKey('LogLevel') -and $LogLevel) {
    $pyArgs += "--log-level", $LogLevel
}
if ($RemainingArgs) {
    $pyArgs += $RemainingArgs
}

# 4. Invoke start.py
try {
    & $pythonCmd "$ScriptDir\start.py" @pyArgs
    exit $LASTEXITCODE
} catch {
    Write-Host "[ERROR] Execution failed: $_" -ForegroundColor Red
    exit 1
}
