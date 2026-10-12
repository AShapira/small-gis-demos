#requires -Version 5.1
<#
.SYNOPSIS
Loads an extracted offline basemap bundle into Windows Podman Desktop.
.EXAMPLE
.\load-and-run.ps1 -BundlePath 'D:\Maps\east-europe-bundle'
.NOTES
Requires a running Linux AMD64 Podman machine. No Compose or Python required.
Persistent volumes are never removed or overwritten by this script.
#>
[CmdletBinding()]
param(
    [string]$BundlePath = $PSScriptRoot,
    [string]$Connection,
    [ValidateRange(1, 65535)][int]$Port,
    [string]$BindAddress = '127.0.0.1',
    [ValidatePattern('^[a-z0-9][a-z0-9_-]{0,47}$')][string]$Name = 'east-europe-bundle',
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-PodmanExecutable {
    if ($env:OS -eq 'Windows_NT' -and ($env:PATHEXT -split ';') -notcontains '.EXE') {
        throw 'This PowerShell session has no .EXE entry in PATHEXT. Open a normal Windows PowerShell terminal and retry.'
    }
    $command = Get-Command podman.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command podman -CommandType Application -ErrorAction SilentlyContinue }
    if (-not $command) { throw 'Podman CLI is missing. Install Podman Desktop and prepare its Linux machine before going offline.' }
    return $command.Source
}

function Invoke-Podman {
    param([string[]]$Arguments, [switch]$AllowFailure)
    # Capture native stderr without turning it into terminating PowerShell 5.1 errors.
    # Do not echo command arguments/output: inspect and startup errors can contain credentials.
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = -1
        $output = & $script:Podman @script:ConnectionArguments @Arguments 2>&1
        $code = $global:LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "Podman $($Arguments[0]) failed (exit $code). Inspect the deployment in Podman Desktop; raw output is withheld because it may contain credentials."
    }
    return [pscustomobject]@{ ExitCode = $code; Text = (($output | Out-String).Trim()) }
}

function Read-PodmanJson {
    param([string[]]$Arguments)
    $result = Invoke-Podman $Arguments
    return ($result.Text | ConvertFrom-Json)
}

function Test-PodmanResource {
    param([string]$Kind, [string]$ResourceName)
    $result = Invoke-Podman @($Kind, 'exists', $ResourceName) -AllowFailure
    if ($result.ExitCode -eq 0) { return $true }
    if ($result.ExitCode -eq 1) { return $false }
    throw "Cannot check Podman $Kind '$ResourceName' (exit $($result.ExitCode)). Check the selected connection."
}

function Get-TextHash {
    param([string]$Value)
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value)))).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
}

function Write-Utf8File {
    param([string]$Path, [string]$Value)
    [IO.File]::WriteAllText($Path, $Value, (New-Object Text.UTF8Encoding $false))
}

