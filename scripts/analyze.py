#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WizTree CSV -> 磁盘清理分析报告

用法:
  python analyze.py <scan.csv> [--report out.md] [--json out.json]
                    [--top 40] [--dup-min-mb 100] [--old-days 365] [--old-min-mb 500]

设计要点:
  * 流式解析，百万级行也不吃内存（1.3M 行约 10-30s）
  * 规则表用单条组合正则匹配，避免 40 条正则 × 百万文件
  * 每个文件只归入一个类别（第一个命中的规则），防止重复计数
  * 输出：Markdown 报告（给人看）+ JSON 摘要（给 agent 读）
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from heapq import heappush, heappushpop

BANNER_KEYS = ("文件名称", "File Name", "Filename")
FOLDER_SUFFIX = ("\\", "/")

# ---------------------------------------------------------------- 清理规则表
# key, 显示名, 风险(安全/谨慎/勿动/系统), 正则(对小写全路径), 最小单文件字节数, 建议
RULES = [
    # ---- 安全：可再生的临时/缓存/日志 ----
    ("win_temp", "Windows 临时目录", "安全", r"\\windows\\temp\\", 0,
     "直接删除；占用中的文件跳过即可"),
    ("user_temp", "用户临时目录 (TEMP)", "安全", r"\\appdata\\local\\temp\\", 0,
     "退出应用后清空，可自动重建"),
    ("update_cache", "Windows 更新下载缓存", "安全",
     r"\\softwaredistribution\\download\\", 0,
     "停 wuauserv 后删除，或 设置>存储>临时文件"),
    ("delivery_opt", "传递优化缓存", "安全", r"\\deliveryoptimization\\", 0,
     "设置>存储>临时文件 中清理"),
    ("prefetch", "预读取文件 Prefetch", "安全", r"\\windows\\prefetch\\", 0,
     "可删，仅影响首次启动速度"),
    ("dumps", "崩溃转储 / 错误报告", "安全",
     r"\\crashdumps\\|\\windows\\minidump\\|\\windows\\memory\.dmp|\\livekernelreports\\|\\wer\\report(archive|queue)\\|\\wer\\", 0,
     "可删；排查蓝屏后才需要"),
    ("recycle", "回收站", "安全", r"\\\$recycle\.bin\\", 0,
     "清空回收站即可"),
    ("windows_old", "Windows.old 旧系统", "安全", r"\\windows\.old\\", 0,
     "设置>存储>临时文件，或 磁盘清理 删除"),
    ("winre_agent", "Windows 恢复代理临时目录 ($WinREAgent)", "安全",
     r"\\\$winreagent\\", 0, "系统更新后的残留，可删"),
    ("setup_temp", "系统升级临时目录 ($Windows.~BT / ~WS)", "安全",
     r"\\\$windows\.~b[ts]\\|\\\$getcurrent\\", 0, "升级残留，可删"),
    ("windows_logs", "Windows 系统日志", "安全", r"\\windows\\logs\\", 0,
     "可删；CBS 日志常涨到几百 MB"),
    ("cache_dirs", "应用缓存目录（浏览器 / Electron / 缩略图 / 着色器）", "安全",
     r"(\\users\\[^\\]+\\appdata\\(local|roaming)\\|\\programdata\\|\\htmlcache\\|\\\.adspower_global\\).*\\(cache|code cache|gpucache|gpucache\\d+|shadercache|grshadercache|dawngraphitecache|cachestorage|service worker|blob_storage|jumpflistimagedb|thumbcache|iconcache|cache2|startupcache|dxcache|gldcache|d3dscache|cacheddata)\\|\\inetcache\\|\\webcache\\",
     0, "退出对应应用后删除，会自动重建"),
    ("local_packages_cache", "UWP 应用本地缓存", "安全",
     r"\\appdata\\local\\packages\\[^\\]+\\localcache\\", 0, "可删，应用会重建"),
    ("pkg_cache", "包管理器缓存 (npm/pip/yarn/pnpm)", "安全",
     r"\\npm-cache\\|\\_cacache\\|\\pnpm-store\\|\\pnpm\\cache\\|\\pip\\cache\\|\\yarn\\cache\\|\\\.cache\\pip\\", 0,
     "npm cache clean --force / pip cache purge"),
    ("pycache", "Python 字节码缓存", "安全",
     r"\\__pycache__\\|\\\.pytest_cache\\|\\\.mypy_cache\\|\\\.ruff_cache", 0,
     "可删，运行时会重建"),
    ("logs", "日志文件", "谨慎", r"\.log$|\.etl$|\.dmp$", 10 * 1024 * 1024,
     "确认无用后删除；部分日志用于排障"),

    # ---- 谨慎：删了要重下 / 可能是数据 / 项目相关 ----
    ("node_modules", "node_modules 依赖目录", "谨慎", r"\\node_modules\\", 0,
     "项目可 npm install 重建；先确认项目是否仍在用"),
    ("gradle_m2", "构建工具本地仓库", "谨慎",
     r"\\\.gradle\\caches\\|\\\.m2\\repository\\|\\\.nuget\\packages\\|\\\.cargo\\registry\\|\\conda\\pkgs\\|\\\.ivy2\\", 0,
     "重下耗时长，网络差时别删"),
    ("build_out", "编译输出 (obj/bin/target)", "谨慎",
     r"\\obj\\|\\bin\\debug\\|\\bin\\release\\|\\target\\debug\\|\\target\\release\\", 0,
     "可 rebuild；确认不是唯一产物"),
    ("ucache", "Unity/UE 派生缓存", "谨慎",
     r"\\library\\(artifactdb|shadercache|il2cppbuildcache)\\|\\deriveddatacache\\|\\intermediate\\", 0,
     "重新打开工程会重建，较耗时"),
    ("wechat", "微信/QQ/钉钉 聊天文件", "谨慎",
     r"\\wechat files\\|\\xwechat_files\\|\\tencent files\\|\\appdata\\roaming\\tencent\\|\\dingtalk\\", 0,
     "聊天记录/接收文件，务必确认后再删"),
    ("netdisk", "网盘缓存与下载", "谨慎", r"\\baidunetdisk\\|\\appdata\\local\\baidu\\", 0,
     "缓存可删；已下载文件确认是否还需"),
    ("app_update_cache", "应用更新包缓存（华为管家等）", "谨慎", r"\\programdata\\comms\\", 0,
     "更新安装包，删后可重新下载"),
    ("steam", "Steam/游戏安装目录", "谨慎", r"\\steamapps\\common\\|\\epic games\\|\\riot games\\|\\battle\.net\\", 0,
     "删了要重下（几十 GB）"),
    ("installer", "Windows Installer 安装缓存", "谨慎", r"\\windows\\installer\\", 0,
     "勿手动删，会导致卸载/修复失败；用 PatchCleaner 类工具"),
    ("iso_img", "镜像/虚拟磁盘 (iso/vhd/vmdk)", "谨慎",
     r"\.(iso|img|vhd|vhdx|vmdk|vdi|wim|esd)$", 50 * 1024 * 1024,
     "常为系统镜像/虚拟机盘，确认后可归档到外置盘"),
    ("archives", "压缩包", "谨慎",
     r"\.(zip|rar|7z|tar|gz|bz2|xz|001)$", 100 * 1024 * 1024,
     "多为下载的安装包/备份，确认后可删"),
    ("bak_tmp", "备份/临时/旧版本文件", "谨慎",
     r"\.(bak|old|orig|tmp|temp|~)$|~\d+$", 10 * 1024 * 1024,
     "确认后被 .bak/.old 可删"),

    # ---- 勿动：系统托管 ----
    ("winsxs", "WinSxS 组件存储", "勿动", r"\\winsxs\\", 0,
     "只能用 DISM /StartComponentCleanup 清理"),
    ("sysvol", "System Volume Information", "勿动", r"\\system volume information\\", 0,
     "系统还原点，用 系统保护 界面管理"),
    ("pagefiles", "页面文件/休眠文件", "系统",
     r"^[a-z]:\\(hiberfil\.sys|pagefile\.sys|swapfile\.sys)$", 0,
     "用 powercfg /h off 关休眠；页面文件在系统属性里调"),
]

