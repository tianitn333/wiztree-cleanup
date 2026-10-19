# WizTree 命令行扫描 -> CSV
#
# 用法:
#   .\scan.ps1 -Target "C:"                      # 扫整个 C:（非管理员，较慢但可用）
#   .\scan.ps1 -Target "C:" -Admin 1             # 管理员 MFT 模式，快 5-10 倍（会弹 UAC）
#   .\scan.ps1 -Target "D:\Games" -OutDir D:\scans
#
# WizTree 位置探测顺序: -WizTreePath > $env:WIZTREE_PATH > 常见安装目录 > 各盘浅层搜索
# 输出: stdout 打印一行 JSON，便于 agent / 脚本解析。
param(
    [Parameter(Mandatory = $true)][string]$Target,
    [string]$OutDir = "",
    [string]$Name = "",
    [int]$Admin = -1,
    [string]$Filter = "",
    [string]$FilterExclude = "",
    [int]$SortBy = -1,
    [int]$TimeoutSec = 3600,
    [string]$WizTreePath = ""
)

$ErrorActionPreference = "Stop"

function Find-WizTree {
    param([string]$Hint)
    $cands = @(
        $Hint,
        $env:WIZTREE_PATH,
        "$env:ProgramFiles\WizTree\WizTree64.exe",
        "${env:ProgramFiles(x86)}\WizTree\WizTree64.exe",
        "$env:LOCALAPPDATA\Programs\WizTree\WizTree64.exe"
    )
    foreach ($c in $cands) { if ($c -and (Test-Path -LiteralPath $c)) { return (Resolve-Path -LiteralPath $c).Path } }

    # 便携版：在各固定盘符浅层找（跳过 Windows 目录，避免深递归）
    $drives = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object -ExpandProperty DeviceID
    foreach ($d in $drives) {
        $hit = Get-ChildItem -LiteralPath "$d\" -Filter "WizTree64.exe" -Recurse -Depth 3 `
            -ErrorAction SilentlyContinue -Force |
            Where-Object { $_.FullName -notmatch '\\Windows\\' } | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    throw "找不到 WizTree64.exe。请用 -WizTreePath 指定，或设置环境变量 WIZTREE_PATH"
}

$exe = Find-WizTree -Hint $WizTreePath
if (-not $OutDir) { $OutDir = Join-Path (Get-Location).Path "scans" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not $Name) {
    $safe = ($Target -replace '[\\/:*?"<>|\s]+', '_').Trim('_')
    $Name = "{0}_{1}" -f $safe, (Get-Date -Format "yyyyMMdd_HHmmss")
}
$csvPath = Join-Path (Resolve-Path -LiteralPath $OutDir).Path "$Name.csv"
if (Test-Path -LiteralPath $csvPath) { Remove-Item -LiteralPath $csvPath -Force }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Admin -lt 0) { $Admin = if ($isAdmin) { 1 } else { 0 } }

$argList = @($Target, "/export=`"$csvPath`"", "/admin=$Admin")
if ($Filter) { $argList += "/filter=`"$Filter`"" }
if ($FilterExclude) { $argList += "/filterexclude=`"$FilterExclude`"" }
if ($SortBy -ge 0) { $argList += "/sortby=$SortBy" }

$sw = [Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $exe -ArgumentList $argList -PassThru
while (-not $proc.HasExited -and $sw.Elapsed.TotalSeconds -lt $TimeoutSec) { Start-Sleep -Milliseconds 400 }
$sw.Stop()

$timedOut = -not $proc.HasExited
if ($timedOut) {
    try { $proc.Kill() } catch { }
    Start-Sleep -Milliseconds 500
    Get-Process WizTree64, WizTree -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

$result = [ordered]@{
    target     = $Target
    exe        = $exe
    csv        = $csvPath
    admin      = ($Admin -eq 1)
    elevated   = $isAdmin
    elapsed    = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    timedOut   = $timedOut
    exists     = $false
    sizeMB     = 0
    rows       = 0
    capacityGB = $null
    freeGB     = $null
    usedGB     = $null
    error      = $null
}

if (Test-Path -LiteralPath $csvPath) {
    $f = Get-Item -LiteralPath $csvPath
    $result.exists = $true
    $result.sizeMB = [math]::Round($f.Length / 1MB, 1)

    $reader = [System.IO.File]::OpenText($csvPath)
    $dataRows = 0
    try {
        while (($line = $reader.ReadLine()) -ne $null) {
            if ($line.StartsWith('"')) { $dataRows++ }
        }
    } finally { $reader.Close() }
    $result.rows = $dataRows

    $head = Get-Content -LiteralPath $csvPath -TotalCount 3 -Encoding UTF8
    if ($head.Count -ge 3 -and $head[2] -match ',(\d+),(\d+),(\d+),(\d+)\s*$') {
        $result.capacityGB = [math]::Round([double]$Matches[1] / 1GB, 1)
        $result.freeGB = [math]::Round([double]$Matches[2] / 1GB, 1)
        $result.usedGB = [math]::Round([double]$Matches[3] / 1GB, 1)
    }
} else {
    $result.error = "no csv produced (timeout or bad parameters)"
}

$result | ConvertTo-Json -Compress