function Read-VerifiedBundle {
    param([string]$Path)
    $root = (Get-Item -LiteralPath $Path -Force).FullName
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'BundlePath must be an extracted bundle directory.' }
    # Walk explicitly so junctions/symlinks are rejected before following them.
    $queue = New-Object 'System.Collections.Generic.Queue[string]'
    $queue.Enqueue($root)
    $files = New-Object 'System.Collections.Generic.List[string]'
    while ($queue.Count) {
        $directory = $queue.Dequeue()
        if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Bundle directories must not contain junctions or symbolic links.'
        }
        foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Bundle files must not contain junctions or symbolic links.' }
            if ($item.PSIsContainer) { $queue.Enqueue($item.FullName) }
            else { $files.Add($item.FullName) }
        }
    }
    $sumsPath = Join-Path $root 'SHA256SUMS'
    $entries = @{}
    $lines = @(Get-Content -LiteralPath $sumsPath)
    $index = 0
    foreach ($line in $lines) {
        if ($line -notmatch '^([a-fA-F0-9]{64})  (.+)$') { throw 'Invalid SHA256SUMS line.' }
        $digest, $relative = $Matches[1].ToLowerInvariant(), $Matches[2]
        if ($relative -match '[\\:<>"|?*\x00-\x1f]' -or $relative.StartsWith('/') -or
            $relative -match '(^|/)\.\.?(/|$)|//|/$|(^|/)[^/]*[. ](/|$)' -or $entries.ContainsKey($relative)) {
            throw 'Unsafe or duplicate SHA256SUMS path.'
        }
        $file = Join-Path $root $relative
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing bundle file: $relative" }
        $index++
        Write-Progress -Activity 'Verifying offline bundle' -Status "$index / $($lines.Count): $relative" -PercentComplete (100 * $index / $lines.Count)
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $digest) { throw "Checksum mismatch: $relative" }
        $entries[$relative] = $digest
    }
    Write-Progress -Activity 'Verifying offline bundle' -Completed
    foreach ($required in @('.env', 'config.json', 'source-manifest.json', 'images/geoserver.tar', 'init-permissions.sh',
            'data_dir/data/regional.mbtiles', 'data_dir/data/world.gpkg', 'cache/geowebcache.xml', 'cache/geowebcache-diskquota.xml')) {
        if (-not $entries.ContainsKey($required)) { throw "Required file is not covered by SHA256SUMS: $required" }
    }
    foreach ($file in $files) {
        $relative = $file.Substring($root.TrimEnd('\', '/').Length + 1).Replace('\', '/')
        if ($relative -match '^(data_dir|fonts|cache)/' -and -not $entries.ContainsKey($relative)) {
            throw "Unverified runtime file: $relative. Use a fresh extraction of the export."
        }
    }
    if (@($entries.Keys | Where-Object { $_ -match '^fonts/.+\.(ttf|otf)$' }).Count -eq 0) { throw 'The verified bundle has no fonts.' }
    $envValues = @{}
    foreach ($line in Get-Content -LiteralPath (Join-Path $root '.env')) {
        if (-not $line.Trim() -or $line.TrimStart().StartsWith('#')) { continue }
        if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$' -or $envValues.ContainsKey($Matches[1])) { throw 'Invalid or duplicate .env entry.' }
        $envValues[$Matches[1]] = $Matches[2]
    }
    foreach ($key in @('GEOSERVER_ADMIN_USER', 'GEOSERVER_ADMIN_PASSWORD', 'GEOSERVER_PORT', 'GEOSERVER_HEAP')) {
        if (-not $envValues.ContainsKey($key) -or -not $envValues[$key]) { throw "Missing .env setting: $key" }
    }
    if ($envValues.GEOSERVER_HEAP -notmatch '^\d+[mMgG]$') { throw 'Invalid GEOSERVER_HEAP.' }
    $manifest = Get-Content -LiteralPath (Join-Path $root 'source-manifest.json') -Raw | ConvertFrom-Json
    $id = $manifest.images.geoserver.id -replace '^sha256:', ''
    if ($id -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid GeoServer image ID in source-manifest.json.' }
    # Parse config as well: malformed/incomplete extracts fail before Podman mutations.
    $null = Get-Content -LiteralPath (Join-Path $root 'config.json') -Raw | ConvertFrom-Json
    return [pscustomobject]@{
        Root = $root; Entries = $entries; Env = $envValues; Image = "sha256:$($id.ToLowerInvariant())"
        Fingerprint = (Get-FileHash -LiteralPath $sumsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Assert-ManagedResource {
    param($Labels, [hashtable]$Expected, [string]$ResourceName)
    foreach ($key in $Expected.Keys) {
        $property = if ($null -ne $Labels) { $Labels.PSObject.Properties[$key] } else { $null }
        if (-not $property -or $property.Value -ne $Expected[$key]) {
            throw "Resource '$ResourceName' conflicts with this bundle or its launch settings. Use a different -Name; existing data will not be replaced."
        }
    }
}

function Get-HttpBytes {
    param([string]$Url, [int]$TimeoutSeconds = 15)
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
    try {
        $response = $client.GetAsync($Url).GetAwaiter().GetResult()
        try {
            if ([int]$response.StatusCode -ne 200) { throw "HTTP $([int]$response.StatusCode)" }
            return ,($response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult())
        } finally { $response.Dispose() }
    } finally { $client.Dispose(); $handler.Dispose() }
}

function Assert-Capabilities {
    param([byte[]]$Bytes, [string]$Service)
    $settings = New-Object Xml.XmlReaderSettings
    $settings.DtdProcessing = [Xml.DtdProcessing]::Ignore
    $settings.XmlResolver = $null
    $stream = New-Object IO.MemoryStream(,$Bytes)
    $reader = [Xml.XmlReader]::Create($stream, $settings)
    try {
        $document = New-Object Xml.XmlDocument
        $document.XmlResolver = $null
        $document.Load($reader)
        $expectedRoot = if ($Service -eq 'WMS') { 'WMS_Capabilities' } else { 'Capabilities' }
        if ($document.DocumentElement.LocalName -ne $expectedRoot) { throw "Invalid $Service capabilities." }
        $names = @($document.SelectNodes("//*[local-name()='Layer']/*[local-name()='Name' or local-name()='Identifier']") | ForEach-Object { $_.InnerText })
        foreach ($style in @('osm-bright', 'positron', 'dark-matter', 'toner', 'maptiler-basic', 'world')) {
            foreach ($suffix in @('', '-en')) {
                if ($names -notcontains "omt:$style$suffix") { throw "Missing $Service layer omt:$style$suffix" }
            }
        }
    } finally { $reader.Dispose(); $stream.Dispose() }
}

function Assert-Png {
    param([byte[]]$Bytes)
    if ($Bytes.Length -lt 33 -or [BitConverter]::ToString($Bytes[0..7]) -ne '89-50-4E-47-0D-0A-1A-0A' -or
        [Text.Encoding]::ASCII.GetString($Bytes, 12, 4) -ne 'IHDR' -or
        [BitConverter]::ToString($Bytes[16..23]) -ne '00-00-01-00-00-00-01-00') {
        throw 'Map response is not a 256 x 256 PNG.'
    }
    # Decode too: a PNG header alone is not evidence of a usable map image.
    Add-Type -AssemblyName System.Drawing
    $stream = New-Object IO.MemoryStream(,$Bytes)
    try {
        $bitmap = [Drawing.Image]::FromStream($stream, $true, $true)
        try { if ($bitmap.Width -ne 256 -or $bitmap.Height -ne 256) { throw 'Unexpected rendered dimensions.' } }
        finally { $bitmap.Dispose() }
    } finally { $stream.Dispose() }
}

function Wait-Basemap {
    param([string]$BaseUrl, [string]$ContainerName, [int]$TimeoutSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $ready = $false
    do {
        try {
            $remaining = [Math]::Max(1, [Math]::Min(15, [int]($deadline - [DateTime]::UtcNow).TotalSeconds))
            Assert-Capabilities (Get-HttpBytes "$BaseUrl/ows?service=WMS&version=1.3.0&request=GetCapabilities" $remaining) 'WMS'
            $ready = $true
            break
        } catch {
            $state = @(Read-PodmanJson @('container', 'inspect', $ContainerName))[0].State.Status
            if ($state -in @('exited', 'dead', 'stopped')) { throw 'GeoServer stopped before becoming ready. Inspect its logs privately in Podman Desktop.' }
            if ([DateTime]::UtcNow -ge $deadline) { break }
            Start-Sleep -Seconds 2
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $ready) { throw 'Timed out waiting for Windows-side WMS readiness. Check Podman forwarding, the selected bind address, and GeoServer logs.' }
    Assert-Capabilities (Get-HttpBytes "$BaseUrl/gwc/service/wmts?service=WMTS&version=1.0.0&request=GetCapabilities") 'WMTS'
    Assert-Png (Get-HttpBytes "$BaseUrl/wms?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=omt:osm-bright-en&STYLES=&SRS=EPSG:4326&BBOX=14.39,50.07,14.45,50.10&WIDTH=256&HEIGHT=256&FORMAT=image/png")
    Assert-Png (Get-HttpBytes "$BaseUrl/gwc/service/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=omt:osm-bright-en&STYLE=&TILEMATRIXSET=EPSG:3857&TILEMATRIX=EPSG:3857:6&TILECOL=34&TILEROW=21&FORMAT=image/png")
}

function Start-OfflineBundle {
    [CmdletBinding()]
    param(
        [string]$BundlePath = $PSScriptRoot,
        [string]$Connection,
        [ValidateRange(1, 65535)][int]$Port,
        [string]$BindAddress = '127.0.0.1',
        [ValidatePattern('^[a-z0-9][a-z0-9_-]{0,47}$')][string]$Name = 'east-europe-bundle',
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 600
    )
    $address = $null
    if (-not [Net.IPAddress]::TryParse($BindAddress, [ref]$address) -or $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw 'BindAddress must be an IPv4 address (default 127.0.0.1).'
    }
    Write-Host 'Verifying the extracted bundle before changing Podman resources...'
    $bundle = Read-VerifiedBundle $BundlePath
    if (-not $PSBoundParameters.ContainsKey('Port')) { $Port = [int]$bundle.Env.GEOSERVER_PORT }
    if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid GEOSERVER_PORT.' }
    $script:Podman = Get-PodmanExecutable
    $script:ConnectionArguments = @()
    if ($Connection) { $script:ConnectionArguments = @('--connection', $Connection) }
    $info = Read-PodmanJson @('info', '--format', 'json')
    if ($info.host.os -ne 'linux' -or $info.host.arch -ne 'amd64') { throw 'The selected Podman connection must use a running Linux AMD64 machine.' }
    $fingerprint = $bundle.Fingerprint
    $labels = @{
        'io.small-gis-demos.offline.name' = $Name
        'io.small-gis-demos.offline.bundle' = $fingerprint
        'io.small-gis-demos.offline.settings' = (Get-TextHash "windows-podman-v1|$Name|$BindAddress|$Port|$($bundle.Env.GEOSERVER_HEAP)|$($bundle.Image)")
    }
    $labelArgs = @()
    foreach ($key in ($labels.Keys | Sort-Object)) { $labelArgs += @('--label', "$key=$($labels[$key])") }
    $volumes = @("$Name-catalog", "$Name-fonts", "$Name-cache")
    $network = "$Name-isolated"
    $helper = "$Name-initialize"
    $existingVolumes = @()
    foreach ($volume in $volumes) {
        $exists = Test-PodmanResource 'volume' $volume
        $existingVolumes += $exists
        if ($exists) { Assert-ManagedResource (@(Read-PodmanJson @('volume', 'inspect', $volume))[0].Labels) $labels $volume }
    }
    $volumeCount = @($existingVolumes | Where-Object { $_ }).Count
    if ($volumeCount -ne 0 -and $volumeCount -ne 3) { throw 'Incomplete volume set. Preserve it for inspection and choose a new -Name; automatic overwrite is disabled.' }
    $networkExists = Test-PodmanResource 'network' $network
    if ($networkExists) {
        $net = @(Read-PodmanJson @('network', 'inspect', $network))[0]
        Assert-ManagedResource $net.labels $labels $network
        if (-not $net.internal) { throw "Network '$network' is not internal." }
    }
    if (Test-PodmanResource 'container' $helper) { throw "Initialization container '$helper' already exists. Inspect the interrupted deployment or choose a new -Name." }
    $containerExists = Test-PodmanResource 'container' $Name
    $running = $false
    if ($containerExists) {
        $container = @(Read-PodmanJson @('container', 'inspect', $Name))[0]
        Assert-ManagedResource $container.Config.Labels $labels $Name
        if ($volumeCount -ne 3 -or -not $networkExists) { throw 'The existing deployment is missing persistent resources.' }
        if (($container.Image -replace '^sha256:', '') -ne ($bundle.Image -replace '^sha256:', '')) { throw 'Existing container image differs from the bundle.' }
        $running = [bool]$container.State.Running
    }
    # Conflicts are checked before even importing the bundled image.
    if (-not (Test-PodmanResource 'image' $bundle.Image)) {
        Write-Host 'Loading the bundled GeoServer image (no registry access)...'
        $null = Invoke-Podman @('load', '--input', (Join-Path $bundle.Root 'images/geoserver.tar'))
    }
    $image = @(Read-PodmanJson @('image', 'inspect', $bundle.Image))[0]
    if (($image.Id -replace '^sha256:', '') -ne ($bundle.Image -replace '^sha256:', '') -or $image.Os -ne 'linux' -or $image.Architecture -ne 'amd64') {
        throw 'Loaded image does not match the locked Linux AMD64 image.'
    }
    if (-not $running) {
        $temp = Join-Path ([IO.Path]::GetTempPath()) ('basemap-launcher-' + [Guid]::NewGuid().ToString('N'))
        $helperCreated = $false
        $null = New-Item -ItemType Directory -Path $temp
        try {
            if (-not $networkExists) { $null = Invoke-Podman (@('network', 'create', '--internal') + $labelArgs + @($network)) }
            if ($volumeCount -eq 0) {
                foreach ($volume in $volumes) { $null = Invoke-Podman (@('volume', 'create') + $labelArgs + @($volume)) }
            }
            $mode = if ($volumeCount -eq 0) { 'initialize' } else { 'reuse' }
            $mountArgs = @('--volume', "$($volumes[0]):/bundle/data_dir", '--volume', "$($volumes[1]):/bundle/fonts", '--volume', "$($volumes[2]):/bundle/cache")
            $null = Invoke-Podman (@('create', '--name', $helper, '--pull=never', '--network', 'none', '--user', '0:0', '--entrypoint', '/bin/bash') +
                $labelArgs + $mountArgs + @($bundle.Image, '/tmp/initialize.sh', $mode, $fingerprint))
            $helperCreated = $true
            $initialization = @'
#!/bin/bash
set -euo pipefail
mode="$1"
fingerprint="$2"
marker=/bundle/data_dir/.offline-launcher-ready
if [[ "$mode" == initialize ]]; then
    test ! -e "$marker"
    cd /bundle
    sha256sum --check /tmp/volume-SHA256SUMS >/dev/null
    chown -R 2000:2000 /bundle/data_dir /bundle/fonts /bundle/cache
    printf '%s\n' "$fingerprint" > "$marker"
    chown 2000:2000 "$marker"
else
    test -f "$marker"
    test "$(cat "$marker")" = "$fingerprint"
fi
mkdir -p /tmp/docker-entrypoint-geoserver.d
install -m 0755 /tmp/99-demo-permissions.sh /tmp/docker-entrypoint-geoserver.d/99-demo-permissions.sh
'@
            Write-Utf8File (Join-Path $temp 'initialize.sh') ($initialization.Replace("`r`n", "`n") + "`n")
            $null = Invoke-Podman @('cp', (Join-Path $temp 'initialize.sh'), "${helper}:/tmp/initialize.sh")
            $null = Invoke-Podman @('cp', (Join-Path $bundle.Root 'init-permissions.sh'), "${helper}:/tmp/99-demo-permissions.sh")
            if ($volumeCount -eq 0) {
                Write-Host 'Copying catalog, fonts, and cache into Podman volumes; large datasets may take several minutes...'
                foreach ($directory in @('data_dir', 'fonts', 'cache')) {
                    # Relative sources avoid Windows drive-letter ambiguity in podman cp.
                    Push-Location -LiteralPath $bundle.Root
                    try { $null = Invoke-Podman @('cp', "./$directory/.", "${helper}:/bundle/$directory") }
                    finally { Pop-Location }
                }
                $checks = @($bundle.Entries.Keys | Where-Object { $_ -match '^(data_dir|fonts|cache)/' } | Sort-Object | ForEach-Object { "$($bundle.Entries[$_])  $_" })
                Write-Utf8File (Join-Path $temp 'volume-SHA256SUMS') (($checks -join "`n") + "`n")
                $null = Invoke-Podman @('cp', (Join-Path $temp 'volume-SHA256SUMS'), "${helper}:/tmp/volume-SHA256SUMS")
            }
            $result = Invoke-Podman @('start', '--attach', $helper) -AllowFailure
            if ($result.ExitCode -ne 0) {
                throw 'Volume initialization/verification failed or the volumes are incomplete. Data was retained; inspect disk space and choose a new -Name for a fresh attempt.'
            }
            if (-not $containerExists) {
                $envSettings = @{
                    GEOSERVER_ADMIN_USER = $bundle.Env.GEOSERVER_ADMIN_USER; GEOSERVER_ADMIN_PASSWORD = $bundle.Env.GEOSERVER_ADMIN_PASSWORD
                    STABLE_EXTENSIONS = 'mbstyle-plugin'; ACTIVE_EXTENSIONS = 'mbstyle-plugin'; COMMUNITY_EXTENSIONS = 'mbtiles-store-plugin'
                    FORCE_DOWNLOAD_STABLE_EXTENSIONS = 'false'; FORCE_DOWNLOAD_COMMUNITY_EXTENSIONS = 'false'
                    SAMPLE_DATA = 'false'; RECREATE_DATADIR = 'false'; GEOSERVER_DATA_DIR = '/opt/geoserver/data_dir'
                    GEOWEBCACHE_CACHE_DIR = '/opt/geoserver/gwc'; INITIAL_MEMORY = '1G'; MAXIMUM_MEMORY = $bundle.Env.GEOSERVER_HEAP
                    JAVA_TOOL_OPTIONS = '-DMBSTYLE_ROOT_TILE_PIXELS=256'; GOOGLE_FONTS_NAMES = ''
                    GEOSERVER_DISABLE_STATIC_WEB_FILES = 'false'; GEOSERVER_STATIC_WEB_FILES_SCRIPT = 'SELF'
                }
                $envArgs = @(); $oldEnv = @{}
                try {
                    # Pass only variable names on the command line, never the password.
                    foreach ($key in $envSettings.Keys) {
                        $oldEnv[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
                        [Environment]::SetEnvironmentVariable($key, $envSettings[$key], 'Process')
                        $envArgs += @('--env', $(if ($envSettings[$key] -eq '') { "$key=" } else { $key }))
                    }
                    $null = Invoke-Podman (@('create', '--name', $Name, '--pull=never', '--user', '0:0', '--restart', 'unless-stopped',
                        '--network', $network, '--publish', "${BindAddress}:${Port}:8080",
                        '--volume', "$($volumes[0]):/opt/geoserver/data_dir", '--volume', "$($volumes[1]):/opt/fonts:ro",
                        '--volume', "$($volumes[2]):/opt/geoserver/gwc",
                        '--health-cmd', 'curl -fsS http://127.0.0.1:8080/geoserver/ows?service=WMS\&request=GetCapabilities',
                        '--health-interval', '30s', '--health-timeout', '10s', '--health-retries', '10', '--health-start-period', '120s') +
                        $labelArgs + $envArgs + @($bundle.Image))
                } finally {
                    foreach ($key in $oldEnv.Keys) { [Environment]::SetEnvironmentVariable($key, $oldEnv[$key], 'Process') }
                }
            }
            $null = Invoke-Podman @('cp', "${helper}:/tmp/docker-entrypoint-geoserver.d", "${Name}:/")
            $null = Invoke-Podman @('start', $Name)
        } finally {
            if ($helperCreated) {
                $cleanup = Invoke-Podman @('rm', '-f', $helper) -AllowFailure
                if ($cleanup.ExitCode -ne 0) { Write-Warning "Temporary container '$helper' remains; inspect it before retrying." }
            }
            if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
        }
    }
    $probeAddress = if ($BindAddress -eq '0.0.0.0') { '127.0.0.1' } else { $BindAddress }
    $baseUrl = "http://${probeAddress}:${Port}/geoserver"
    Write-Host "Waiting for GeoServer at $baseUrl ..."
    Wait-Basemap $baseUrl $Name $TimeoutSeconds
    Write-Host "Ready: WMS/WMTS capabilities and representative PNG renders passed."
    Write-Host "Viewer: $baseUrl/www/basemap/index.html"
    Write-Host "WMS:    $baseUrl/wms"
    Write-Host "WMTS:   $baseUrl/gwc/service/wmts"
    $prefix = 'podman'
    if ($Connection) { $prefix += " --connection '" + $Connection.Replace("'", "''") + "'" }
    Write-Host "Stop:   $prefix stop $Name"
    Write-Host "Start:  $prefix start $Name"
    Write-Host "Logs:   $prefix logs --tail 100 $Name (keep logs private)"
    Write-Host "Persistent volumes: $($volumes -join ', ')"
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Start-OfflineBundle @PSBoundParameters }
    catch {
        # Avoid PowerShell's default exception dump, which can show a source line containing secrets.
        [Console]::Error.WriteLine('Offline basemap startup failed: ' + $_.Exception.Message)
        exit 1
    }
}
