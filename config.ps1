param(
    [string]$Config = "config.json"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if ([string]::IsNullOrEmpty($ScriptRoot)) {
    $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

if (-not [System.IO.Path]::IsPathRooted($Config)) {
    $ConfigPath = Join-Path $ScriptRoot $Config
} else {
    $ConfigPath = $Config
}
$ConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)

$tui = Join-Path $ScriptRoot "scripts\config_tui.py"
if (-not (Test-Path -LiteralPath $tui)) {
    throw "Missing $tui"
}

python $tui --config $ConfigPath
if ($LASTEXITCODE -eq 2) {
    Write-Host "正在安装 textual ..."
    python -m pip install textual
    if ($LASTEXITCODE -ne 0) {
        throw "pip install textual failed"
    }
    python $tui --config $ConfigPath
}
exit $LASTEXITCODE
