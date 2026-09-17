param(
    [ValidateSet("all", "inventory", "rip", "import", "build")]
    [string]$Stage = "all",
    [string]$Config = "config.json"
)

$ErrorActionPreference = "Stop"

try {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
} catch {}

$ScriptRoot = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptRoot)) {
    $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$StageOrder = @("inventory", "rip", "import", "build")

function Resolve-RepoPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $ScriptRoot $Path))
}

function Get-ToolCommand {
    param([Parameter(Mandatory = $true)][string]$Value)
    if ($Value -match '[\\/]' -or [System.IO.Path]::IsPathRooted($Value)) {
        return (Resolve-RepoPath $Value)
    }
    return $Value
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [object[]]$ArgumentList = @()
    )
    $argArray = @($ArgumentList)
    & $FilePath @argArray
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $FilePath $($argArray -join ' ')"
    }
}

function Get-StageMarkerPath {
    param([Parameter(Mandatory = $true)][string]$Name)
    return (Join-Path $WorkDir ".stage-$Name.ok")
}

function Clear-StageMarkersFrom {
    param([Parameter(Mandatory = $true)][string]$Name)
    $idx = [Array]::IndexOf($StageOrder, $Name)
    if ($idx -lt 0) { throw "Unknown stage '$Name'" }
    for ($i = $idx; $i -lt $StageOrder.Count; $i++) {
        $marker = Get-StageMarkerPath $StageOrder[$i]
        if (Test-Path -LiteralPath $marker) {
            Remove-Item -LiteralPath $marker -Force
        }
    }
}

function Write-StageMarker {
    param([Parameter(Mandatory = $true)][string]$Name)
    Set-Content -LiteralPath (Get-StageMarkerPath $Name) -Value ((Get-Date).ToString("o")) -Encoding UTF8
}

function Invoke-Stage {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Body
    )
    Clear-StageMarkersFrom $Name
    Write-Host "STAGE $Name"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $ok = $false
    try {
        & $Body
        $ok = $true
    } finally {
        $sw.Stop()
        Write-Host ("STAGE {0} duration: {1:N2}s" -f $Name, $sw.Elapsed.TotalSeconds)
    }
    if ($ok) {
        Write-StageMarker $Name
    }
}

if (-not [System.IO.Path]::IsPathRooted($Config)) {
    $ConfigPath = Join-Path $ScriptRoot $Config
} else {
    $ConfigPath = $Config
}
$ConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Config not found: $ConfigPath"
}

$cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json

$Python = Get-ToolCommand ([string]$cfg.python)
$UnityEditor = Get-ToolCommand ([string]$cfg.unityEditor)
$AssetRipper = Get-ToolCommand ([string]$cfg.assetRipper)
$ConfiguredUnityVersion = [string]$cfg.unityVersion
$InputDir = Resolve-RepoPath ([string]$cfg.inputDir)
$WorkDir = Resolve-RepoPath ([string]$cfg.workDir)
$OutputDir = Resolve-RepoPath ([string]$cfg.outputDir)
$AndroidTexture = [string]$cfg.androidTexture
$Compression = [string]$cfg.compression
$AstcBlockSize = if ($null -ne $cfg.astcBlockSize) { [string]$cfg.astcBlockSize } else { "8x8" }
$MaxTextureSize = if ($null -ne $cfg.maxTextureSize) { [string]$cfg.maxTextureSize } else { "0" }
$RipperArgs = @()
if ($null -ne $cfg.assetRipperArgs) {
    $RipperArgs = @($cfg.assetRipperArgs)
}

$InventoryDir = Join-Path $WorkDir "inventory"
$RippedDir = Join-Path $WorkDir "ripped"
$UnityProject = Join-Path $WorkDir "unity-project"
$MappingPath = Join-Path $InventoryDir "mapping.json"
$UnityLog = Join-Path $WorkDir "unity-build.log"
$InventoryPy = Join-Path $ScriptRoot "scripts\inventory.py"
$RipAssetRipperPy = Join-Path $ScriptRoot "scripts\rip_assetripper.py"
$CopyIntoUnityPy = Join-Path $ScriptRoot "scripts\copy_into_unity.py"

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

