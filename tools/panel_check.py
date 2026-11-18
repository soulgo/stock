#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""panel_check.py —— 面板契约回归（ADR-003）。

两件事：
1. **解析等价**：panel.py 的 parse_* 与某个 git 版本的 report_gen.parse_* 在全部历史面板文本上
   产出完全相同的 dict。搬代码不许改行为，这是判据。
2. **契约体检**：对每份历史文本跑 validate()，看必备段实际缺失率 —— 校验规则定得太严会让
   正常报告卡死，太松又挡不住漂移，所以先用真实语料量一遍。

用法：
    python tools/panel_check.py                 # 对比 HEAD 与工作区
    python tools/panel_check.py --ref 36ac9d9   # 指定基准提交
"""
import argparse
import glob
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from paths import REPORTS, ARCHIVE  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def corpus():
    files = []
    for d in (REPORTS, ARCHIVE):
        files += glob.glob(os.path.join(d, "_morning_*.txt"))
        files += glob.glob(os.path.join(d, "_noon_*.txt"))
        files += glob.glob(os.path.join(d, "_evening_*.txt"))
    return sorted(f for f in files if os.path.basename(f).count("_") >= 2)


def mode_of(path):
    b = os.path.basename(path)
    return ("morning" if b.startswith("_morning_") else
            "noon" if b.startswith("_noon_") else "evening")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    a = ap.parse_args()

    out = subprocess.run(["git", "-C", ROOT, "show", "%s:report_gen.py" % a.ref], capture_output=True)
    if out.returncode != 0:
        sys.exit("取不到 %s:report_gen.py" % a.ref)
    tmpdir = tempfile.mkdtemp(prefix="panel_check_")
    oldpath = os.path.join(tmpdir, "report_gen_base.py")
    with io.open(oldpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(out.stdout.decode("utf-8"))

    base = load_module("rg_base", oldpath)
    cur = load_module("panel_cur", os.path.join(ROOT, "panel.py"))

    files = corpus()
    print("语料：%d 份历史面板文本\n" % len(files))
    bad = 0
    stat = {}
    for p in files:
        mode = mode_of(p)
        text = io.open(p, encoding="utf-8").read()
        fn = {"morning": "parse_morning", "noon": "parse_noon"}.get(mode)
        if not fn:
            continue
        try:
            x = getattr(base, fn)(text)
        except Exception as e:
            print("  基准解析失败 %-28s %s" % (os.path.basename(p), e))
            continue
        try:
            y = getattr(cur, fn)(text)
        except Exception as e:
            bad += 1
            print("  **新解析抛异常** %-28s %s" % (os.path.basename(p), e))
            continue
        sx = json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)
        sy = json.dumps(y, ensure_ascii=False, sort_keys=True, default=str)
        if sx == sy:
            print("  %-6s %-30s 一致（%d 字节）" % (mode, os.path.basename(p), len(sx)))
        else:
            bad += 1
            print("  %-6s %-30s **不一致**" % (mode, os.path.basename(p)))
            for k in sorted(set(x) | set(y)):
                if json.dumps(x.get(k), sort_keys=True, default=str) != \
                   json.dumps(y.get(k), sort_keys=True, default=str):
                    print("      字段 %s：" % k)
                    print("        旧 %s" % json.dumps(x.get(k), ensure_ascii=False, default=str)[:120])
                    print("        新 %s" % json.dumps(y.get(k), ensure_ascii=False, default=str)[:120])
        # 契约体检
        miss = cur.validate(y, mode, strict=False)
        stat.setdefault(mode, {})["总数"] = stat.setdefault(mode, {}).get("总数", 0) + 1
        for msg in miss:
            stat[mode][msg] = stat[mode].get(msg, 0) + 1

    print("\n【契约体检】历史文本跑 validate(strict=False) 的缺项统计：")
    for mode, d in stat.items():
        n = d.pop("总数")
        print("  %-8s %d 份，缺项：%s" % (mode, n, d if d else "无"))

    print("\n结论：%s" % ("解析完全等价 ✓" if not bad else "%d 份不一致 ✗" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
