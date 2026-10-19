<#
.SYNOPSIS
  Start, inspect or stop the local MetricGuard application (API + isolated-runner supervisor).

.EXAMPLE
  .\scripts\mg.ps1 start            # build what is missing, start API + supervisor, open nothing
  .\scripts\mg.ps1 start -Seed      # ...and load the two demo investigations
  .\scripts\mg.ps1 start -Workers 3 # run up to 3 comparisons at the same time (default 2)
  .\scripts\mg.ps1 status
  .\scripts\mg.ps1 stop

.NOTES
  Data lives in -DataDir (default data\demo-app), separate from test and acceptance data.
  The default workspace accepts local requests without a key. Set -RequireKey to make
  every request sign in with an API key (issue one with scripts\issue_api_key.py).
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)][ValidateSet('start', 'status', 'stop', 'restart')][string]$Command = 'status',
  [int]$Port = 8000,
  [string]$DataDir = 'data\demo-app',
  [switch]$Seed,
  [switch]$RequireKey,
  [switch]$NoDocker,
  # Comparisons that run at the same time. Each isolated runner may use up to 1 GiB of
  # Docker memory, so keep this within what Docker Desktop is given.
  [ValidateRange(1, 4)][int]$Workers = 2
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$RunDir = Join-Path $Root '.metricguard'
$StateFile = Join-Path $RunDir 'run.json'
$Image = 'metricguard-runner:1.0'
$Url = "http://127.0.0.1:$Port"
$PortGiven = $PSBoundParameters.ContainsKey('Port')

function Write-Step([string]$Text) { Write-Host "  - $Text" }
function Fail([string]$Text) { Write-Host "ERROR: $Text" -ForegroundColor Red; exit 1 }

function Get-State {
  if (-not (Test-Path -LiteralPath $StateFile)) { return $null }
  try { return Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json } catch { return $null }
}

function Test-Pid([object]$ProcessId) {
  if (-not $ProcessId) { return $false }
  return [bool](Get-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue)
}

function Get-PortOwner([int]$P) {
  $conn = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $conn) { return $null }
  $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
  return [pscustomobject]@{ Pid = $conn.OwningProcess; Name = $(if ($proc) { $proc.ProcessName } else { 'unknown' }) }
}

function Get-Health {
  try { return Invoke-RestMethod -Uri "$Url/api/v1/health" -TimeoutSec 3 } catch { return $null }
}

function Get-RunnerSourceHash {
  # The runner image bakes in the engine code; a changed engine needs a rebuilt image,
  # otherwise new definitions (e.g. ratios) would run on, and be refused by, old code.
  $files = @('infra\Dockerfile.runner', 'requirements.txt') +
    (Get-ChildItem -LiteralPath 'packages', 'services\supervisor' -Recurse -File -Filter '*.py' |
      Where-Object { $_.FullName -notmatch '__pycache__' } | Sort-Object FullName | ForEach-Object { $_.FullName })
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $buffer = New-Object System.IO.MemoryStream
  foreach ($f in $files) {
    $name = [System.Text.Encoding]::UTF8.GetBytes(((Resolve-Path -LiteralPath $f -Relative) -replace '\\', '/') + "`n")
    $buffer.Write($name, 0, $name.Length)
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $f))
    $buffer.Write($bytes, 0, $bytes.Length)
  }
  return ([System.BitConverter]::ToString($sha.ComputeHash($buffer.ToArray())) -replace '-', '').ToLower()
}

function Test-Docker {
  if ($NoDocker) { return 'skipped' }
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return 'missing' }
  & docker info --format '{{.ServerVersion}}' *> $null
  if ($LASTEXITCODE -ne 0) { return 'stopped' }
  return 'running'
}

