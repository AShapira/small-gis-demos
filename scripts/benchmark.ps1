[CmdletBinding()]
param(
    [ValidateSet('build','build-webp','doctor','select','fetch','prepare','convert','validate','serve','smoke','benchmark','report','run','resume','status','estimate','overnight')]
    [string]$Action = 'status',
    [string]$Config = 'configs/full.yaml',
    [string]$PythonExe = 'python',
    [string]$PodmanExe = '',
    [string]$CredentialDirectory = '',
    [string]$Variant = ''
    ,[string]$ExecutionPlan = 'configs/overnight-10.yaml'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$Repo = Split-Path $PSScriptRoot -Parent
if (!$PodmanExe) { $PodmanExe = Join-Path $env:LOCALAPPDATA 'Programs\Podman\podman.exe' }
if (!(Test-Path $PodmanExe)) { throw 'Set -PodmanExe to the Windows Podman executable.' }
$env:RASTERBENCH_PODMAN = $PodmanExe
if ($CredentialDirectory) { $env:RASTERBENCH_CDSE_SECRET_DIR = $CredentialDirectory }
Push-Location $Repo
try {
    & $PythonExe -c 'import sys,yaml; assert sys.version_info >= (3,11)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ and PyYAML are required. See README setup.' }
    if ($Action -eq 'build') {
        & $PythonExe scripts/bootstrap.py
    } elseif ($Action -eq 'build-webp') {
        & $PythonExe scripts/build_webp.py
    } elseif ($Action -eq 'overnight') {
        & $PythonExe -m rasterbench.overnight --plan $ExecutionPlan
    } else {
        $Arguments = @('-m','rasterbench.cli',$Action,'--config',$Config)
        if ($Variant) { $Arguments += @('--variant',$Variant) }
        & $PythonExe @Arguments
    }
    if ($LASTEXITCODE -ne 0) { throw "Benchmark action failed with exit code $LASTEXITCODE. Read .runs logs." }
} finally { Pop-Location }
