[CmdletBinding()]
param(
  [string]$DatabaseName = 'cadensy_pr06b4_e2e_local_test',
  [string]$DatabaseHost = '127.0.0.1',
  [int]$DatabasePort = 5432,
  [string]$DatabaseUser = 'postgres',
  [string]$DatabasePassword = 'postgres'
)

$ErrorActionPreference = 'Stop'
function Stop-ChildProcessTree([System.Diagnostics.Process]$Process) {
  if ($null -ne $Process -and -not $Process.HasExited) {
    & taskkill.exe /PID $Process.Id /T /F | Out-Null
  }
}
if ($DatabaseHost -notin @('localhost', '127.0.0.1', '::1') -or $DatabaseName -notmatch '^cadensy_pr06b4_e2e_[a-z0-9_]+_test$') {
  throw 'Refusing database setup: host must be local and name must be an explicit cadensy_pr06b4_e2e_*_test database.'
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backend = Join-Path $repo 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$fixture = Join-Path $backend 'test-results-pr06b4-e2e-fixture.json'
$control = Join-Path $backend 'test-results-pr06b4-e2e-control'
$env:PGPASSWORD = $DatabasePassword
$admin = "postgresql://$DatabaseUser@$DatabaseHost`:$DatabasePort/postgres"
$env:DATABASE_URL = "postgresql+psycopg://$DatabaseUser`:$DatabasePassword@$DatabaseHost`:$DatabasePort/$DatabaseName"
$env:APP_ENV = 'test'
$env:DISABLE_SCHEDULER = '1'
$env:DEV_ALLOW_MEMBERSHIP_HEADER = '0'
$env:MOCK_AI = '1'
$env:CADENSY_E2E_FAKE_RUNTIME = '1'
$env:CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED = '1'
$env:CADENSY_E2E_RUNTIME_CONTROL_DIR = $control
$env:CADENSY_E2E_FRONTEND_URL = 'http://localhost:4499'
$env:CORS_ORIGINS = $env:CADENSY_E2E_FRONTEND_URL

if (-not (Test-NetConnection $DatabaseHost -Port $DatabasePort -InformationLevel Quiet -WarningAction SilentlyContinue)) {
  throw "Local PostgreSQL is not reachable at $DatabaseHost`:$DatabasePort; no database was changed."
}
& $python -c "import psycopg; c=psycopg.connect('$admin', autocommit=True, connect_timeout=5); c.execute('DROP DATABASE IF EXISTS $DatabaseName WITH (FORCE)'); c.execute('CREATE DATABASE $DatabaseName'); c.close()"
if ($LASTEXITCODE -ne 0) { throw 'Disposable database creation failed.' }
Push-Location $backend
try {
  & $python -m alembic upgrade head
  if ($LASTEXITCODE -ne 0) { throw 'Alembic upgrade failed.' }
  & $python scripts/pr06b4_e2e_fixture.py seed $fixture
  if ($LASTEXITCODE -ne 0) { throw 'E2E fixture seed failed.' }
  Push-Location (Join-Path $repo 'frontend')
  try {
    & npm.cmd run build:trip-preview
    if ($LASTEXITCODE -ne 0) { throw 'Trip preview build failed.' }
  } finally { Pop-Location }
  Remove-Item -LiteralPath $control -Recurse -Force -ErrorAction SilentlyContinue
  Start-Process -FilePath $python -ArgumentList '-m','uvicorn','app.api.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $backend -WindowStyle Hidden -PassThru | Set-Variable backendProcess
  Push-Location (Join-Path $repo 'frontend')
  try {
    Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev','--','--host=localhost','--port=4499','--strictPort' -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru | Set-Variable frontendProcess
    & node tests/pr06b4-local-browser-e2e.mjs $fixture
    if ($LASTEXITCODE -ne 0) { throw 'Browser E2E run failed.' }
  } finally {
    Stop-ChildProcessTree $frontendProcess
    Pop-Location
  }
} finally {
  Stop-ChildProcessTree $backendProcess
  # The runtime-off check is a separate server process so the browser sees the
  # actual capability declaration from the backend rather than a client stub.
  $env:CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED = '0'
  Start-Process -FilePath $python -ArgumentList '-m','uvicorn','app.api.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $backend -WindowStyle Hidden -PassThru | Set-Variable runtimeOffBackend
  Push-Location (Join-Path $repo 'frontend')
  try {
    Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev','--','--host=localhost','--port=4499','--strictPort' -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru | Set-Variable runtimeOffFrontend
    & node tests/pr06b4-local-browser-e2e.mjs $fixture --runtime-off
    if ($LASTEXITCODE -ne 0) { throw 'Runtime-off browser E2E run failed.' }
  } finally {
    Stop-ChildProcessTree $runtimeOffFrontend
    Stop-ChildProcessTree $runtimeOffBackend
    Pop-Location
  }
  Pop-Location
}
