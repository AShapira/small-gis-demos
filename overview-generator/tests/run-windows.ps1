param(
    [Parameter(Mandatory=$true)][string]$QgisRoot,
    [ValidateSet('cli','api','console')][string]$Mode = 'cli'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$scriptName = switch ($Mode) {
    'cli' { 'validate_overview.py' }
    'api' { 'start_api_validation.py' }
    'console' { 'standalone_gui.py' }
}
$scriptPath = Join-Path $PSScriptRoot $scriptName
$active = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w|3)?\.exe$' -and $_.CommandLine -like "*$scriptPath*"
}
if ($active) {
    $active | Select-Object ProcessId,CommandLine
    throw 'This test is already running; inspect its logs before restarting.'
}
$envFile = Join-Path $QgisRoot 'bin\qgis-bin.env'
if (!(Test-Path $envFile)) { throw "QGIS environment file not found: $envFile" }
Get-Content $envFile | ForEach-Object {
    $pair = $_ -split '=',2
    if ($pair.Count -eq 2) { [Environment]::SetEnvironmentVariable($pair[0],$pair[1],'Process') }
}
$qgisPython = Join-Path $QgisRoot 'apps\qgis\python'
$env:PYTHONPATH = "$qgisPython;$qgisPython\plugins;$env:PYTHONPATH"
$python = Join-Path $env:PYTHONHOME 'python.exe'
if (!(Test-Path $python)) { throw "QGIS Python not found: $python" }
$runDir = Join-Path $root '.validation'
New-Item -ItemType Directory -Force $runDir | Out-Null
$launchId = "$Mode-$(Get-Date -Format yyyyMMdd-HHmmss)-$([guid]::NewGuid().ToString('N').Substring(0,8))"
$stdout = Join-Path $runDir "$launchId-stdout.txt"
$stderr = Join-Path $runDir "$launchId-stderr.txt"
$p = Start-Process $python -ArgumentList @('-u',"`"$scriptPath`"") -WorkingDirectory $root -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$record = [pscustomobject]@{Pid=$p.Id;Mode=$Mode;QgisRoot=$QgisRoot;Python=$python;Started=(Get-Date).ToString('o');Stdout=$stdout;Stderr=$stderr}
$record | ConvertTo-Json | Set-Content (Join-Path $runDir "$launchId.json")
$record | Format-List