function Show-Status {
  $state = Get-State
  # Report the recorded instance on its own port unless -Port was given explicitly.
  if ($state -and -not $PSBoundParameters.ContainsKey('Port') -and -not $script:PortGiven) { $script:Url = "http://127.0.0.1:$($state.port)" }
  $health = Get-Health
  Write-Host "MetricGuard at $Url"
  if ($state -and [int]$state.port -ne [int]($Url -replace '.*:', '')) {
    Write-Step "The instance started by this script runs on port $($state.port), not $($Url -replace '.*:', '')."
    $state = $null
  }
  if ($state) {
    $api = if (Test-Pid $state.api_pid) { "running (PID $($state.api_pid))" } else { 'not running' }
    $pids = @(Get-SupervisorPids $state)
    $alive = @($pids | Where-Object { Test-Pid $_ })
    $sup = if (-not $pids.Count) { 'not started' } elseif ($alive.Count -eq $pids.Count) { "$($alive.Count) worker(s) running (PID $($alive -join ', '))" } else { "$($alive.Count) of $($pids.Count) worker(s) running" }
    Write-Step "API: $api"
    Write-Step "Comparison engine: $sup"
    Write-Step "Data directory: $($state.data_dir)"
    Write-Step "Logs: $($state.log_dir)"
  } else {
    Write-Step 'Not started by this script.'
    $owner = Get-PortOwner $Port
    if ($owner) { Write-Step "Port $Port is in use by $($owner.Name) (PID $($owner.Pid))." }
  }
  if ($health) {
    Write-Step "Health: $($health.status), mode $($health.mode), engine available: $($health.runner_available)"
  } else {
    Write-Step 'Health: API not reachable.'
  }
  Write-Step "Docker: $(Test-Docker)"
}

function Get-SupervisorPids($state) {
  # Older state files record a single supervisor_pid.
  if ($state.PSObject.Properties.Name -contains 'supervisor_pids' -and $state.supervisor_pids) { return @($state.supervisor_pids) }
  if ($state.supervisor_pid) { return @($state.supervisor_pid) }
  return @()
}

function Stop-App {
  $state = Get-State
  if (-not $state) { Write-Host 'Nothing to stop (no running instance recorded).'; return }
  foreach ($id in @(Get-SupervisorPids $state)) {
    if (Test-Pid $id) {
      Stop-Process -Id ([int]$id) -Force -ErrorAction SilentlyContinue
      Write-Step "Stopped comparison worker (PID $id)"
    }
  }
  if (Test-Pid $state.api_pid) {
    Stop-Process -Id ([int]$state.api_pid) -Force -ErrorAction SilentlyContinue
    Write-Step "Stopped api (PID $($state.api_pid))"
  }
  Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
  Write-Host 'MetricGuard stopped. Your data is kept.'
}

