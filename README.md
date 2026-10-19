# wiztree-cleanup

> 让 AI agent 用 WizTree 命令行扫盘，并把结果变成"删什么、删多少、风险多大"的清理方案。

WizTree 是 Windows 上最快的磁盘占用分析器（直接读 NTFS MFT，200 GB 盘几秒扫完）。
它自带命令行导出（`/export`），所以**不需要 MCP**：一次扫描拿到全量 CSV，
之后的分析、复核、下钻都在数据上做，不用反复点 GUI。

这个仓库是一个 [Claude Code / Codex](https://docs.claude.com/en/docs/claude-code) 技能：
`SKILL.md` 是给 agent 的指令，`scripts/` 是可独立运行的工具。

## 它能做什么

```powershell
# 1. 扫描（返回 JSON：耗时、行数、容量、CSV 路径）
.\scripts\scan.ps1 -Target "C:" -OutDir D:\scans

# 2. 分析（Markdown 报告 + JSON 摘要 + 目录索引）
python .\scripts\analyze.py D:\scans\C_.csv `
  --report D:\reports\report.md --json D:\reports\summary.json --folders D:\reports\folders.tsv

# 3. 逐层下钻（"到底谁吃了空间"）
python .\scripts\drill.py D:\reports\folders.tsv "C:\Users\me\AppData\Local" --top 15
```

报告包含：结论速览（安全 / 谨慎 / 勿动三级）· 分类清理候选 · 最大文件 Top40 ·
占用最大目录（顶层 + 深层热点）· 文件类型分布 · 同名同大小重复文件 · 长期未动的大文件 ·
未归类的大文件。

## 安装

```powershell
git clone https://github.com/<owner>/wiztree-cleanup.git "$env:USERPROFILE\.claude\skills\wiztree-cleanup"
```

- Claude Code：`~/.claude/skills/`
- Codex：`~/.codex/skills/`

下次开会话即可在技能列表里看到 `wiztree-cleanup`。

依赖：WizTree 4.x（`WizTree64.exe`）、PowerShell 5.1+、Python 3.8+（只用标准库）。

WizTree 位置自动探测，找不到时：

```powershell
$env:WIZTREE_PATH = "D:\apps\WizTree\WizTree64.exe"
# 或 .\scripts\scan.ps1 -Target "C:" -WizTreePath "D:\apps\WizTree\WizTree64.exe"
```

## 实测数据

| 场景 | 权限 | 规模 | 耗时 |
|---|---|---|---|
| 整盘 C: | 非管理员（目录遍历） | 132 万文件 / 3336 目录 | 36.5 s |
| 分析 CSV | — | 165 万行 / 256 MB | 68 s |

## GUI 还是命令行？

两者用的是同一个扫描引擎，**速度几乎一样**，差别在交互方式：

| | GUI | 命令行 |
|---|---|---|
| 人自己看 | ✅ 树状图、右键、搜索框、拖拽 | ❌ 需要先导出再看 |
| agent 使用 | ❌ 无法脚本化，只能看截图或手动点 | ✅ 一次导出，全量可查 |
| 可复现 | ❌ 每次手点 | ✅ 同一条命令 |
| 批量/定时 | ❌ | ✅ 多盘、多目录、任务计划 |
| 排序/聚合 | 点列头，单维度 | ✅ 任意维度组合（按类型/时间/目录） |
| 下钻 20 层 | 展开到眼花 | ✅ `drill.py` 一条命令 |

结论：**人想"自己感觉一下"用 GUI，想让 agent 干活用 CLI。** 最好的组合是
CLI 扫 + 分析出候选清单，人再用 GUI 确认一两个存疑的大目录。
需要图示时 CLI 也能直接出图：

```powershell
WizTree64.exe C: /treemapimagefile="D:\treemap_%d.png" /treemapimagewidth=1920 /treemapimageheight=1080
```

## 安全

这个工具**只读**：扫描和分析不会删除任何文件。

但它产出的"清理候选"会被 agent 用来执行删除，所以 `SKILL.md` 里写死了几条红线：
先报告后删除、只删目录内容不删目录本身、`WinSxS` 只走 DISM、
`C:\Windows\Installer` 不手删、重复文件必须校验哈希、删完核实实际释放量。

## 常见坑

- **没有 `/path=` 参数**。目标是位置参数：`wiztree64.exe "C:" /export="x.csv"`。
  写成 `/path="C:"` 只会弹出 GUI 且不产出文件。
- CSV 第一行是 `生成由 WizTree ...` 的 banner，是 UTF-8 BOM，解析要跳过。
- 中文系统列名是中文（`文件名称,大小,分配,修改时间,属性,文件,文件夹`），英文系统是
  `File Name,Size,Allocated,...`，解析要同时兼容。
- 目录行以 `\` 结尾，其"大小"是递归总和，可直接排序，不用自己累加。
- 硬链接文件"分配"列带 `0` 前缀，统计时不要重复计入。

## License

MIT
