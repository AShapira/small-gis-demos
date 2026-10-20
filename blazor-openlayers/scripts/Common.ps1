Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# WSL-launched PowerShell may inherit a PATHEXT without .EXE and treat dotnet as a document.
if ($env:OS -eq 'Windows_NT' -and ($env:PATHEXT -split ';') -notcontains '.EXE') {
    $env:PATHEXT = $env:PATHEXT + ';.EXE'
}

function Invoke-DemoDotnet {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "dotnet failed with exit code $LASTEXITCODE" }
}

function Set-DemoEnvironment {
    param([string]$SdkDirectory, [string]$WorkDirectory)
    # Callers run in a child PowerShell process, so these do not change the user's shell.
    $env:DOTNET_ROOT = $SdkDirectory
    $env:DOTNET_MULTILEVEL_LOOKUP = '0'
    $env:DOTNET_CLI_HOME = Join-Path $WorkDirectory 'cli-home'
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
    $env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
    $env:DOTNET_GENERATE_ASPNET_CERTIFICATE = 'false'
    $env:DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE = 'true'
    $env:NUGET_PACKAGES = Join-Path $WorkDirectory 'packages'
    $env:NUGET_HTTP_CACHE_PATH = Join-Path $WorkDirectory 'http-cache'
    $env:NUGET_PLUGINS_CACHE_PATH = Join-Path $WorkDirectory 'plugins-cache'
}

function Write-DemoChecksums {
    param([string]$Directory)
    $records = @(Get-ChildItem -LiteralPath $Directory -File -Recurse | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($Directory.TrimEnd('\', '/').Length + 1).Replace('\', '/')
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    ConvertTo-Json -InputObject $records -Depth 4 | Set-Content -LiteralPath (Join-Path $Directory 'checksums.json') -Encoding UTF8
}

function Test-DemoChecksums {
    param([string]$Directory)
    $records = Get-Content -Raw -LiteralPath (Join-Path $Directory 'checksums.json') | ConvertFrom-Json
    if (@($records).Count -eq 0) { throw 'The bundle checksum manifest is empty.' }
    foreach ($record in $records) {
        $file = [IO.Path]::GetFullPath((Join-Path $Directory $record.path))
        if (!$file.StartsWith($Directory.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Invalid checksum path: $($record.path)"
        }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $record.sha256) {
            throw "Bundle checksum mismatch: $($record.path)"
        }
    }
}
