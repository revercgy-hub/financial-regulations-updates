param(
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$SdkRoot = Join-Path $env:LOCALAPPDATA 'FinRegAndroidBuild\android-sdk'
$Emulator = Join-Path $SdkRoot 'emulator\emulator.exe'
$Adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
$AvdName = 'FinReg_API35'

if (-not (Test-Path $Emulator)) {
    throw "Android Emulator is missing: $Emulator"
}
if (-not (Test-Path $Adb)) {
    throw "ADB is missing: $Adb"
}

& $Adb start-server | Out-Null
$Serial = (& $Adb devices | Select-String '^emulator-\d+\s+device').Line |
    ForEach-Object { ($_ -split '\s+')[0] } |
    Select-Object -First 1

if (-not $Serial) {
    Write-Host "Starting visible Android 15 emulator: $AvdName"
    Start-Process -FilePath $Emulator -ArgumentList @(
        '-avd', $AvdName,
        '-memory', '2048',
        '-gpu', 'swiftshader_indirect',
        '-no-audio',
        '-no-boot-anim',
        '-camera-back', 'none',
        '-camera-front', 'none',
        '-netdelay', 'none',
        '-netspeed', 'full'
    ) | Out-Null

    $Deadline = (Get-Date).AddMinutes(5)
    do {
        Start-Sleep -Seconds 2
        $Serial = (& $Adb devices | Select-String '^emulator-\d+\s+device').Line |
            ForEach-Object { ($_ -split '\s+')[0] } |
            Select-Object -First 1
        if ($Serial) {
            $Booted = ((& $Adb -s $Serial shell getprop sys.boot_completed 2>$null) -join '').Trim()
        }
    } while (($Booted -ne '1') -and ((Get-Date) -lt $Deadline))

    if ($Booted -ne '1') {
        throw 'The emulator did not finish booting within five minutes.'
    }
}

Write-Host "Emulator ready: $Serial"
& $Adb -s $Serial shell settings put global window_animation_scale 0
& $Adb -s $Serial shell settings put global transition_animation_scale 0
& $Adb -s $Serial shell settings put global animator_duration_scale 0

if (-not $SkipInstall) {
    $Apk = Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'FinReg-KnowledgeBase-*.apk' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $Apk) {
        throw "No FinReg APK was found in $PSScriptRoot"
    }
    Write-Host "Installing: $($Apk.Name)"
    & $Adb -s $Serial install -r -t $Apk.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "APK installation failed with exit code $LASTEXITCODE"
    }
}

& $Adb -s $Serial shell am force-stop com.finreg.knowledgebase
& $Adb -s $Serial shell am start -W -n com.finreg.knowledgebase/.MainActivity
if ($LASTEXITCODE -ne 0) {
    throw "APP launch failed with exit code $LASTEXITCODE"
}

Write-Host 'Financial regulation knowledge-base APP is open in the emulator.'
