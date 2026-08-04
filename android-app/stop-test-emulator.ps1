$ErrorActionPreference = 'Stop'
$Adb = Join-Path $env:LOCALAPPDATA 'FinRegAndroidBuild\android-sdk\platform-tools\adb.exe'

if (-not (Test-Path $Adb)) {
    throw "ADB is missing: $Adb"
}

$Serials = (& $Adb devices | Select-String '^emulator-\d+\s+device').Line |
    ForEach-Object { ($_ -split '\s+')[0] }

if (-not $Serials) {
    Write-Host 'No running Android emulator was found.'
    exit 0
}

foreach ($Serial in $Serials) {
    Write-Host "Stopping emulator: $Serial"
    & $Adb -s $Serial emu kill
}
