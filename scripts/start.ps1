$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
& "$repoRoot\.venv\Scripts\python.exe" -m appmon daemon
exit $LASTEXITCODE
