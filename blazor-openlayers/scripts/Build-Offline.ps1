# Never downloads packages or SDKs. Run in a child PowerShell process.
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Bundle)
. (Join-Path $PSScriptRoot 'Common.ps1')
if ($env:OS -ne 'Windows_NT' -or ![Environment]::Is64BitProcess) { throw 'Use 64-bit PowerShell on Windows.' }
$Bundle = (Resolve-Path -LiteralPath $Bundle).Path
Test-DemoChecksums $Bundle
$source = Join-Path $Bundle 'source'
$work = Join-Path $Bundle ('.artifacts\build-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$sdkDirectory = Join-Path $work 'sdk'
Expand-Archive -LiteralPath (Join-Path $Bundle 'dotnet-sdk-win-x64.zip') -DestinationPath $sdkDirectory
Set-DemoEnvironment $sdkDirectory $work
$env:NUGET_CERT_REVOCATION_MODE = 'offline'
$dotnet = Join-Path $sdkDirectory 'dotnet.exe'
$feed = [Security.SecurityElement]::Escape((Join-Path $Bundle 'feed'))
$config = Join-Path $work 'NuGet.Config'
@"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources><clear/><add key="offline" value="$feed"/></packageSources>
  <auditSources><clear/></auditSources>
</configuration>
"@ | Set-Content $config -Encoding UTF8

# A fresh source copy and package cache prevent a successful build from depending on old state.
$buildSource = Join-Path $work 'source'
New-Item -ItemType Directory -Path $buildSource | Out-Null
Get-ChildItem -LiteralPath $source -Force | Where-Object { $_.Name -notin @('bin', 'obj', '.artifacts', 'offline-bundle') } |
    Copy-Item -Destination $buildSource -Recurse
$publish = Join-Path $work 'publish'
Push-Location $buildSource
try {
    $expected = (Get-Content -Raw 'global.json' | ConvertFrom-Json).sdk.version
    $actual = & $dotnet --version
    if ($LASTEXITCODE -ne 0 -or $actual -ne $expected) { throw "Expected SDK $expected; found $actual" }
    Invoke-DemoDotnet $dotnet @('restore', '--locked-mode', '--no-http-cache', '--configfile', $config, '-p:NuGetAudit=false')
    Invoke-DemoDotnet $dotnet @('publish', '-c', 'Release', '--no-restore', '-o', $publish, '-p:NuGetAudit=false')
} finally { Pop-Location }
Write-Host "Published app: $publish\wwwroot"
Write-Host 'Edit published map-config.json for your internal GeoServer, then serve wwwroot over HTTP(S).'
Write-Host 'To start the local development server for a quick check:'
Write-Host "& `"$dotnet`" run --project `"$buildSource\BlazorOpenLayers.csproj`" -c Release --no-build --no-restore --urls http://127.0.0.1:5080"
