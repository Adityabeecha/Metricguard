<#
.SYNOPSIS
  Capture MetricGuard pages with headless Microsoft Edge (design review screenshots).
.EXAMPLE
  .\scripts\capture_ui.ps1 -OutDir .impeccable\review\before
#>
param(
  [string]$Base = 'http://127.0.0.1:8000/w/local-retail',
  [string]$OutDir = '.impeccable\review',
  [string[]]$Pages = @('dashboard', 'start', 'investigations', 'data-sources', 'changes', 'metrics'),
  [int]$Width = 1440,
  [int]$Height = 1000,
  [int]$MobileWidth = 390,
  [int]$MobileHeight = 1600
)
$ErrorActionPreference = 'Stop'
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw 'Microsoft Edge was not found.' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$profile = Join-Path $env:TEMP "mg-capture-$PID"
foreach ($page in $Pages) {
  foreach ($size in @(@('desktop', $Width, $Height), @('mobile', $MobileWidth, $MobileHeight))) {
    $file = Join-Path (Resolve-Path $OutDir) "$($page -replace '[^a-z0-9-]', '_')-$($size[0]).png"
    # Edge logs harmless warnings on stderr; run it as its own process and wait.
    $args = @('--headless=new', '--disable-gpu', '--hide-scrollbars', "--user-data-dir=`"$profile`"",
              "--window-size=$($size[1]),$($size[2])", '--virtual-time-budget=9000', "--screenshot=`"$file`"", "$Base/$page")
    Start-Process -FilePath $edge -ArgumentList $args -Wait -WindowStyle Hidden `
      -RedirectStandardError (Join-Path $env:TEMP 'mg-capture.err') -RedirectStandardOutput (Join-Path $env:TEMP 'mg-capture.out')
    Write-Host "  - $file"
  }
}
Remove-Item -Recurse -Force $profile -ErrorAction SilentlyContinue
