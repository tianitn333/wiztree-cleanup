---
name: wiztree-cleanup
description: 用本机 WizTree 命令行扫描磁盘、定位空间占用并生成可执行的清理方案。当用户说 C 盘满了、清理磁盘、释放空间、找出最占空间的大文件/文件夹、分析存储占用、清理缓存或重复文件时使用。
---

# WizTree 磁盘清理

把 WizTree 当成"数据采集器"：它负责几秒内扫出全盘文件清单（CSV），
本 skill 负责把 CSV 变成"删什么、删多少、风险多大"的决策清单。

**不需要 MCP**。WizTree 自带完整命令行（`/export` 导出 CSV），
比 MCP 更直接：一次扫描拿到全量数据，后续分析、复核都不必反复开 GUI。

## 前提

- 可执行文件：`scan.ps1` 会按 `-WizTreePath` → `$env:WIZTREE_PATH` →
  `Program Files` → `Program Files (x86)` → `%LOCALAPPDATA%\Programs` → 各盘浅层搜索 依次查找。
- 管理员权限决定扫描速度：**有管理员**走 NTFS MFT 直读，200 GB 盘通常 5–20 秒；
  **无管理员**走目录遍历，130 万文件约 35–60 秒。两者结果一致。
- 需要 UAC 提权的场景（`/admin=1`）必须由用户点确认，agent 不要假设能静默提权。

## 三步工作流

### 1. 扫描

```powershell
$skill = "<skill 目录>"   # 例如 $env:USERPROFILE\.claude\skills\wiztree-cleanup
& "$skill\scripts\scan.ps1" -Target "C:" -OutDir "D:\scans"
```

- 返回一行 JSON：`csv`、`elapsed`、`sizeMB`、`rows`、`capacityGB/freeGB/usedGB`、`error`。
- `-Admin 1` 强制提权（弹 UAC）；`-Admin 0` 强制非提权；不传则按当前会话是否提权自动决定。
- 只要某个目录：`-Target "C:\Users\me"`；太大太杂可加 `-FilterExclude "C:\Windows\|C:\Program Files\"`。
- 只关心大文件时加 `-Filter ">=500mb"`，CSV 会小很多、分析更快。

### 2. 分析

```powershell
python "$skill\scripts\analyze.py" "<scan.csv>" `
  --report "<out>\report.md" --json "<out>\summary.json" --folders "<out>\folders.tsv"
```

产出：
- `report.md`：结论速览 / 分类清理候选 / 最大文件 Top40 / 占用最大目录 / 类型分布 /
  重复文件 / 陈旧大文件 / 未归类大文件。
- `summary.json`：同数据的机器可读版，agent 用它做二次决策（不必重读 CSV）。
- `folders.tsv`：目录索引侧文件，供逐层下钻。
- 控制台打印 `safe=` / `care=` / `dup=` 三行总账，可先看这个。

常用参数：`--top 60`（更多大文件）、`--dup-min-mb 50`（更激进的重复检测）、
`--old-days 180 --old-min-mb 300`（找半年没动的大文件）。

### 3. 逐层下钻（推荐）

```powershell
python "$skill\scripts\drill.py" "<out>\folders.tsv" "C:\Users\me\AppData\Local" --top 15
python "$skill\scripts\drill.py" "<out>\folders.tsv" "C:\Users\me\AppData" --depth 2 --top 10
python "$skill\scripts\drill.py" "<out>\folders.tsv" "D:\" --files "<scan.csv>" --min-mb 200
```

报告只给到"类别"和"前 15 个目录"，真正决策通常要下钻 2–3 层：
`AppData` → `AppData\Local` → `AppData\Local\Google` → `...\User Data`。
`--files` 会把该目录下**直接存放**的大文件也列出来（不递归）。

### 4. 提方案（不要直接删）

把结果整理成三级清单交给用户确认：

1. **可直接清（安全）**：临时目录、浏览器/Electron 缓存、更新缓存、`$WinREAgent`、
   回收站、缩略图缓存、包管理器缓存、Python 字节码。
2. **需确认（谨慎）**：聊天记录/接收文件、`node_modules`、构建缓存、安装包/镜像、
   网盘下载、游戏本体、`C:\Windows\Installer`（**不要手删**）。
3. **不要动（系统）**：`WinSxS`（只能 `DISM /Online /Cleanup-Image /StartComponentCleanup`）、
   `System Volume Information`、`pagefile.sys` / `hiberfil.sys`（用 `powercfg /h off` 关休眠）。

每条都要给出：路径、大小、删了会怎样、怎么删（GUI 还是命令）。

## 安全红线

- **先报告、后删除**。任何删除动作前，把待删清单（路径 + 大小 + 合计）明确列出并得到确认。
- 优先用"系统自带且可回退"的手段：设置 → 存储 → 临时文件、磁盘清理、`powercfg /h off`、
  `DISM /StartComponentCleanup`、`npm cache clean --force`、`pip cache purge`。
- 手工删除时**只删目录内容，不删目录本身**（很多程序要求父目录存在）。
  正在被占用的文件跳过即可，不要强杀进程。
- 不碰：`C:\Windows`（除 Temp/Logs/SoftwareDistribution\Download）、`WinSxS`、
  `System Volume Information`、用户文档/桌面/图片、`*.vhdx`（WSL/Docker 的数据盘）。
- 重复文件必须**校验内容**（同名同大小 ≠ 同一文件）。抽查 `Get-FileHash -Algorithm SHA256`，
  确认后再删，且至少保留一份。
- 删完用 `Get-PSDrive C` 或再次扫描对比，报告"实际释放了多少"，不要只报预期。

## 常见坑

| 现象 | 原因 / 处理 |
|---|---|
| 命令跑完没生成 CSV，WizTree 窗口却弹出来了 | 参数写成了 `/path="C:"`。扫描目标是**位置参数**：`wiztree64.exe "C:" /export="x.csv"` |
| 脚本提前继续执行 | 没用 `start /wait`（cmd）或没等进程退出（PowerShell 用 `Start-Process -PassThru` + 轮询） |
| CSV 第一行是"生成由 WizTree ..." | banner 行，解析时跳过；文件是 UTF-8 BOM |
| 中文列名 / 英文列名不一致 | 中英文系统列名不同，按 `文件名称`/`File Name` 双关键字识别表头 |
| 目录行和文件行混在一起 | 目录路径以 `\` 结尾；目录大小是递归总和，可直接排序 |
| 统计偏大 | 硬链接的"分配"列有 `0` 前缀，不该重复计入占用 |
| `/admin=1` 没反应 | UAC 被拒或需要用户交互；退回 `-Admin 0` 慢扫 |
| 扫描后残留 WizTree 进程 | 超时被杀的正常现象；脚本会在超时后清理 |

## 参考资料

- `references/wiztree-cli.md`：完整命令行参数（过滤语法、树状图 PNG、按类型导出、MFT dump、CSV 列语义）。
- 官方文档：<https://diskanalyzer.com/guide>

## 输出约定

给用户的最终答复里至少包含：**当前可用空间 → 安全可清多少 → 谨慎可清多少 →
建议先做哪三件事**，并附上 report.md 的路径。数字要来自 `summary.json`，不要凭印象估。