function Start-App {
  Write-Host 'Checking prerequisites'
  $state = Get-State
  if ($state -and (Test-Pid $state.api_pid)) {
    Write-Host "MetricGuard is already running at $Url (PID $($state.api_pid)). Use 'restart' to start fresh, or 'status'."
    return
  }
  if ($state) { Remove-Item -LiteralPath $StateFile -Force }

  if (-not (Test-Path -LiteralPath $Python)) {
    Fail "Python environment not found. Run: py -3.12 -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements.txt"
  }
  Write-Step 'Python environment found'

  $owner = Get-PortOwner $Port
  if ($owner) {
    $free = ($Port + 1)..($Port + 50) | Where-Object { -not (Get-PortOwner $_) } | Select-Object -First 1
    $hint = if ($free) { "choose a free port, for example: .\scripts\mg.ps1 start -Port $free" } else { 'choose another port with -Port.' }
    Fail "Port $Port is already in use by $($owner.Name) (PID $($owner.Pid)). Stop that program, or $hint"
  }
  Write-Step "Port $Port is free"

  if (-not (Test-Path -LiteralPath 'apps\web\dist\index.html')) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail 'The web interface is not built and npm is not installed. Install Node.js 20+.' }
    Write-Step 'Building the web interface (first run)'
    if (-not (Test-Path -LiteralPath 'apps\web\node_modules')) { & npm ci --prefix apps/web; if ($LASTEXITCODE -ne 0) { Fail 'npm ci failed.' } }
    & npm run build --prefix apps/web; if ($LASTEXITCODE -ne 0) { Fail 'Web build failed.' }
  }
  Write-Step 'Web interface built'

  $docker = Test-Docker
  switch ($docker) {
    'missing' { Fail 'Docker is not installed. Install Docker Desktop, start it, then run this again. (Use -NoDocker to start view-only.)' }
    'stopped' { Fail 'Docker Desktop is installed but not running. Start Docker Desktop, wait until it says "Engine running", then run this again.' }
    'running' {
      $sourceHash = Get-RunnerSourceHash
      # Read the labels as JSON: Windows PowerShell drops inner quotes passed to native programs.
      $built = $null; $present = $false
      $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
      try {
        $labels = (& docker image inspect $Image --format '{{json .Config.Labels}}' 2>$null)
        if ($LASTEXITCODE -eq 0) {
          $present = $true
          $parsed = "$labels" | ConvertFrom-Json
          if ($parsed) { $built = $parsed.'metricguard.source' }
        }
      } catch { } finally { $ErrorActionPreference = $previous }
      if (-not $present -or "$built".Trim() -ne $sourceHash) {
        $why = if (-not $present) { 'first run' } else { 'engine code changed' }
        Write-Step "Building the isolated runner image $Image ($why, a few minutes)"
        # docker writes build progress to stderr; judge success by the exit code only.
        $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { & docker build -f infra/Dockerfile.runner --label "metricguard.source=$sourceHash" -t $Image . 2>&1 | ForEach-Object { "$_" } | Where-Object { $_.Trim() -and $_ -ne 'System.Management.Automation.RemoteException' } | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" } }
        finally { $ErrorActionPreference = $previous }
        if ($LASTEXITCODE -ne 0) { Fail 'Runner image build failed.' }
      }
      Write-Step "Docker running; runner image $Image matches the current engine code"
    }
    'skipped' { Write-Step 'Docker skipped: comparisons will wait until the engine is available' }
  }

  $data = Join-Path $Root $DataDir
  $logs = Join-Path $data 'logs'
  New-Item -ItemType Directory -Force -Path $data, $logs, $RunDir | Out-Null

  $env:METRICGUARD_MODE = 'isolated'
  $env:METRICGUARD_DATA_DIR = $data
  $env:METRICGUARD_DATABASE_URL = "sqlite:///$($data -replace '\\','/')/metricguard.db"
  $env:METRICGUARD_ALLOW_UNVERIFIED_LOCAL = $(if ($RequireKey) { 'false' } else { 'true' })
  # Temporary runner job folders outside OneDrive/synced folders: faster Docker file sharing.
  $env:METRICGUARD_JOB_DIR = Join-Path $env:LOCALAPPDATA 'MetricGuard\jobs'
  New-Item -ItemType Directory -Force -Path $env:METRICGUARD_JOB_DIR | Out-Null

  Write-Host 'Starting'
  $api = Start-Process -FilePath $Python -ArgumentList '-m', 'uvicorn', 'services.api.app:app', '--host', '127.0.0.1', '--port', "$Port" `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logs 'api.out.log') -RedirectStandardError (Join-Path $logs 'api.err.log')
  $supervisorPids = @()
  if ($docker -eq 'running') {
    # Each worker claims queued jobs with a compare-and-set lease, so several can share one queue.
    foreach ($n in 1..$Workers) {
      $suffix = if ($n -eq 1) { '' } else { "-$n" }
      $sup = Start-Process -FilePath $Python -ArgumentList '-m', 'services.supervisor.worker' `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs "supervisor$suffix.out.log") -RedirectStandardError (Join-Path $logs "supervisor$suffix.err.log")
      $supervisorPids += $sup.Id
    }
  }
  $supervisorPid = if ($supervisorPids.Count) { $supervisorPids[0] } else { $null }
  [pscustomobject]@{ api_pid = $api.Id; supervisor_pid = $supervisorPid; supervisor_pids = $supervisorPids; workers = $Workers; port = $Port; data_dir = $data; log_dir = $logs; started_at = (Get-Date).ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8

  $deadline = (Get-Date).AddSeconds(45)
  $health = $null
  while ((Get-Date) -lt $deadline -and -not $health) {
    if (-not (Test-Pid $api.Id)) { Fail "The API exited during startup. See $(Join-Path $logs 'api.err.log')." }
    Start-Sleep -Milliseconds 700
    $health = Get-Health
  }
  if (-not $health) { Fail "The API did not answer within 45 seconds. See $(Join-Path $logs 'api.err.log')." }
  Write-Step "API ready (PID $($api.Id))"
  if ($supervisorPids.Count) { Write-Step "Comparison engine started: $($supervisorPids.Count) worker(s) can run comparisons at the same time (PID $($supervisorPids -join ', '))" }

  if ($Seed) {
    Write-Host 'Loading demo investigations'
    $seedArgs = @('-m', 'scripts.seed_closeout_demo', '--api', $Url)
    & $Python @seedArgs
    if ($LASTEXITCODE -ne 0) { Write-Host 'Demo seeding did not finish; the app is still running. See the message above.' -ForegroundColor Yellow }
  }

  Write-Host ''
  Write-Host "MetricGuard is running: $Url" -ForegroundColor Cyan
  Write-Host "Stop it with: .\scripts\mg.ps1 stop"
}

switch ($Command) {
  'start' { Start-App }
  'status' { Show-Status }
  'stop' { Stop-App }
  'restart' { Stop-App; Start-App }
}
