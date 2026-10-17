param(
    [ValidateSet('login', 'browser-login', 'check', 'export', 'logout')][string]$Command = 'check',
    [ValidateSet('chrome', 'msedge')][string]$Browser = 'chrome',
    [string]$Resource = 'transactions',
    [string]$Since,
    [string]$OutputFile,
    [string]$DatasetId
)
$ErrorActionPreference = 'Stop'
$clientPython = if ($env:SIMPLIFI_PYTHON) { $env:SIMPLIFI_PYTHON } else { Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $clientPython)) {
    throw 'Python environment missing. Run setup.ps1 first, or set SIMPLIFI_PYTHON to an installed environment.'
}
$clientArgs = @('-m', 'simplifi.local_cli', $Command)
if ($Command -eq 'browser-login') { $clientArgs += @('--browser', $Browser) }
if ($Command -eq 'export') {
    $clientArgs += @('--resource', $Resource)
    if (-not $OutputFile) { throw 'Export requires -OutputFile' }
    $clientArgs += @('--output', $OutputFile)
}
if ($Since) { $clientArgs += @('--since', $Since) }
if ($DatasetId) { $clientArgs += @('--dataset-id', $DatasetId) }
& $clientPython @clientArgs
exit $LASTEXITCODE