function Invoke-InventoryStage {
    if (-not (Test-Path -LiteralPath $InventoryPy)) {
        throw "Missing inventory script: $InventoryPy"
    }
    if (-not (Test-Path -LiteralPath $InputDir)) {
        throw "Input directory not found: $InputDir"
    }
    if (Test-Path -LiteralPath $InventoryDir) {
        Remove-Item -LiteralPath $InventoryDir -Recurse -Force
    }
    Invoke-Native -FilePath $Python -ArgumentList @($InventoryPy, "--input", $InputDir, "--out", $InventoryDir)
    if (Test-Path -LiteralPath $MappingPath) {
        $mapping = Get-Content -LiteralPath $MappingPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $detected = [string]$mapping.unityVersion
        $configured = [string]$cfg.unityVersion
        if ($detected -and $configured -and $detected -ne $configured) {
            Write-Warning "Detected Unity $detected from AssetBundles, but config.unityVersion is $configured. Align the Editor version before building."
        }
    }
}

function Invoke-RipStage {
    if (-not (Test-Path -LiteralPath $RipAssetRipperPy)) {
        throw "Missing rip script: $RipAssetRipperPy"
    }
    if (-not (Test-Path -LiteralPath $AssetRipper)) {
        throw "AssetRipper not found at '$AssetRipper'. Download AssetRipper into tools/AssetRipper and set config.assetRipper."
    }
    if (-not (Test-Path -LiteralPath $InputDir)) {
        throw "Input directory not found: $InputDir"
    }

    $ripInputRoot = Join-Path $WorkDir "rip-input"

    # 隔离转换：按 mapping.json 里的 bundle 清单逐个 rip 到 work/ripped/<bundle>/，
    # 避免同名资源在合并目录里被 AssetRipper 重命名（_0/_1）导致覆盖。
    $bundles = $null
    if (Test-Path -LiteralPath $MappingPath) {
        $mapping = Get-Content -LiteralPath $MappingPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $bundles = $mapping.bundles
    }

    if ($null -ne $bundles -and @($bundles.PSObject.Properties).Count -gt 0) {
        if (Test-Path -LiteralPath $RippedDir) {
            Remove-Item -LiteralPath $RippedDir -Recurse -Force
        }
        if (Test-Path -LiteralPath $ripInputRoot) {
            Remove-Item -LiteralPath $ripInputRoot -Recurse -Force
        }
        $ripCount = 0
        foreach ($prop in @($bundles.PSObject.Properties)) {
            $bundleName = [string]$prop.Name
            $rel = [string]$prop.Value.file
            if ([string]::IsNullOrEmpty($rel)) {
                Write-Warning "Bundle '$bundleName' has no file path in mapping; skipping rip."
                continue
            }
            $relWin = $rel -replace '/', '\'
            $srcFile = Join-Path $InputDir $relWin
            if (-not (Test-Path -LiteralPath $srcFile)) {
                Write-Warning "Bundle source not found: $srcFile"
                continue
            }
            $tmpDir = Join-Path $ripInputRoot $bundleName
            New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
            Copy-Item -LiteralPath $srcFile -Destination $tmpDir -Force
            $srcManifest = "$srcFile.manifest"
            if (Test-Path -LiteralPath $srcManifest) {
                Copy-Item -LiteralPath $srcManifest -Destination $tmpDir -Force
            }
            $outDir = Join-Path $RippedDir $bundleName
            $ripArgs = @(
                $RipAssetRipperPy,
                "--ripper", $AssetRipper,
                "--input", $tmpDir,
                "--output", $outDir
            )
            if ($RipperArgs.Count -gt 0) {
                $ripArgs += "--"
                $ripArgs += $RipperArgs
            }
            Write-Host "RIP bundle '$bundleName'"
            Invoke-Native -FilePath $Python -ArgumentList $ripArgs
            $ripCount++
        }
        if (Test-Path -LiteralPath $ripInputRoot) {
            Remove-Item -LiteralPath $ripInputRoot -Recurse -Force
        }
        Write-Host "Ripped $ripCount bundle(s) in isolation."
        if ($ripCount -eq 0) {
            throw "No bundles were ripped."
        }
    } else {
        Write-Warning "mapping.json not found or has no bundles; ripping the whole input directory as a single project."
        $ripArgs = @(
            $RipAssetRipperPy,
            "--ripper", $AssetRipper,
            "--input", $InputDir,
            "--output", $RippedDir
        )
        if ($RipperArgs.Count -gt 0) {
            $ripArgs += "--"
            $ripArgs += $RipperArgs
        }
        Invoke-Native -FilePath $Python -ArgumentList $ripArgs
    }

    $hasAssets = $false
    if (Test-Path -LiteralPath $RippedDir) {
        $assetDirs = @(Get-ChildItem -LiteralPath $RippedDir -Recurse -Directory -Filter "Assets" -ErrorAction SilentlyContinue)
        $hasAssets = $assetDirs.Count -gt 0
    }
    if (-not $hasAssets) {
        throw "Ripped export has no Assets folder under '$RippedDir'."
    }
}

