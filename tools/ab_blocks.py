#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ab_blocks.py —— 指标段 A/B 对拍（ADR-002）。

把某个 git 版本里的 brief.py 取出来，与工作区当前版本吃**同一份冻结输入**，
逐字节比较 market / margin / news 三个指标段在 morning / noon / evening 下的 stdout。

用法：
    python tools/ab_blocks.py                  # 对比 HEAD 与工作区
    python tools/ab_blocks.py --ref 073e89f    # 对比指定提交与工作区
    python tools/ab_blocks.py --only edge      # 只跑退化输入（6 例）

为什么需要它：文本是 brief.py 与 report_gen.py 之间唯一的契约，改指标段时
「少印一行」「缩进差一格」都不会报错，只会在报告里静默少一块。冻结输入 + 逐字节比较
是唯一能证明「行为没变」的办法。正常数据跑不到退化分支，所以两组都要跑。

前提：data/ 下要有至少一份 _mx_<date>.json 快照（没有就先跑连接器 + mx_snapshot.py）。
"""
import argparse
import glob
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from paths import DATA  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def latest_snapshot():
    files = sorted(glob.glob(os.path.join(DATA, "_mx_*.json")))
    if not files:
        sys.exit("找不到 data/_mx_<date>.json —— 先跑连接器取数 + mx_snapshot.py")
    return files[-1]


def load_json(path, fallback=None):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback if fallback is not None else {}


def fixtures():
    """全部冻结输入：快照、两融缓存、持仓、快讯 fixture。"""
    mx = load_json(latest_snapshot())
    mk = load_json(os.path.join(DATA, "margin_market.json"), {"rows": []})
    et = load_json(os.path.join(DATA, "margin_etf.json"), {"data": {}})
    cfg = load_json(os.path.join(ROOT, "portfolio.json"), {"holdings": []})

    # 快讯用固定样本：真实新闻每次都变，无法作为可重放的输入
    seeds = [
        ("09:12", "汇金公司今日增持沪深300ETF，国家队资金再度入场", "东财"),
        ("09:40", "央行开展 5000 亿元 MLF 操作，中标利率持平", "新浪"),
        ("10:05", "多只宽基 ETF 份额单日增长，ETF 净流入超 80 亿", "东财"),
        ("10:31", "医药板块午后走强，创新药 ETF 放量", "新浪"),
        ("10:44", "汇金增持四大行，国家队托底信号明确", "东财"),
        ("11:02", "北向资金半日净买入 32 亿元", "新浪"),
        ("11:15", "证监会：稳步推进中长期资金入市", "东财"),
        ("11:28", "两融余额升至 1.9 万亿，杠杆资金回流", "东财"),
        ("11:40", "美股三大指数昨夜收涨，纳指涨 1.2%", "新浪"),
        ("11:52", "某银行上调消费股目标价", "东财"),
        ("12:01", "国债期货走弱，10 年期主力合约跌 0.1%", "新浪"),
        ("12:10", "港股恒生科技指数涨超 2%", "东财"),
    ]
    news = [{"dt": "2026-11-18 %s:00" % t, "time": t, "title": title, "src": src,
             "url": "http://fixture/%d" % i} for i, (t, title, src) in enumerate(seeds)]
    return mx, mk, et, cfg, news


def prepare(mod, mx, mkrows, etfdata, base, news):
    mod.MX["data"] = mx
    mod.intraday_progress = lambda dt=None: 0.824   # 固定日内进度，否则读时钟
    mod.pool_baseline = lambda codes: base
    mod.margin_market = lambda n=30: mkrows
    mod.margin_etf = lambda codes: etfdata
    mod.news_feed = lambda hours=6, max_pages=3: news
    return mod


def capture(fn, *a, **kw):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        fn(*a, **kw)
    except Exception as e:                       # 旧版某些退化输入会抛异常，要能记录而不是崩掉
        sys.stdout = old
        return buf.getvalue() + "<EXC %s>" % type(e).__name__
    sys.stdout = old
    return buf.getvalue()


def norm(t):
    # 「更新时间」是跑的那一刻，两版可能跨分钟，这一行不参与比较
    return re.sub(r"更新时间\s+\d{2}:\d{2}", "更新时间        HH:MM", t)


def run(mod, cfg, mxfab, mkrows, etfdata, base, news, calls):
    prepare(mod, mxfab, mkrows, etfdata, base, news)
    out = []
    for fn, args in calls:
        f = getattr(mod, fn, None)
        if f is None:
            out.append("<NOFN %s>" % fn)
            continue
        out.append(norm(capture(f, *args)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD", help="对比基准的 git 版本，默认 HEAD")
    ap.add_argument("--only", choices=["normal", "edge"], help="只跑其中一组")
    a = ap.parse_args()

    mx, mk, et, cfg, news = fixtures()
    base_full = {c: 1e8 for c in mx.get("quotes", {})}
    small_base = {}                                   # 基准全 0 → 同时点算不出来

    normal = [("market[%s]" % m, "market_block", ("五", cfg, m)) for m in ("morning", "noon", "evening")]
    normal += [("margin[noon]", "margin_block", ("六", cfg, "noon"))]
    normal += [("news[%s]" % m, "news_block", ("七", None, m)) for m in ("morning", "noon", "evening")]

    flows_only = {"flows_full": mx.get("flows_full") or {}, "quotes_full": mx.get("quotes_full") or {}}
    mkt_etf_only = {"market": mx.get("market") or {}, "etf": mx.get("etf") or {},
                    "quotes_full": mx.get("quotes_full") or {}}
    no_quotes = {"flows_full": mx.get("flows_full") or {}, "market": mx.get("market") or {},
                 "etf": mx.get("etf") or {}}
    edge = [
        ("空快照", {}, mk.get("rows") or [], et.get("data") or {}, base_full, [],
         [("market_block", ("五", cfg, "noon")), ("margin_block", ("六", cfg, "noon")),
          ("news_block", ("七", None, "noon"))]),
        ("只有资金流", flows_only, mk.get("rows") or [], et.get("data") or {}, base_full, [],
         [("market_block", ("五", cfg, "noon"))]),
        ("无指数资金流", mkt_etf_only, [], {}, base_full, [],
         [("market_block", ("五", cfg, "noon")), ("margin_block", ("六", cfg, "noon"))]),
        ("无 quotes", no_quotes, mk.get("rows") or [], {}, base_full, [],
         [("market_block", ("五", cfg, "noon"))]),
        ("基准全 0", mx, mk.get("rows") or [], et.get("data") or {}, small_base, [],
         [("market_block", ("五", cfg, "evening"))]),
        ("两融无记录", mx, [], {}, base_full, [],
         [("margin_block", ("六", cfg, "noon"))]),
    ]

    code = subprocess.run(["git", "-C", ROOT, "show", "%s:brief.py" % a.ref],
                          capture_output=True)
    if code.returncode != 0:
        sys.exit("取不到 %s:brief.py —— %s" % (a.ref, code.stderr.decode("utf-8", "ignore")[:200]))
    tmpdir = tempfile.mkdtemp(prefix="ab_blocks_")
    oldpath = os.path.join(tmpdir, "brief_base.py")
    with io.open(oldpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(code.stdout.decode("utf-8"))

    base = load_module("brief_base", oldpath)
    cur = load_module("brief_cur", os.path.join(ROOT, "brief.py"))

    bad = []
    if a.only != "edge":
        print("【正常输入】")
        for name, fn, args in normal:
            x = run(base, cfg, mx, mk.get("rows") or [], et.get("data") or {}, base_full, news, [(fn, args)])
            y = run(cur, cfg, mx, mk.get("rows") or [], et.get("data") or {}, base_full, news, [(fn, args)])
            if x == y:
                print("  %-16s 一致（%d 行 / %d 字节）" % (name, x[0].count("\n"), len(x[0].encode())))
            else:
                bad.append((name, x[0], y[0]))
                print("  %-16s **不一致**" % name)

    if a.only != "normal":
        print("【退化输入】")
        for name, mxfab, mkrows, etfdata, basef, nw, calls in edge:
            x = run(base, cfg, mxfab, mkrows, etfdata, basef, nw, calls)
            y = run(cur, cfg, mxfab, mkrows, etfdata, basef, nw, calls)
            if x == y:
                print("  %-16s 一致" % name)
            elif any("<EXC" in s for s in x) and not any("<EXC" in s for s in y):
                print("  %-16s 旧版抛异常(=整段丢失)，新版正常输出（已知修复）" % name)
            else:
                bad.append((name, "\n".join(x), "\n".join(y)))
                print("  %-16s **不一致**" % name)

    for name, x, y in bad:
        print("\n--- %s ---" % name)
        lx, ly = x.splitlines(), y.splitlines()
        for i in range(max(len(lx), len(ly))):
            p = lx[i] if i < len(lx) else "<缺行>"
            q = ly[i] if i < len(ly) else "<缺行>"
            if p != q:
                print("  旧 |%s" % p)
                print("  新 |%s" % q)
                break

    print("\n结论：%s" % ("全部一致 ✓" if not bad else "%d 例不一致 ✗" % len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
