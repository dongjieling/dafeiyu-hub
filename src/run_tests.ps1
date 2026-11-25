# Hub test runner (ASCII only; stderr must not be treated as failure)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sidecar = Join-Path $here 'sidecar'

$python = 'C:\Users\41328\AppData\Local\AstrBot\backend\python\python.exe'
if (-not (Test-Path $python)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) { Write-Host 'python not found' -ForegroundColor Red; exit 2 }
    $python = $found.Source
}

Push-Location $sidecar
try {
    # cmd merges stderr into stdout; python's unittest writes to stderr
    cmd /c "`"$python`" -X utf8 -m unittest discover -s tests -v 2>&1"
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ''
if ($code -eq 0) {
    Write-Host 'ALL TESTS PASSED' -ForegroundColor Green
} else {
    Write-Host "TESTS FAILED (exit $code)" -ForegroundColor Red
}
exit $code
