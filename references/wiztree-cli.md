# WizTree 命令行参数速查（Windows，v4.x）

来源：<https://diskanalyzer.com/guide>（"Command Line CSV Export" 等章节）。以下为实测/官方语义整理。

## 基本形式

```bat
wiztree64.exe "扫描目标" /export="输出.csv" [选项]
```

- 扫描目标是**位置参数**，必须加引号：`"C:"`、`"D:\Games"`、`"C:\Users\13666"`。
  ⚠️ 没有 `/path=` 这个参数；写成 `/path="C:"` 会被忽略，WizTree 只会打开 GUI 且不产出文件。
- `/export` 输出 CSV；文件名支持 `%d`（YYYYMMDD）与 `%t`（HHMMSS）。
  - 在 `.bat/.cmd` 里要写成 `%%d` / `%%t`。PowerShell 直接传 `%d` 即可。
- 必须用 `start /wait`（cmd）或 `Start-Process -PassThru` + 轮询（PowerShell）等它结束，
  否则脚本会在 CSV 写完前继续执行。
- 32 位的 `wiztree.exe` 在 64 位系统上会自动转交 `wiztree64.exe` 并立即返回——
  所以 PowerShell 里一律直接调用 `WizTree64.exe`。

## 扫描与权限

| 参数 | 说明 |
|---|---|
| `/admin=1` | 申请以管理员身份运行。NTFS 卷可走 MFT 直读，整盘扫描通常几秒到几十秒；不加则为目录遍历，慢 5–10 倍 |
| `/admin=0` | 强制非管理员模式 |

非管理员也能扫整盘，只是慢；对单个目录（如 `C:\Users\xxx`）差别不大。

## 导出内容控制

| 参数 | 说明 |
|---|---|
| `/exportfolders=0` | 不导出目录行（只要文件清单时用，CSV 体积可减半） |
| `/exportfiles=0` | 不导出文件行 |
| `/sortby=0..3` | 0 名称（默认）｜1 大小降序｜2 分配大小降序｜3 修改时间降序 |
| `/exportmaxdepth=n` | 只导出 n 层以内的目录，0 = 不限 |
| `/exportfiletypes="out.csv"` | 额外导出一份"按扩展名汇总"的 CSV，可与 `/export` 同时用 |
| `/exportalldates=1` | 增加"最后访问时间""创建时间" |
| `/exportallsizes=1` | 目录行增加"不含子目录"的自身大小 |
| `/exportsplitfilename=1` | 路径拆成 root/folder/name/ext 四列 |
| `/exportmftrecno=1` | 增加 MFT 记录号（文件唯一 ID，仅管理员扫整盘时有效） |
| `/exportpercentofparent=1` | 增加"占父目录百分比"列 |
| `/exportUTCTime=1` | 时间用 UTC 而非本地时间 |

## 过滤（在扫描/导出阶段就裁掉无关文件）

```bat
/filter="*.mp3|*.wav"                     :: 只导出匹配项，| 表示 OR
/filterexclude="D:\Temp\"                 :: 排除
/filterfullpath=0                         :: 只匹配文件名（默认匹配全路径）
/filter="'C:\Program Files\'|'C:\Program Files (x86)\'"   :: 含空格的路径用单引号包住
```

过滤语法与 GUI 搜索框一致：支持通配符、`>500mb`、`<2020/01/01`、`>=today-30`、
`namelen>200`、`pathlen>200`、`a=0`（分配大小）、`=file` / `=folder`、`!` 取反、
`/[0-9]{4}-[0-9]{2}-[0-9]{2}\.csv$`（正则）。

## CSV 格式

```
生成由 WizTree 4.31 2026/10/19 19:49:41 (...)      ← 第 1 行 banner
文件名称,大小,分配,修改时间,属性,文件,文件夹,DRIVECAPACITY,FREESPACE,USEDSPACE,RESERVEDSPACE
"C:\",191613404635,191317676032,2106/02/07 14:28:15,0,1321565,333656,213603315712,14276714496,199326601216,0
"C:\Windows\",...                                   ← 目录行以反斜杠结尾
"C:\Windows\notepad.exe",...,...,0,0                ← 文件行，后两列为 0
```

- 编码：UTF-8（带 BOM），首行是生成器 banner，解析时要跳过。
- 中文系统列名是中文；英文系统是 `File Name,Size,Allocated,Modified,Attributes,Files,Folders`。
- 目录行的"大小"是其**递归总和**，可直接用来排序目录，不必自己累加。
- 文件行末尾可能只有 7 列；前几行可能多出 DRIVECAPACITY 等列，解析要按列数容错。
- 硬链接文件的"分配大小"前面会带一个 `0` 前缀，表示不额外占空间，统计时别重复计入。

## 其他命令行能力

```bat
:: 文件名/大小/时间筛选（不用自己写脚本，导出阶段就筛）
wiztree64.exe "C:\Users" /export="big.csv" /filter=">=1gb" /exportfolders=0

:: 生成树状图 PNG（适合直观展示"谁吃了空间"）
wiztree64.exe "C:" /treemapimagefile="C:\temp\c_%d.png" /treemapimagewidth=1920 /treemapimageheight=1080 /treemapimagefreespace=0

:: 直接 dump MFT（需要管理员，用于超大规模/取证分析）
wiztree64.exe "D:" /dumpmftfile="C:\temp\d.mft"
```
