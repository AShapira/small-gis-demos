param(
    [string]$QgisRoot = 'C:\Program Files\QGIS 4.2.0',
    [Parameter(Mandatory=$true)][string]$Script,
    [string]$ArgumentsJson = '[]',
    [string]$ArgumentsFile
)
$ErrorActionPreference = 'Stop'
Get-Content (Join-Path $QgisRoot 'bin\qgis-bin.env') | ForEach-Object {
    $pair = $_ -split '=',2
    if ($pair.Count -eq 2) { [Environment]::SetEnvironmentVariable($pair[0],$pair[1],'Process') }
}
$qgisPython = Join-Path $QgisRoot 'apps\qgis\python'
$env:PYTHONPATH = "$qgisPython;$qgisPython\plugins;$env:PYTHONPATH"
$env:PYTHONIOENCODING = 'utf-8'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$python = Join-Path $env:PYTHONHOME 'python.exe'
if ($ArgumentsFile) { $ArgumentsJson = Get-Content -Raw $ArgumentsFile }
$scriptArguments = $ArgumentsJson | ConvertFrom-Json
$logBase = Join-Path (Split-Path $Script -Parent) ('launch-' + [guid]::NewGuid().ToString('N'))
$quoted = @('-u', ('"' + $Script + '"')) + @($scriptArguments | Where-Object { $null -ne $_ -and $_ -ne '' } | ForEach-Object { '"' + $_ + '"' })
$process = Start-Process -FilePath $python -ArgumentList $quoted -WorkingDirectory (Split-Path $Script -Parent) -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput "$logBase.stdout.log" -RedirectStandardError "$logBase.stderr.log"
Get-Content "$logBase.stdout.log"
Get-Content "$logBase.stderr.log"
exit $process.ExitCode