function Invoke-ImportStage {
    if (-not (Test-Path -LiteralPath $CopyIntoUnityPy)) {
        throw "Missing import script: $CopyIntoUnityPy"
    }
    Invoke-Native -FilePath $Python -ArgumentList @($CopyIntoUnityPy, "--config", $ConfigPath)
}

function Resolve-UnityEditorPath {
    param([string]$Configured, [string]$Version)
    if ($Configured -and (Test-Path -LiteralPath $Configured)) {
        return $Configured
    }
    $hub = "C:\Program Files\Unity\Hub\Editor"
    if ($Version) {
        $exact = Join-Path $hub "$Version\Editor\Unity.exe"
        if (Test-Path -LiteralPath $exact) {
            if ($Configured) {
                Write-Warning "config.unityEditor not found at '$Configured'; using $exact"
            }
            return $exact
        }
    }
    if (Test-Path -LiteralPath $hub) {
        $found = Get-ChildItem -LiteralPath $hub -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "Editor\Unity.exe" } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
        if ($found) {
            Write-Warning "config.unityEditor not found at '$Configured'; using $found"
            return $found
        }
    }
    return $Configured
}

function Invoke-BuildStage {
    if (-not (Test-Path -LiteralPath $MappingPath)) {
        throw "mapping.json not found at '$MappingPath'. Run inventory stage first."
    }
    if (-not (Test-Path -LiteralPath $UnityProject)) {
        throw "Unity project not found at '$UnityProject'. Run import stage first."
    }
    $script:UnityEditor = Resolve-UnityEditorPath -Configured $UnityEditor -Version $ConfiguredUnityVersion
    if (-not (Test-Path -LiteralPath $UnityEditor)) {
        throw "Unity Editor not found at '$UnityEditor'. Set config.unityEditor to your Unity.exe (Android module required)."
    }
    $unityArgs = @(
        "-batchmode",
        "-nographics",
        "-quit",
        "-projectPath", $UnityProject,
        "-buildTarget", "Android",
        "-executeMethod", "AbRebuild.BuildAndroid",
        "-logFile", $UnityLog,
        "-mapping", $MappingPath,
        "-output", $OutputDir,
        "-androidTexture", $AndroidTexture,
        "-astcBlockSize", $AstcBlockSize,
        "-maxTextureSize", $MaxTextureSize,
        "-compression", $Compression
    )
    if (Test-Path -LiteralPath $UnityLog) {
        Remove-Item -LiteralPath $UnityLog -Force
    }
    $unityArgLine = ($unityArgs | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join ' '
    $unityProc = Start-Process -FilePath $UnityEditor -ArgumentList $unityArgLine -Wait -PassThru
    $unityExit = $unityProc.ExitCode
    if ($null -eq $unityExit) { $unityExit = 0 }
    if ($unityExit -ne 0) {
        if (Test-Path -LiteralPath $UnityLog) {
            Write-Host "----- last 80 lines of $UnityLog -----"
            Get-Content -LiteralPath $UnityLog -Tail 80 | ForEach-Object { Write-Host $_ }
        }
        throw "Unity build failed with exit code $unityExit"
    }

    $outFiles = @(Get-ChildItem -LiteralPath $OutputDir -Recurse -File -ErrorAction SilentlyContinue)
    $nonEmpty = @($outFiles | Where-Object { $_.Length -gt 0 })
    if ($nonEmpty.Count -eq 0) {
        Write-Warning "Output directory has no files: $OutputDir"
    }

    Invoke-RelocateAndroidOutput -MappingPath $MappingPath -OutputDir $OutputDir
}

function Invoke-RelocateAndroidOutput {
    param(
        [Parameter(Mandatory = $true)][string]$MappingPath,
        [Parameter(Mandatory = $true)][string]$OutputDir
    )
    if (-not (Test-Path -LiteralPath $MappingPath)) {
        Write-Warning "mapping.json not found at '$MappingPath'; skipping output relocation."
        return
    }
    $mapping = Get-Content -LiteralPath $MappingPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $inputRoot = [string]$mapping.inputDir
    $bundles = $mapping.bundles
    if ($null -eq $bundles) {
        Write-Warning "mapping.json has no bundles; skipping output relocation."
        return
    }
    if ([string]::IsNullOrEmpty($inputRoot)) {
        Write-Warning "mapping.json has no inputDir; cannot place results next to sources."
        return
    }
    $moved = 0
    foreach ($prop in $bundles.PSObject.Properties) {
        $key = [string]$prop.Name
        $bundle = $prop.Value
        $rel = [string]$bundle.file
        if ([string]::IsNullOrEmpty($rel)) {
            Write-Warning "Bundle '$key' has no file path in mapping; skipping."
            continue
        }
        $relWin = $rel -replace '/', '\'
        $dest = (Join-Path $inputRoot $relWin) + ".android"
        $destDir = Split-Path -Parent $dest
        if ($destDir -and -not (Test-Path -LiteralPath $destDir)) {
            New-Item -ItemType Directory -Force -Path $destDir | Out-Null
        }
        $src = Join-Path $OutputDir $key
        if (Test-Path -LiteralPath $src) {
            Move-Item -LiteralPath $src -Destination $dest -Force
            $moved++
            $srcManifest = "$src.manifest"
            $destManifest = "$dest.manifest"
            if (Test-Path -LiteralPath $srcManifest) {
                Move-Item -LiteralPath $srcManifest -Destination $destManifest -Force
            }
        } else {
            Write-Warning "Built bundle not found at '$src' for mapping entry '$key'."
        }
    }
    Write-Host "Placed $moved Android bundles next to their sources (suffix .android)"
}

try {
    $runAll = $Stage -eq "all"
    if ($runAll -or $Stage -eq "inventory") {
        Invoke-Stage "inventory" { Invoke-InventoryStage }
    }
    if ($runAll -or $Stage -eq "rip") {
        Invoke-Stage "rip" { Invoke-RipStage }
    }
    if ($runAll -or $Stage -eq "import") {
        Invoke-Stage "import" { Invoke-ImportStage }
    }
    if ($runAll -or $Stage -eq "build") {
        Invoke-Stage "build" { Invoke-BuildStage }
    }
    Write-Host "DONE $Stage"
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
}
