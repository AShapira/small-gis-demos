# Run using powershell.exe -NoProfile -File .\scripts\Prepare-Offline.ps1
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $PSScriptRoot '..\offline-bundle'),
    [string]$SdkArchive
)
. (Join-Path $PSScriptRoot 'Common.ps1')
if ($env:OS -ne 'Windows_NT' -or ![Environment]::Is64BitProcess) { throw 'Use 64-bit PowerShell on Windows.' }

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$Destination = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $Destination) { throw 'Destination already exists. Choose a new empty destination.' }
$sdkVersion = (Get-Content -Raw (Join-Path $projectRoot 'global.json') | ConvertFrom-Json).sdk.version
$work = Join-Path $projectRoot ('.artifacts\prepare-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Destination, $work | Out-Null
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Preserve the official SDK archive and verify it against Microsoft's release metadata.
$releases = Invoke-RestMethod 'https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json'
$sdk = @($releases.releases | ForEach-Object { $_.sdks } | Where-Object { $_.version -eq $sdkVersion })[0]
if (!$sdk) { throw "SDK $sdkVersion is not in Microsoft's release metadata." }
$archiveInfo = @($sdk.files | Where-Object { $_.rid -eq 'win-x64' -and $_.name.EndsWith('.zip') })[0]
$archive = Join-Path $Destination 'dotnet-sdk-win-x64.zip'
if ($SdkArchive) {
    Copy-Item -LiteralPath $SdkArchive -Destination $archive
} else {
    Invoke-WebRequest -UseBasicParsing -Uri $archiveInfo.url -OutFile $archive
}
if ((Get-FileHash $archive -Algorithm SHA512).Hash -ne $archiveInfo.hash) { throw 'SDK SHA512 verification failed.' }
$sdkDirectory = Join-Path $work 'sdk'
Expand-Archive -LiteralPath $archive -DestinationPath $sdkDirectory
Set-DemoEnvironment $sdkDirectory $work
$dotnet = Join-Path $sdkDirectory 'dotnet.exe'

# Copy source explicitly, excluding caches, output, and this bundle.
$source = Join-Path $Destination 'source'
New-Item -ItemType Directory -Path $source | Out-Null
foreach ($name in @('BlazorOpenLayers.csproj', 'global.json', 'packages.lock.json', 'Program.cs', '_Imports.razor',
                    'App.razor', 'MapOptions.cs', 'SampleGraphics.cs', 'README.md', 'VALIDATION.md', '.gitignore', 'wwwroot', 'scripts', 'tests')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $name) -Destination $source -Recurse
}
$onlineConfig = Join-Path $work 'NuGet.Config'
@'
<?xml version="1.0" encoding="utf-8"?>
<configuration><packageSources><clear/><add key="nuget.org" value="https://api.nuget.org/v3/index.json"/></packageSources></configuration>
'@ | Set-Content $onlineConfig -Encoding UTF8

Push-Location $source
try {
    Invoke-DemoDotnet $dotnet @('restore', '--locked-mode', '--configfile', $onlineConfig, '-p:NuGetAudit=false')
    Invoke-DemoDotnet $dotnet @('publish', '-c', 'Release', '--no-restore', '-o', (Join-Path $work 'publish'))
} finally { Pop-Location }

# A flat local NuGet feed needs the original .nupkg files, including runtime packs.
$feed = Join-Path $Destination 'feed'
New-Item -ItemType Directory -Path $feed | Out-Null
Get-ChildItem -LiteralPath $env:NUGET_PACKAGES -Recurse -Filter '*.nupkg' | Copy-Item -Destination $feed
if (@(Get-ChildItem $feed -Filter '*.nupkg').Count -eq 0) { throw 'No NuGet packages were collected.' }
# Remove only generated files in our new source copy; never touch the original checkout.
foreach ($name in @('bin', 'obj')) {
    $generated = Join-Path $source $name
    if (Test-Path $generated) { Remove-Item -LiteralPath $generated -Recurse -Force }
}
Write-DemoChecksums $Destination
Write-Host "Bundle prepared: $Destination"
Write-Host 'Transfer the entire directory. On the offline Windows machine run:'
Write-Host "powershell.exe -NoProfile -File `"$Destination\source\scripts\Build-Offline.ps1`" -Bundle `"$Destination`""
