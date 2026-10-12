#requires -Version 5.1
# Dependency-free contract tests. All Podman operations below are simulated.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'load-and-run.ps1')
$originalNative = ${function:Invoke-Podman}
$originalWait = ${function:Wait-Basemap}
$originalHttp = ${function:Get-HttpBytes}
$originalPng = ${function:Assert-Png}
$testRoot = Join-Path $root ('.artifacts/windows-launcher-tests-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $testRoot -Force
$script:Passed = 0
$script:FixtureSecret = 'fixture-private-password-keep-out-of-errors'
$previousPathExt = $env:PATHEXT
# WSL-launched PowerShell on the development host inherits PATHEXT=.CPL.
# Normalize only this test process so the native exit-code probe executes cmd.exe.
$env:PATHEXT = '.COM;.EXE;.BAT;.CMD'

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "Assertion failed: $Message" }
}
function Assert-Throws {
    param([scriptblock]$Action, [string]$Pattern)
    $caught = $false
    try { & $Action | Out-Null }
    catch {
        $caught = $true
        Assert-True ($_.Exception.Message -match $Pattern) "Expected error matching '$Pattern', got: $($_.Exception.Message)"
        Assert-True (-not $_.Exception.Message.Contains($script:FixtureSecret)) 'Error revealed the password'
    }
    Assert-True $caught "Expected error matching '$Pattern'"
}
function Test-Case {
    param([string]$Title, [scriptblock]$Body)
    & $Body
    $script:Passed++
    Write-Host "PASS $Title"
}
function Update-Checksums {
    param([string]$Directory)
    $lines = @(Get-ChildItem -LiteralPath $Directory -File -Recurse -Force | Where-Object { $_.Name -ne 'SHA256SUMS' } | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($Directory.Length + 1).Replace('\', '/')
        (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $relative
    })
    Write-Utf8File (Join-Path $Directory 'SHA256SUMS') (($lines -join "`n") + "`n")
}
function New-Fixture {
    $directory = Join-Path $testRoot ('bundle with spaces ' + [Guid]::NewGuid().ToString('N'))
    foreach ($sub in @('images', 'data_dir/data', 'fonts', 'cache')) { $null = New-Item -ItemType Directory -Path (Join-Path $directory $sub) -Force }
    $contents = @{
        '.env' = "GEOSERVER_ADMIN_USER=admin`nGEOSERVER_ADMIN_PASSWORD=$script:FixtureSecret`nGEOSERVER_PORT=8602`nGEOSERVER_HEAP=4G`n"
        'config.json' = '{"port":8602,"geoserver_heap":"4G"}'
        'source-manifest.json' = ('{"images":{"geoserver":{"tag":"docker.io/kartoza/geoserver:3.0.1","id":"' + ('a' * 64) + '"}}}')
        'images/geoserver.tar' = 'fake image archive'
        'init-permissions.sh' = "#!/bin/bash`nexit 0`n"
        'data_dir/data/regional.mbtiles' = 'fixture regional data'
        'data_dir/data/world.gpkg' = 'fixture world data'
        'fonts/example.ttf' = 'fixture font'
        'cache/geowebcache.xml' = '<gwc/>'
        'cache/geowebcache-diskquota.xml' = '<quota/>'
    }
    foreach ($file in $contents.Keys) { Write-Utf8File (Join-Path $directory $file) $contents[$file] }
    Update-Checksums $directory
    return $directory
}
function Reset-FakePodman {
    $script:Calls = New-Object 'System.Collections.Generic.List[object]'
    $script:Resources = @{ volume = @{}; network = @{}; container = @{}; image = @{} }
    $script:FailCommand = ''
    $script:FailInitialization = $false
    $script:ReadyVolumes = $false
    $script:WrongImage = $false
    $script:HealthCalls = 0
}
function Get-FakeLabels {
    param([string[]]$Arguments)
    $labels = @{}
    for ($i = 0; $i -lt $Arguments.Count; $i++) {
        if ($Arguments[$i] -eq '--label') {
            $pair = $Arguments[$i + 1] -split '=', 2
            $labels[$pair[0]] = $pair[1]
        }
    }
    return $labels
}
function Get-FakeArg {
    param([string[]]$Arguments, [string]$Flag)
    return $Arguments[[Array]::IndexOf($Arguments, $Flag) + 1]
}
function Get-PodmanExecutable { return 'simulated-podman' }
function Wait-Basemap {
    param([string]$BaseUrl, [string]$ContainerName, [int]$TimeoutSeconds)
    Assert-True ($BaseUrl -eq 'http://127.0.0.1:8602/geoserver') 'Windows-side probe URL'
    $script:HealthCalls++
}
function Invoke-Podman {
    param([string[]]$Arguments, [switch]$AllowFailure)
    $script:Calls.Add([pscustomobject]@{ Args = $Arguments })
    Assert-True (-not (($Arguments -join ' ').Contains($script:FixtureSecret))) 'Password must not appear in CLI arguments'
    $code = 0; $value = $null
    if (($Arguments -join ' ') -eq $script:FailCommand) { $code = 125 }
    elseif ($Arguments[0] -eq 'info') { $value = @{ host = @{ os = 'linux'; arch = 'amd64' } } }
    elseif ($Arguments.Count -gt 1 -and $Arguments[1] -eq 'exists') {
        if (-not $script:Resources[$Arguments[0]].ContainsKey($Arguments[2])) { $code = 1 }
    }
    elseif ($Arguments.Count -gt 1 -and $Arguments[1] -eq 'inspect') {
        $value = @($script:Resources[$Arguments[0]][$Arguments[2]])
        if ($Arguments[0] -eq 'image' -and $script:WrongImage) { $value = @(@{ Id = ('b' * 64); Os = 'linux'; Architecture = 'amd64' }) }
    }
    elseif ($Arguments[0] -eq 'load') {
        $id = 'sha256:' + ('a' * 64)
        $script:Resources.image[$id] = @{ Id = $id; Os = 'linux'; Architecture = 'amd64' }
    }
    elseif ($Arguments[0] -in @('volume', 'network') -and $Arguments[1] -eq 'create') {
        $object = @{ Labels = (Get-FakeLabels $Arguments) }
        if ($Arguments[0] -eq 'network') { $object.internal = ($Arguments -contains '--internal') }
        $script:Resources[$Arguments[0]][$Arguments[-1]] = $object
    }
    elseif ($Arguments[0] -eq 'create') {
        $containerName = Get-FakeArg $Arguments '--name'
        $script:Resources.container[$containerName] = @{
            Config = @{ Labels = (Get-FakeLabels $Arguments) }
            State = @{ Running = $false; Status = 'created' }; Image = ('sha256:' + ('a' * 64))
            Arguments = $Arguments
        }
        if (-not $containerName.EndsWith('-initialize')) {
            Assert-True ([Environment]::GetEnvironmentVariable('GEOSERVER_ADMIN_PASSWORD', 'Process') -eq $script:FixtureSecret) 'Password available only through inherited environment'
        }
    }
    elseif ($Arguments[0] -eq 'start') {
        $containerName = $Arguments[-1]
        if ($containerName.EndsWith('-initialize')) {
            $helperArgs = $script:Resources.container[$containerName].Arguments
            if ($script:FailInitialization -or ($helperArgs[-2] -eq 'reuse' -and -not $script:ReadyVolumes)) { $code = 1 }
            else { $script:ReadyVolumes = $true }
        } else { $script:Resources.container[$containerName].State = @{ Running = $true; Status = 'running' } }
    }
    elseif ($Arguments[0] -eq 'rm') { $script:Resources.container.Remove($Arguments[-1]) }
    elseif ($Arguments[0] -ne 'cp') { throw ('Unimplemented simulated command: ' + ($Arguments -join ' ')) }
    if ($code -ne 0 -and -not $AllowFailure) { throw "Simulated Podman command failed (exit $code)." }
    $text = if ($null -ne $value) { ConvertTo-Json -InputObject $value -Depth 12 -Compress } else { '' }
    return [pscustomobject]@{ ExitCode = $code; Text = $text }
}
function Get-Calls {
    param([string]$Verb)
    return @($script:Calls | Where-Object { $_.Args[0] -eq $Verb })
}

try {
    Test-Case 'Valid bundle, including spaces in its path' {
        $script:Fixture = New-Fixture
        $verified = Read-VerifiedBundle $script:Fixture
        Assert-True ($verified.Entries.Count -eq 10) 'All manifest entries verified'
    }
    Test-Case 'Corrupted file fails before Podman' {
        Reset-FakePodman
        $fixture = New-Fixture
        Write-Utf8File (Join-Path $fixture 'data_dir/data/regional.mbtiles') 'changed'
        Assert-Throws { Start-OfflineBundle -BundlePath $fixture } 'Checksum mismatch'
        Assert-True ($script:Calls.Count -eq 0) 'No Podman operations before checksums pass'
    }
    Test-Case 'Missing file and traversal manifest rejected' {
        $fixture = New-Fixture
        Remove-Item -LiteralPath (Join-Path $fixture 'images/geoserver.tar')
        Assert-Throws { Read-VerifiedBundle $fixture } 'Missing bundle file'
        Write-Utf8File (Join-Path $fixture 'SHA256SUMS') ((('a' * 64) + '  ../outside') + "`n")
        Assert-Throws { Read-VerifiedBundle $fixture } 'Unsafe or duplicate'
    }
    Test-Case 'Case-insensitive duplicate and unlisted runtime file rejected' {
        $fixture = New-Fixture
        Write-Utf8File (Join-Path $fixture 'fonts/extra.ttf') 'unverified'
        Assert-Throws { Read-VerifiedBundle $fixture } 'Unverified runtime file'
        Remove-Item -LiteralPath (Join-Path $fixture 'fonts/extra.ttf')
        $first = (Get-Content -LiteralPath (Join-Path $fixture 'SHA256SUMS'))[0]
        [IO.File]::AppendAllText((Join-Path $fixture 'SHA256SUMS'), $first.ToUpperInvariant() + "`n")
        Assert-Throws { Read-VerifiedBundle $fixture } 'Unsafe or duplicate'
    }
    Test-Case 'Native stderr and exit code handled without credential disclosure' {
        $script:Podman = $env:ComSpec
        $script:ConnectionArguments = @()
        $result = & $originalNative -Arguments @('/d', '/c', "echo $script:FixtureSecret 1>&2 & exit /b 23") -AllowFailure
        Assert-True ($result.ExitCode -eq 23) 'Native exit code retained'
        Assert-Throws { & $originalNative -Arguments @('/d', '/c', "echo $script:FixtureSecret 1>&2 & exit /b 23") } 'exit 23'
    }
    Test-Case 'Fresh deployment loads image, copies data, isolates network, restores environment' {
        Reset-FakePodman
        $prior = [Environment]::GetEnvironmentVariable('GEOSERVER_ADMIN_PASSWORD', 'Process')
        try {
            [Environment]::SetEnvironmentVariable('GEOSERVER_ADMIN_PASSWORD', 'preexisting-value', 'Process')
            Start-OfflineBundle -BundlePath $script:Fixture -Connection 'test-connection'
            Assert-True ([Environment]::GetEnvironmentVariable('GEOSERVER_ADMIN_PASSWORD', 'Process') -eq 'preexisting-value') 'Caller environment restored'
        } finally { [Environment]::SetEnvironmentVariable('GEOSERVER_ADMIN_PASSWORD', $prior, 'Process') }
        Assert-True (@(Get-Calls 'load').Count -eq 1) 'One image import'
        Assert-True ($script:Resources.volume.Count -eq 3) 'Three persistent volumes'
        Assert-True ($script:Resources.container.Count -eq 1) 'Temporary container removed'
        Assert-True $script:ReadyVolumes 'Initialization marker established'
        $creates = @(Get-Calls 'create')
        Assert-True ($creates.Count -eq 2) 'Helper and service created'
        foreach ($create in $creates) { Assert-True ($create.Args -contains '--pull=never') 'No pulls allowed' }
        Assert-True ($creates[0].Args -contains 'none') 'Initialization network disabled'
        Assert-True ($creates[1].Args -contains 'east-europe-bundle-fonts:/opt/fonts:ro') 'Fonts mounted read-only'
        Assert-True ($script:ConnectionArguments[1] -eq 'test-connection') 'Explicit connection selected without modifying default'
        Assert-True (@($script:Calls | Where-Object { $_.Args[0] -eq 'cp' -and $_.Args[1] -match '^\./(data_dir|fonts|cache)/\.$' }).Count -eq 3) 'Runtime copied with relative paths'
        Assert-True ($script:HealthCalls -eq 1) 'Readiness checked'
    }
    Test-Case 'Running deployment reused without reimport or copy' {
        $script:Calls.Clear()
        Start-OfflineBundle -BundlePath $script:Fixture
        Assert-True (@(Get-Calls 'create').Count -eq 0 -and @(Get-Calls 'cp').Count -eq 0 -and @(Get-Calls 'load').Count -eq 0) 'No data overwrite on rerun'
    }
    Test-Case 'Stopped deployment restarts without recopying persistent data' {
        $script:Resources.container['east-europe-bundle'].State = @{ Running = $false; Status = 'exited' }
        $script:Calls.Clear()
        Start-OfflineBundle -BundlePath $script:Fixture
        Assert-True $script:Resources.container['east-europe-bundle'].State.Running 'Stopped service started'
        Assert-True (@($script:Calls | Where-Object { $_.Args[0] -eq 'cp' -and $_.Args[1] -match '^\./' }).Count -eq 0) 'Catalog/cache not overwritten'
        Assert-True (@(Get-Calls 'create').Count -eq 1) 'Only temporary helper created'
    }
    Test-Case 'Missing serving container recreated from initialized volumes' {
        $script:Resources.container.Remove('east-europe-bundle')
        $script:Calls.Clear()
        Start-OfflineBundle -BundlePath $script:Fixture
        Assert-True $script:Resources.container['east-europe-bundle'].State.Running 'Service recreated'
        Assert-True (@($script:Calls | Where-Object { $_.Args[0] -eq 'cp' -and $_.Args[1] -match '^\./' }).Count -eq 0) 'Persistent data preserved'
    }
    Test-Case 'Changed launch settings refuse existing resources' {
        $script:Calls.Clear()
        Assert-Throws { Start-OfflineBundle -BundlePath $script:Fixture -Port 8604 } 'conflicts'
        Assert-True (@(Get-Calls 'create').Count -eq 0) 'No new container on conflict'
    }
    Test-Case 'Partial volumes refuse automatic recovery' {
        $script:Resources.volume.Remove('east-europe-bundle-cache')
        Assert-Throws { Start-OfflineBundle -BundlePath $script:Fixture } 'Incomplete volume set'
    }
    Test-Case 'Wrong image ID fails before volume creation' {
        Reset-FakePodman
        $script:WrongImage = $true
        Assert-Throws { Start-OfflineBundle -BundlePath $script:Fixture } 'does not match'
        Assert-True ($script:Resources.volume.Count -eq 0) 'No volumes for incorrect image'
    }
    Test-Case 'Initialization failure retains volumes and rejects later reuse' {
        Reset-FakePodman
        $script:FailInitialization = $true
        Assert-Throws { Start-OfflineBundle -BundlePath $script:Fixture } 'initialization/verification failed'
        Assert-True ($script:Resources.volume.Count -eq 3 -and $script:Resources.container.Count -eq 0) 'Persistent data retained, helper removed'
        $script:FailInitialization = $false
        Assert-Throws { Start-OfflineBundle -BundlePath $script:Fixture } 'initialization/verification failed'
        Assert-True ($script:Resources.container.Count -eq 0) 'Service never launched with incomplete data'
    }
    Test-Case 'Connection errors are not mistaken for missing resources' {
        Reset-FakePodman
        $script:FailCommand = 'volume exists east-europe-bundle-catalog'
        Assert-Throws { Start-OfflineBundle -BundlePath $script:Fixture } 'Cannot check Podman'
        Assert-True ($script:Resources.volume.Count -eq 0) 'No mutations after failed existence query'
    }
    Test-Case 'XML exceptions and missing layers are rejected' {
        Assert-Throws { Assert-Capabilities ([Text.Encoding]::UTF8.GetBytes('<ServiceExceptionReport/>')) 'WMS' } 'Invalid WMS'
        Assert-Throws { Assert-Capabilities ([Text.Encoding]::UTF8.GetBytes('<WMS_Capabilities/>')) 'WMS' } 'Missing WMS layer'
        $layers = @('osm-bright', 'positron', 'dark-matter', 'toner', 'maptiler-basic', 'world') | ForEach-Object { "<Layer><Name>omt:$_</Name></Layer><Layer><Name>omt:$_-en</Name></Layer>" }
        Assert-Capabilities ([Text.Encoding]::UTF8.GetBytes('<WMS_Capabilities>' + ($layers -join '') + '</WMS_Capabilities>')) 'WMS'
    }
    Test-Case 'PNG decoding accepts images and rejects exception/header-only responses' {
        Assert-Throws { Assert-Png ([Text.Encoding]::UTF8.GetBytes('<ServiceException>bad style</ServiceException>')) } 'not a 256'
        Add-Type -AssemblyName System.Drawing
        $bitmap = New-Object Drawing.Bitmap(256, 256)
        $stream = New-Object IO.MemoryStream
        try {
            $bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
            $bytes = $stream.ToArray()
            Assert-Png $bytes
            Assert-Throws { Assert-Png $bytes[0..32] } '.'
        } finally { $stream.Dispose(); $bitmap.Dispose() }
    }
    Test-Case 'Readiness times out on invalid HTTP responses' {
        function Get-HttpBytes { param([string]$Url, [int]$TimeoutSeconds); throw 'simulated HTTP unavailable' }
        Reset-FakePodman
        $script:Resources.container['service'] = @{ State = @{ Status = 'running' } }
        Assert-Throws { & $originalWait 'http://127.0.0.1:8602/geoserver' 'service' 1 } 'Timed out'
    }
    Write-Host "All $script:Passed Windows launcher contract tests passed. No Podman resources were created."
} finally {
    $env:PATHEXT = $previousPathExt
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