COMPILED = re.compile(
    "|".join("(?P<%s>%s)" % (k, rx) for k, _, _, rx, _, _ in RULES), re.IGNORECASE
)
RULE_META = {k: (label, risk, min_size, action) for k, label, risk, rx, min_size, action in RULES}


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.2f %s" % (n, unit))
        n /= 1024.0


def parse_time(s):
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except Exception:
            pass
    return None


def short(path, keep=72):
    if len(path) <= keep:
        return path
    return path[: keep // 3] + " ... " + path[-(keep - keep // 3 - 5):]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--report", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--folders", default="", help="把全部目录行导出为 TSV 侧文件，供 drill.py 逐层下钻")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--dup-min-mb", type=float, default=100)
    ap.add_argument("--old-days", type=int, default=365)
    ap.add_argument("--old-min-mb", type=float, default=500)
    args = ap.parse_args()

    now = datetime.now()
    dup_min = int(args.dup_min_mb * 1024 * 1024)
    old_min = int(args.old_min_mb * 1024 * 1024)

    drive = {"capacity": 0, "free": 0, "used": 0}
    root = {"files": 0, "folders": 0, "size": 0}
    n_rows = 0
    n_files = 0
    total_file_bytes = 0

    top_files = []          # heap of (size, path, mtime)
    folders = []            # (size, path, depth)
    all_folders = []        # 全部 >=1MB 的目录，供 --folders 侧文件与 drill 用
    ext_stat = defaultdict(lambda: [0, 0])
    rule_stat = defaultdict(lambda: [0, 0])     # key -> [bytes, count]
    rule_examples = defaultdict(list)           # key -> [(size, path)]
    dup_map = defaultdict(list)                 # (name, size) -> [paths]
    old_large = []                              # (size, path, days)
    biggest_unclassified = []

    with open(args.csv, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        header = None
        for row in reader:
            if not row:
                continue
            cell = (row[0] or "").strip()
            if cell in BANNER_KEYS or cell.startswith("文件名称") or cell.startswith("File Name"):
                header = [c.strip().upper() for c in row]
                break
        cap_idx = free_idx = used_idx = None
        if header:
            for i, h in enumerate(header):
                if h == "DRIVECAPACITY":
                    cap_idx = i
                elif h == "FREESPACE":
                    free_idx = i
                elif h == "USEDSPACE":
                    used_idx = i

        for row in reader:
            if len(row) < 5:
                continue
            path = row[0]
            try:
                size = int(row[1] or 0)
            except ValueError:
                continue
            n_rows += 1

            is_dir = path.endswith(FOLDER_SUFFIX)
            if is_dir:
                depth = len([seg for seg in path.rstrip("\\/").split("\\") if seg])
                root["folders"] += 1
                if depth == 1 and size:
                    root["size"] = max(root["size"], size)
                    root["files"] = int(row[5] or 0) if len(row) > 5 else 0
                if size >= 1024 * 1024:
                    all_folders.append((size, path, depth))
                if cap_idx is not None and len(row) > max(x for x in (cap_idx, free_idx, used_idx) if x is not None):
                    try:
                        drive["capacity"] = int(row[cap_idx] or 0) or drive["capacity"]
                        drive["free"] = int(row[free_idx] or 0)
                        drive["used"] = int(row[used_idx] or 0)
                    except ValueError:
                        pass
                continue

            n_files += 1
            total_file_bytes += size
            mtime = parse_time(row[3]) if len(row) > 3 else None

            # 大文件
            if size > 0:
                item = (size, path, mtime)
                if len(top_files) < args.top:
                    heappush(top_files, item)
                elif size > top_files[0][0]:
                    heappushpop(top_files, item)

            # 扩展名
            base = path.rsplit("\\", 1)[-1]
            if "." in base[1:] and not base.endswith("."):
                ext = "." + base.rsplit(".", 1)[-1].lower()
            else:
                ext = "(无扩展名)"
            ext_stat[ext][0] += size
            ext_stat[ext][1] += 1

            # 清理规则
            m = COMPILED.search(path)
            if m:
                key = m.lastgroup
                label, risk, min_size, action = RULE_META[key]
                if size >= min_size:
                    rule_stat[key][0] += size
                    rule_stat[key][1] += 1
                    ex = rule_examples[key]
                    if len(ex) < 3:
                        ex.append((size, path))
                    else:
                        ex.sort()
                        if size > ex[0][0]:
                            ex[0] = (size, path)
            elif size >= 512 * 1024 * 1024:
                biggest_unclassified.append((size, path))

            # 重复候选（同文件名 + 同大小，>= dup_min）
            if size >= dup_min:
                dup_map[(base.lower(), size)].append(path)

            # 陈旧大文件
            if size >= old_min and mtime:
                days = (now - mtime).days
                if days >= args.old_days:
                    old_large.append((size, path, days))

    # ---- 汇总 ----
    folders = [x for x in all_folders if 2 <= x[2] <= 5 and x[0] >= 512 * 1024 * 1024]
    top_files.sort(reverse=True)
    biggest_unclassified.sort(reverse=True)
    old_large.sort(reverse=True)
    folders.sort(reverse=True)

    # 目录去重：父目录已列出则跳过其子目录
    def pick(cands, limit):
        out, seen = [], []
        for s, p, d in cands:
            lp = p.lower()
            if any(lp.startswith(x) for x in seen):
                continue
            seen.append(lp)
            out.append((s, p))
            if len(out) >= limit:
                break
        return out

    user_root = re.compile(r"^[a-z]:\\users\\[^\\]+\\?$")
    top_folders = pick([x for x in folders if x[2] <= 2], 12)
    deep_folders = pick([x for x in folders if 3 <= x[2] <= 6 and not user_root.match(x[1].lower())], 15)
    folders = top_folders + deep_folders

    dup_groups = []
    for (name, size), paths in dup_map.items():
        if len(paths) > 1:
            dup_groups.append((size * (len(paths) - 1), size, name, paths[:6]))
    dup_groups.sort(reverse=True)
    dup_total = sum(g[0] for g in dup_groups)

    rules = []
    for key, (b, c) in rule_stat.items():
        label, risk, min_size, action = RULE_META[key]
        rules.append({
            "key": key, "label": label, "risk": risk, "action": action,
            "bytes": b, "count": c,
            "examples": [p for _, p in sorted(rule_examples[key], reverse=True)],
        })
    rules.sort(key=lambda r: -r["bytes"])
    risk_order = {"安全": 0, "谨慎": 1, "系统": 2, "勿动": 3}

    safe_total = sum(r["bytes"] for r in rules if r["risk"] == "安全")
    care_total = sum(r["bytes"] for r in rules if r["risk"] == "谨慎")

    summary = {
        "csv": os.path.abspath(args.csv),
        "generated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "drive": drive,
        "root_size": root["size"],
        "file_count": n_files,
        "file_bytes": total_file_bytes,
        "safe_total": safe_total,
        "care_total": care_total,
        "rules": rules,
        "top_files": [{"size": s, "path": p, "mtime": t.strftime("%Y/%m/%d") if t else None}
                      for s, p, t in top_files],
        "top_folders": [{"size": s, "path": p} for s, p in top_folders],
        "deep_folders": [{"size": s, "path": p} for s, p in deep_folders],
        "ext": [{"ext": e, "bytes": v[0], "count": v[1]}
                for e, v in sorted(ext_stat.items(), key=lambda kv: -kv[1][0])[:20]],
        "duplicates": [{"wasted": w, "size": s, "name": n, "paths": ps} for w, s, n, ps in dup_groups[:20]],
        "dup_total": dup_total,
        "old_large": [{"size": s, "path": p, "days": d} for s, p, d in old_large[:30]],
        "unclassified_big": [{"size": s, "path": p} for s, p in biggest_unclassified[:20]],
    }

    # ---- Markdown ----
    L = []
    A = L.append
    A("# 磁盘清理分析报告")
    A("")
    A("- 扫描数据：`%s`" % os.path.abspath(args.csv))
    A("- 生成时间：%s" % summary["generated"])
    A("- 文件数：{:,}　目录数：{:,}".format(n_files, root["folders"]))
    if drive["capacity"]:
        A("- 卷容量：%s　已用：%s　可用：%s（%.1f%% 已用）" % (
            human(drive["capacity"]), human(drive["used"]), human(drive["free"]),
            100.0 * drive["used"] / drive["capacity"]))
    A("")
    A("## 一、结论速览")
    A("")
    A("| 项 | 空间 | 说明 |")
    A("|---|---|---|")
    A("| 安全可清理（缓存/临时/回收站等） | **%s** | 再生成本低，确认后可直接删 |" % human(safe_total))
    A("| 需确认后可清理（依赖/安装包/旧备份） | **%s** | 删掉要重下或可能丢数据 |" % human(care_total))
    A("| 同名同大小重复文件（疑似冗余） | %s | 需校验内容后再删 |" % human(dup_total))
    A("")

    A("## 二、分类清理候选")
    A("")
    A("| 类别 | 风险 | 大小 | 文件数 | 建议 |")
    A("|---|---|---|---|---|")
    for r in rules:
        A("| %s | %s | %s | %s | %s |" % (
            r["label"], r["risk"], human(r["bytes"]), "{:,}".format(r["count"]), r["action"]))
    A("")

    A("## 三、最大文件 Top %d" % len(top_files))
    A("")
    A("| 大小 | 修改时间 | 路径 |")
    A("|---|---|---|")
    for s, p, t in top_files:
        A("| %s | %s | `%s` |" % (human(s), t.strftime("%Y/%m/%d") if t else "-", short(p)))
    A("")

    A("## 四、占用最大的目录")
    A("")
    A("### 顶层")
    A("")
    A("| 大小 | 目录 |")
    A("|---|---|")
    for s, p in top_folders:
        A("| %s | `%s` |" % (human(s), short(p, 80)))
    A("")
    if deep_folders:
        A("### 深层热点")
        A("")
        A("| 大小 | 目录 |")
        A("|---|---|")
        for s, p in deep_folders:
            A("| %s | `%s` |" % (human(s), short(p, 80)))
        A("")

    A("## 五、文件类型分布 Top 20")
    A("")
    A("| 类型 | 大小 | 文件数 |")
    A("|---|---|---|")
    for e, (b, c) in sorted(ext_stat.items(), key=lambda kv: -kv[1][0])[:20]:
        A("| %s | %s | %s |" % (e, human(b), "{:,}".format(c)))
    A("")

    if dup_groups:
        A("## 六、疑似重复文件（同名同大小，≥%.0f MB）" % args.dup_min_mb)
        A("")
        A("> WizTree 不比对内容，删除前请先校验（可对前几个文件算 SHA256）。")
        A("")
        A("| 单个大小 | 冗余合计 | 文件名 | 样例路径 |")
        A("|---|---|---|---|")
        for w, s, n, ps in dup_groups[:15]:
            A("| %s | %s | %s | `%s` |" % (human(s), human(w), n, short(ps[0])))
        A("")

    if old_large:
        A("## 七、长期未动的大文件（≥%.0f MB，%d 天以上）" % (args.old_min_mb, args.old_days))
        A("")
        A("| 大小 | 未修改天数 | 路径 |")
        A("|---|---|---|")
        for s, p, d in old_large[:20]:
            A("| %s | %d | `%s` |" % (human(s), d, short(p)))
        A("")

    if biggest_unclassified:
        A("## 八、未归类的大文件（≥512 MB，需人工判断）")
        A("")
        A("| 大小 | 路径 |")
        A("|---|---|")
        for s, p in biggest_unclassified[:20]:
            A("| %s | `%s` |" % (human(s), short(p)))
        A("")

    A("---")
    A("*报告由 wiztree-cleanup skill 生成。删除前请逐项确认，优先处理「安全」类。*")

    md = "\n".join(L)
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(md + "\n")
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
    if args.folders:
        os.makedirs(os.path.dirname(os.path.abspath(args.folders)) or ".", exist_ok=True)
        with open(args.folders, "w", encoding="utf-8") as f:
            f.write("bytes\tpath\n")
            for s, p, _ in sorted(all_folders, reverse=True):
                f.write("%d\t%s\n" % (s, p))

    # 控制台摘要
    print("rows=%d files=%d bytes=%s dirs=%d" % (n_rows, n_files, human(total_file_bytes), root["folders"]))
    if drive["capacity"]:
        print("volume: total=%s used=%s free=%s" % (
            human(drive["capacity"]), human(drive["used"]), human(drive["free"])))
    print("safe=%s  care=%s  dup=%s" % (human(safe_total), human(care_total), human(dup_total)))
    print("\n%-38s %-4s %10s %9s" % ("category", "risk", "size", "files"))
    for r in rules[:18]:
        print("%-38s %-4s %10s %9d" % (r["label"][:36], r["risk"], human(r["bytes"]), r["count"]))
    if args.report:
        print("\nreport: %s" % os.path.abspath(args.report))
    if args.json:
        print("json:   %s" % os.path.abspath(args.json))


if __name__ == "__main__":
    main()
