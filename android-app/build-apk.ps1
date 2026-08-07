param(
    [switch]$SkipToolDownload,
    [ValidateSet('Online', 'Offline', 'Both')]
    [string]$Edition = 'Online',
    [string]$KnowledgeManifest = '',
    [string]$KnowledgePackage = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$ToolRoot = Join-Path $env:LOCALAPPDATA 'FinRegAndroidBuild'
$DownloadRoot = Join-Path $ToolRoot 'downloads'
$JdkRoot = Join-Path $ToolRoot 'jdk17'
$SdkRoot = Join-Path $ToolRoot 'android-sdk'
$GradleRoot = Join-Path $ToolRoot 'gradle-8.9'

function Get-PortableZip([string]$Url, [string]$ZipPath, [string]$Destination) {
    if (Test-Path $Destination) { return }
    if ($SkipToolDownload) { throw "Missing build tool: $Destination" }
    New-Item -ItemType Directory -Force $DownloadRoot | Out-Null
    if (-not (Test-Path $ZipPath)) {
        Write-Host "Downloading $Url"
        & curl.exe -L --fail --retry 3 -o $ZipPath $Url
        if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
    } else {
        Write-Host "Resuming/checking $Url"
        & curl.exe -L --fail --retry 3 -C - -o $ZipPath $Url
        if ($LASTEXITCODE -ne 0) {
            Remove-Item -LiteralPath $ZipPath -Force
            & curl.exe -L --fail --retry 3 -o $ZipPath $Url
            if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
        }
    }
    $Temporary = "$Destination.extracting"
    Remove-Item -LiteralPath $Temporary -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $Temporary | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $Temporary -Force
    $Children = @(Get-ChildItem $Temporary)
    if ($Children.Count -eq 1 -and $Children[0].PSIsContainer) {
        Move-Item -LiteralPath $Children[0].FullName -Destination $Destination
        Remove-Item -LiteralPath $Temporary -Force
    } else {
        Move-Item -LiteralPath $Temporary -Destination $Destination
    }
}

New-Item -ItemType Directory -Force $ToolRoot | Out-Null
Get-PortableZip `
    'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse?project=jdk' `
    (Join-Path $DownloadRoot 'jdk17.zip') $JdkRoot
Get-PortableZip `
    'https://services.gradle.org/distributions/gradle-8.9-bin.zip' `
    (Join-Path $DownloadRoot 'gradle-8.9-bin.zip') $GradleRoot

$env:JAVA_HOME = $JdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot
$env:Path = "$JdkRoot\bin;$SdkRoot\platform-tools;$env:Path"

$SdkManager = Join-Path $SdkRoot 'cmdline-tools\latest\bin\sdkmanager.bat'
if (-not (Test-Path $SdkManager)) {
    if ($SkipToolDownload) { throw "Missing Android SDK command-line tools: $SdkManager" }
    $SdkZip = Join-Path $DownloadRoot 'android-commandline-tools.zip'
    if (-not (Test-Path $SdkZip)) {
        Write-Host 'Downloading Android SDK command-line tools'
        & curl.exe -L --fail --retry 3 -o $SdkZip 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip'
        if ($LASTEXITCODE -ne 0) { throw 'Android SDK command-line tools download failed' }
    }
    $SdkTemp = Join-Path $ToolRoot 'sdk-extracting'
    Remove-Item -LiteralPath $SdkTemp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -LiteralPath $SdkZip -DestinationPath $SdkTemp -Force
    $Latest = Join-Path $SdkRoot 'cmdline-tools\latest'
    New-Item -ItemType Directory -Force (Split-Path $Latest) | Out-Null
    Move-Item -LiteralPath (Join-Path $SdkTemp 'cmdline-tools') -Destination $Latest
    Remove-Item -LiteralPath $SdkTemp -Recurse -Force
}

if (-not (Test-Path (Join-Path $SdkRoot 'platforms\android-35\android.jar'))) {
    Write-Host 'Installing Android Platform 35 and Build Tools 35.0.0'
    1..30 | ForEach-Object { 'y' } | & $SdkManager --licenses --sdk_root=$SdkRoot | Out-Host
    & $SdkManager --sdk_root=$SdkRoot 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'
    if ($LASTEXITCODE -ne 0) { throw "Android SDK installation failed: $LASTEXITCODE" }
}

$LocalProperties = "sdk.dir=$($SdkRoot.Replace('\', '\\'))`r`n"
Set-Content -LiteralPath (Join-Path $ProjectRoot 'local.properties') -Value $LocalProperties -Encoding ASCII

$Gradle = Join-Path $GradleRoot 'bin\gradle.bat'
Push-Location $ProjectRoot
try {
    # Large asset replacements can leave padding in an incremental APK. A clean package
    # keeps the distributable close to the actual compressed resource size.
    & $Gradle --no-daemon clean
    if ($LASTEXITCODE -ne 0) { throw "APK build failed: $LASTEXITCODE" }

    if ($Edition -in @('Offline', 'Both')) {
        if (-not $KnowledgeManifest) {
            $KnowledgeManifest = Join-Path (Split-Path $ProjectRoot) 'deployment\update\latest.json'
        }
        if (-not (Test-Path -LiteralPath $KnowledgeManifest)) {
            throw "Knowledge manifest not found: $KnowledgeManifest"
        }
        $Manifest = [IO.File]::ReadAllText(
            (Resolve-Path -LiteralPath $KnowledgeManifest).Path,
            [Text.Encoding]::UTF8
        ) | ConvertFrom-Json
        if (-not $KnowledgePackage) {
            $KnowledgePackage = Join-Path (Split-Path $ProjectRoot) "deployment\dist\knowledge-package-$($Manifest.version).zip"
        }
        if (-not (Test-Path -LiteralPath $KnowledgePackage)) {
            throw "Knowledge package not found: $KnowledgePackage"
        }
        $ActualSize = (Get-Item -LiteralPath $KnowledgePackage).Length
        $ActualHash = (Get-FileHash -LiteralPath $KnowledgePackage -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($ActualSize -ne [long]$Manifest.package_size -or $ActualHash -ne $Manifest.sha256) {
            throw 'Knowledge package does not match the manifest size/SHA-256'
        }
        $GeneratedAssets = Join-Path $ProjectRoot 'app\build\generated\offlineAssets'
        New-Item -ItemType Directory -Force $GeneratedAssets | Out-Null
        Copy-Item -LiteralPath $KnowledgeManifest -Destination (Join-Path $GeneratedAssets 'offline-manifest.json') -Force
        Copy-Item -LiteralPath $KnowledgePackage -Destination (Join-Path $GeneratedAssets 'knowledge-package.zip') -Force
    }

    $Tasks = @()
    if ($Edition -in @('Online', 'Both')) { $Tasks += 'assembleOnlineDebug' }
    if ($Edition -in @('Offline', 'Both')) { $Tasks += 'assembleOfflineDebug' }
    & $Gradle --no-daemon @Tasks
    if ($LASTEXITCODE -ne 0) { throw "APK build failed: $LASTEXITCODE" }
} finally {
    Pop-Location
}

if ($Edition -in @('Online', 'Both')) {
    $BuiltApk = Join-Path $ProjectRoot 'app\build\outputs\apk\online\debug\app-online-debug.apk'
    if (-not (Test-Path $BuiltApk)) { throw 'Online APK was not found after the build' }
    $OutputApk = Join-Path $ProjectRoot 'FinReg-KnowledgeBase-Online-v1.8.0.apk'
    Copy-Item -LiteralPath $BuiltApk -Destination $OutputApk -Force
    Write-Host "Build complete: $OutputApk"
}
if ($Edition -in @('Offline', 'Both')) {
    $BuiltApk = Join-Path $ProjectRoot 'app\build\outputs\apk\offline\debug\app-offline-debug.apk'
    if (-not (Test-Path $BuiltApk)) { throw 'Offline APK was not found after the build' }
    $OutputApk = Join-Path $ProjectRoot "FinReg-KnowledgeBase-Offline-KB$($Manifest.version)-v1.8.0.apk"
    Copy-Item -LiteralPath $BuiltApk -Destination $OutputApk -Force
    Write-Host "Build complete: $OutputApk"
}
