# Release installer template; scripts/build.py stamps the release tag (PowerShell 5.1+).
$ErrorActionPreference = 'Stop'
$releaseTag = '@RELEASE_TAG@'
if ($releaseTag.StartsWith('@')) {
    throw 'Use an installer from a release, or generate one with scripts/build.py.'
}
$nativeArchitecture = $env:PROCESSOR_ARCHITEW6432
if (-not $nativeArchitecture) { $nativeArchitecture = $env:PROCESSOR_ARCHITECTURE }
if ($env:OS -ne 'Windows_NT' -or $nativeArchitecture -ne 'AMD64') {
    throw 'Windows x64 is required.'
}

do {
    $mqttHost = (Read-Host 'MQTT host (hostname or IP address)').Trim()
    $validHost = $mqttHost -match '^[a-zA-Z0-9:][a-zA-Z0-9._:-]*$'
    if (-not $validHost) {
        Write-Host 'Enter a hostname or IP address without a URL scheme, brackets, or spaces.'
    }
} until ($validHost)
do {
    $portInput = (Read-Host 'MQTT port [1883]').Trim()
    if (-not $portInput) { $portInput = '1883' }
    $mqttPort = 0
    $validPort = [int]::TryParse($portInput, [ref]$mqttPort) -and $mqttPort -ge 1 -and $mqttPort -le 65535
    if (-not $validPort) { Write-Host 'Enter a port between 1 and 65535.' }
} until ($validPort)

$installDir = Join-Path $env:LOCALAPPDATA 'pc2mqtt'
$executable = Join-Path $installDir 'pc2mqtt.exe'
$startupDir = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDir 'pc2mqtt.lnk'
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
$download = Join-Path $installDir ('download-' + [guid]::NewGuid().ToString() + '.exe')
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/maxim-mityutko/pc2mqtt/releases/download/$releaseTag/pc2mqtt-$releaseTag-windows-x64.exe" -OutFile $download
    # Stop only this user's installed copy before replacing a locked executable.
    # Snapshot both PyInstaller's launcher and child before stopping either.
    $running = @(Get-Process -Name pc2mqtt -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $executable })
    foreach ($process in $running) {
        try {
            if (-not $process.HasExited) { $process.Kill() }
        } catch {
            # A child may exit when its parent is stopped.
            if (-not $process.HasExited) { throw }
        }
    }
    foreach ($process in $running) {
        if (-not $process.WaitForExit(10000)) {
            throw 'The installed pc2mqtt process did not exit; the executable was not replaced.'
        }
    }
    Move-Item -LiteralPath $download -Destination $executable -Force
    Unblock-File -LiteralPath $executable

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $executable
    $shortcut.Arguments = "--host $mqttHost --port $mqttPort --tray"
    $shortcut.WorkingDirectory = $installDir
    $shortcut.Save()
    Start-Process -FilePath $executable -ArgumentList $shortcut.Arguments -WorkingDirectory $installDir
} finally {
    if (Test-Path -LiteralPath $download) { Remove-Item -LiteralPath $download -Force }
}
Write-Host "Installed and launched pc2mqtt in $installDir. It will start in the system tray at login."
Write-Host "Startup shortcut: $shortcutPath"
