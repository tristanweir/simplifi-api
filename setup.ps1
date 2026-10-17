param([string]$Python = 'python', [switch]$Dev)
$ErrorActionPreference = 'Stop'
$clientEnvironment = Join-Path $PSScriptRoot '.venv'
& $Python -m venv $clientEnvironment
if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Install Python 3.10+ or pass -Python with its executable path.' }
$clientPython = Join-Path $clientEnvironment 'Scripts/python.exe'
$clientExtras = if ($Dev) { '.[browser,dev]' } else { '.[browser]' }
Push-Location $PSScriptRoot
try {
    & $clientPython -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & $clientPython -m pip install --no-deps -e $clientExtras
    if ($LASTEXITCODE -ne 0) { throw 'Client installation failed.' }
} finally { Pop-Location }
Write-Host 'Ready. Run browser-login.cmd to sign in on this computer.'
