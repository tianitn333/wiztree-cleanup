#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐层下钻：看某个目录下"谁最占空间"。

用法:
  python drill.py folders.tsv "C:\\Users\\13666\\AppData\\Local"
  python drill.py folders.tsv "C:\\Users\\13666\\AppData\\Local" --depth 3 --top 30
  python drill.py folders.tsv "C:\\Users\\13666\\AppData" --files C:\\...\\scan.csv --min-mb 200

  --depth N   展开到第 N 层（默认 1 = 只看直接子目录）
  --top N     每个目录最多列几个子项（默认 15）
  --min-mb M  只显示 >= M MB 的条目（默认 0）
  --files CSV 额外列出该目录下的直接大文件（需要原始 scan.csv）
"""
import argparse
import csv
import os
import sys


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.2f %s" % (n, unit))
        n /= 1024.0


def load_folders(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        head = f.readline()
        if not head.lower().startswith("bytes\t"):
            sys.exit("这不是 analyze.py --folders 生成的索引文件（首行应为 bytes<TAB>path）: %s" % path)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 2:
                continue
            try:
                rows.append((int(parts[0]), parts[1]))
            except ValueError:
                pass
    return rows


def load_files(csv_path, prefix):
    """返回 prefix 目录下直接文件的 [(size, name)]。"""
    out = []
    pre = prefix.rstrip("\\").lower() + "\\"
    with open(csv_path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        r = csv.reader(f)
        for row in r:
            if row and (row[0].startswith("文件名称") or row[0].startswith("File Name")):
                break
        for row in r:
            if len(row) < 5:
                continue
            p = row[0]
            if p.endswith("\\") or not p.lower().startswith(pre):
                continue
            rest = p[len(pre):]
            if "\\" in rest:
                continue
            try:
                out.append((int(row[1] or 0), rest))
            except ValueError:
                pass
    out.sort(reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv")
    ap.add_argument("path")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-mb", type=float, default=0.0)
    ap.add_argument("--files", default="")
    args = ap.parse_args()

    if not os.path.exists(args.tsv):
        sys.exit("找不到 %s，请先运行 analyze.py --folders <tsv> 生成" % args.tsv)

    root = args.path.rstrip("\\")
    prefix = root.lower() + "\\"
    base_depth = root.count("\\")
    min_bytes = int(args.min_mb * 1024 * 1024)

    rows = load_folders(args.tsv)
    children = {}
    total = 0
    for size, p in rows:
        if p.rstrip("\\").lower() == root.lower():
            total = size
            continue
        if not p.lower().startswith(prefix):
            continue
        rel = p[len(prefix):].rstrip("\\")
        if "\\" in rel:
            continue
        children[p] = size
    if not total:
        total = sum(children.values())

    print("%s  ->  %s" % (root, human(total)))
    print("")
    items = sorted(children.items(), key=lambda kv: -kv[1])[: args.top]
    for p, size in items:
        if size < min_bytes:
            continue
        pct = 100.0 * size / total if total else 0
        print("  %10s  %5.1f%%  %s" % (human(size), pct, p.rsplit("\\", 1)[0] + "\\" +
                                       p.rsplit("\\", 1)[-1]))

    if args.files:
        files = load_files(args.files, root)
        files = [(s, n) for s, n in files if s >= max(min_bytes, 50 * 1024 * 1024)][: args.top]
        if files:
            print("\n  直接位于该目录的大文件:")
            for s, n in files:
                print("  %10s         %s" % (human(s), n))

    # 递归展开
    if args.depth > 1:
        for p, size in items:
            if size < min_bytes:
                continue
            sub = [(sp, ss) for ss, sp in rows
                   if sp.lower().startswith(p.rstrip("\\").lower() + "\\")
                   and "\\" not in sp.rstrip("\\").lower()[len(p.rstrip("\\")) + 1:]]
            sub.sort(key=lambda kv: -kv[1])
            print("\n  [%s]" % p)
            for sp, ss in sub[: args.top]:
                print("    %10s  %s" % (human(ss), sp.rsplit("\\", 1)[-1]))


if __name__ == "__main__":
    main()
