[CmdletBinding()]
param(
  [string]$DatabaseName = '',
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

$runId = [guid]::NewGuid().ToString('N').Substring(0, 12)
if ([string]::IsNullOrWhiteSpace($DatabaseName)) {
  $DatabaseName = "cadensy_pr06b4_e2e_${runId}_test"
}
if ($DatabaseHost -notin @('localhost', '127.0.0.1', '::1') -or $DatabaseName -notmatch '^cadensy_pr06b4_e2e_[a-z0-9_]+_test$') {
  throw 'Refusing database setup: host must be local and name must be an explicit cadensy_pr06b4_e2e_*_test database.'
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backend = Join-Path $repo 'backend'
$frontend = Join-Path $repo 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$runRoot = Join-Path $temporaryRoot "cadensy-pr06b4-$runId"
New-Item -ItemType Directory -Path $runRoot -ErrorAction Stop | Out-Null
$runRoot = (Resolve-Path -LiteralPath $runRoot).Path
if (-not $runRoot.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw 'Refusing E2E setup outside the system temporary directory.'
}
$fixture = Join-Path $runRoot 'fixture.json'
$control = Join-Path $runRoot 'runtime-control'
$admin = "postgresql://$DatabaseUser@$DatabaseHost`:$DatabasePort/postgres"

$databaseCreated = $false
$backendProcess = $null
$frontendProcess = $null
$runtimeOffBackend = $null
$runtimeOffFrontend = $null

$managedEnvironmentNames = @(
  'PGPASSWORD', 'CADENSY_E2E_DATABASE_NAME', 'CADENSY_E2E_ADMIN_URL', 'DATABASE_URL',
  'APP_ENV', 'DISABLE_SCHEDULER', 'DEV_ALLOW_MEMBERSHIP_HEADER', 'MOCK_AI',
  'CADENSY_E2E_FAKE_RUNTIME', 'CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED',
  'CADENSY_E2E_RUNTIME_CONTROL_DIR', 'CADENSY_E2E_FRONTEND_URL', 'CORS_ORIGINS',
  'CADENSY_B2C_HMAC_KEY', 'CADENSY_B2C_PROTECTED_BOOKING_WRITES'
)
$previousEnvironment = @{}
foreach ($name in $managedEnvironmentNames) {
  $existing = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
  if ($null -ne $existing) {
    $previousEnvironment[$name] = $existing.Value
  }
}

$env:PGPASSWORD = $DatabasePassword
$env:CADENSY_E2E_DATABASE_NAME = $DatabaseName
$env:CADENSY_E2E_ADMIN_URL = $admin
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
$env:CADENSY_B2C_HMAC_KEY = 'cadensy-pr06b4-e2e-local-only'
$env:CADENSY_B2C_PROTECTED_BOOKING_WRITES = '1'

$createDatabase = @'
import os
import psycopg
from psycopg import sql

name = os.environ["CADENSY_E2E_DATABASE_NAME"]
with psycopg.connect(os.environ["CADENSY_E2E_ADMIN_URL"], autocommit=True, connect_timeout=5) as connection:
    exists = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
    if exists:
        raise SystemExit("Disposable database already exists; refusing to overwrite it.")
    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
'@
$dropDatabase = @'
import os
import psycopg
from psycopg import sql

name = os.environ["CADENSY_E2E_DATABASE_NAME"]
with psycopg.connect(os.environ["CADENSY_E2E_ADMIN_URL"], autocommit=True, connect_timeout=5) as connection:
    connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
'@

try {
  if (-not (Test-NetConnection $DatabaseHost -Port $DatabasePort -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    throw "Local PostgreSQL is not reachable at $DatabaseHost`:$DatabasePort; no database was changed."
  }
  & $python -c $createDatabase
  if ($LASTEXITCODE -ne 0) { throw 'Disposable database creation failed.' }
  $databaseCreated = $true

  Push-Location $backend
  try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Alembic upgrade failed.' }
    & $python scripts/pr06b4_e2e_fixture.py seed $fixture
    if ($LASTEXITCODE -ne 0) { throw 'E2E fixture seed failed.' }
  } finally {
    Pop-Location
  }

  Push-Location $frontend
  try {
    & npm.cmd run build:trip-preview
    if ($LASTEXITCODE -ne 0) { throw 'Trip preview build failed.' }
  } finally {
    Pop-Location
  }

  $backendProcess = Start-Process -FilePath $python -ArgumentList '-m','uvicorn','app.api.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $backend -WindowStyle Hidden -PassThru
  try {
    $frontendProcess = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev','--','--host=localhost','--port=4499','--strictPort' -WorkingDirectory $frontend -WindowStyle Hidden -PassThru
    Push-Location $frontend
    try {
      & node tests/pr06b4-local-browser-e2e.mjs $fixture
      if ($LASTEXITCODE -ne 0) { throw 'Browser E2E run failed.' }
    } finally {
      Pop-Location
    }
  } finally {
    Stop-ChildProcessTree $frontendProcess
    Stop-ChildProcessTree $backendProcess
    $frontendProcess = $null
    $backendProcess = $null
  }

  # Runtime-off is a distinct process so Chromium observes the actual backend
  # capability declaration rather than a client-side stub.
  $env:CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED = '0'
  $runtimeOffBackend = Start-Process -FilePath $python -ArgumentList '-m','uvicorn','app.api.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $backend -WindowStyle Hidden -PassThru
  try {
    $runtimeOffFrontend = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev','--','--host=localhost','--port=4499','--strictPort' -WorkingDirectory $frontend -WindowStyle Hidden -PassThru
    Push-Location $frontend
    try {
      & node tests/pr06b4-local-browser-e2e.mjs $fixture --runtime-off
      if ($LASTEXITCODE -ne 0) { throw 'Runtime-off browser E2E run failed.' }
    } finally {
      Pop-Location
    }
  } finally {
    Stop-ChildProcessTree $runtimeOffFrontend
    Stop-ChildProcessTree $runtimeOffBackend
    $runtimeOffFrontend = $null
    $runtimeOffBackend = $null
  }
} finally {
  Stop-ChildProcessTree $frontendProcess
  Stop-ChildProcessTree $backendProcess
  Stop-ChildProcessTree $runtimeOffFrontend
  Stop-ChildProcessTree $runtimeOffBackend

  $cleanupFailures = @()
  if ($databaseCreated) {
    try {
      & $python -c $dropDatabase
      if ($LASTEXITCODE -ne 0) { $cleanupFailures += 'Disposable database cleanup failed.' }
    } catch {
      $cleanupFailures += 'Disposable database cleanup failed.'
    }
  }
  try {
    if (Test-Path -LiteralPath $runRoot) {
      $resolvedCleanup = (Resolve-Path -LiteralPath $runRoot).Path
      if (-not $resolvedCleanup.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing cleanup outside the system temporary directory.'
      }
      Remove-Item -LiteralPath $runRoot -Recurse -Force
    }
  } catch {
    $cleanupFailures += 'Temporary E2E directory cleanup failed.'
  }
  foreach ($name in $managedEnvironmentNames) {
    try {
      if ($previousEnvironment.ContainsKey($name)) {
        Set-Item -LiteralPath "Env:$name" -Value $previousEnvironment[$name]
      } else {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
      }
    } catch {
      $cleanupFailures += "Environment restoration failed for $name."
    }
  }
  if ($cleanupFailures.Count -gt 0) {
    throw ($cleanupFailures -join ' ')
  }
}
