# -*- coding: utf-8 -*-
"""
汇金重仓宽基 ETF 份额快照 —— K01 的数据采集器

免费接口只给「当日」份额，历史必须自己一天一天存。
每次运行会覆盖当天记录（同日重复跑不会写重），并刷新 data/k01_index.json 汇总。

用法：
    python etf_flow.py --mx data/_mx_2026-11-16.json   # 首选：读连接器快照（total_shares）
    python etf_flow.py                                  # 兜底：直连东财 push2 的 f84
    python etf_flow.py --summary                        # 只读已有 csv，打印近期窗口

口径（2026-11-16 改）：
    份额不再依赖东财 f84 —— 该字段所在主机 push2 已持续返回 000，且备用主机
    push2delay2 的 f84 与真实份额差 1.9 倍（口径不同），不可替代。
    改用腾讯自选股连接器 data_quote 的 total_shares（与公开跟踪工具口径一致）。

    日净流入 ≈ (今日份额 − 上一记录日份额) × 今日价格
    加仓指数 = 100 × 当日主池总份额 ÷ 区间首日主池总份额
    缺口处理：某只当日无数据则不计入该日，指数按「已有前值顺延」累计，
    避免缺数被误读成赎回。仅当主池全部到齐的日期才作为区间基准。

    名单口径：2025 年报「前十大持有人」最后一次实名披露，2026 年起停止披露，无法再更新。
    日度份额变化一律视为汇金操作只是近似假设，实际也可能来自其他机构或个人投资者。
"""
import csv
import json
import os
import sys
import urllib.request
from datetime import date

from paths import data                  # 路径唯一真源（ADR-001）

CSV_PATH = data("etf_flow.csv")
INDEX_PATH = data("k01_index.json")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

# (代码, 市场 1沪/0深, 名称, 指数族, 层级 core=年报确认汇金持仓 / watch=同族观察)
# 注：科创50 的汇金持仓在 588080（易方达），588000（华夏）年报未见，故后者降为 watch。
POOL = [
    ("510300", 1, "沪深300ETF华泰柏瑞", "沪深300", "core"),
    ("510310", 1, "沪深300ETF易方达", "沪深300", "core"),
    ("510330", 1, "沪深300ETF华夏", "沪深300", "core"),
    ("159919", 0, "沪深300ETF嘉实", "沪深300", "core"),
    ("510050", 1, "上证50ETF华夏", "上证50", "core"),
    ("510500", 1, "中证500ETF南方", "中证500", "core"),
    ("159922", 0, "中证500ETF嘉实", "中证500", "core"),
    ("512500", 1, "中证500ETF华夏", "中证500", "core"),
    ("512100", 1, "中证1000ETF南方", "中证1000", "core"),
    ("159845", 0, "中证1000ETF华夏", "中证1000", "core"),
    ("560010", 1, "中证1000ETF广发", "中证1000", "core"),
    ("159629", 0, "中证1000ETF富国", "中证1000", "core"),
    ("588080", 1, "科创50ETF易方达", "科创50", "core"),
    ("588050", 1, "科创50ETF工银", "科创50", "core"),
    ("159915", 0, "创业板ETF易方达", "创业板", "core"),
    ("159952", 0, "创业板ETF广发", "创业板", "core"),
    ("510180", 1, "上证180ETF华安", "上证180", "core"),
    ("515800", 1, "中证800ETF汇添富", "中证800", "core"),
    ("159901", 0, "深证100ETF易方达", "深证100", "core"),
    ("510230", 1, "金融ETF国泰", "金融", "core"),
    ("588000", 1, "科创50ETF华夏", "科创50", "watch"),
    ("159338", 0, "中证A500ETF国泰", "中证A500", "watch"),
    ("512050", 1, "A500ETF华夏", "中证A500", "watch"),
    ("159339", 0, "A500ETF银华", "中证A500", "watch"),
]
CORE = [p[0] for p in POOL if p[4] == "core"]

FIELDS = ["date", "family", "code", "name", "price", "shares", "aum", "chg_shares", "net_flow"]


def fetch(code, mkt):
    """兜底通道：东财 push2 的 f84（总份额）。该主机常返回 000，仅作备用。"""
    secid = ("%d.%s" % (mkt, code))
    url = ("https://push2.eastmoney.com/api/qt/stock/get?secid=%s"
           "&fields=f43,f58,f84&ut=fa5fd1943c7b386f172d6893dbfba10b" % secid)
    req = urllib.request.Request(url, headers=UA)
    d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())["data"]
    if not d or not d.get("f84"):
        return None
    return {
        "code": code,
        "name": (d.get("f58") or code),
        "price": (d.get("f43") or 0) / 1000.0,
        "shares": float(d["f84"]),
    }


def from_mx(path):
    """首选通道：连接器快照 data/_mx_*.json 里的 total_shares。"""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    quotes = d.get("quotes") or {}
    out = {}
    for code, mkt, label, fam, tier in POOL:
        q = quotes.get(code)
        if not q or not q.get("total_shares"):
            continue
        out[code] = {
            "code": code,
            "name": q.get("name") or label,
            "price": float(q.get("price") or 0),
            "shares": float(q["total_shares"]),
        }
    return out


def load():
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save(rows):
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def last_rec(rows, code):
    """取该 code 最近一条（不含今天）的 (份额, 日期)。"""
    today = date.today().isoformat()
    for r in reversed(rows):
        if r["code"] == code and r["date"] != today:
            return float(r["shares"]), r["date"]
    return None, None


def build_index(rows):
    """份额加仓指数：只有主池全部到齐的日期才计入，缺数日按前值顺延。"""
    dates = sorted({r["date"] for r in rows})
    last, series = {}, []
    for d in dates:
        sub = {r["code"]: float(r["shares"]) for r in rows
               if r["date"] == d and r["code"] in CORE}
        for c in CORE:
            if c in sub:
                last[c] = sub[c]
        if len(last) == len(CORE):          # 主池齐了才算一个有效观测点
            series.append((d, sum(last.values())))
    if not series:
        return None
    base_d, base_tot = series[0]
    d, tot = series[-1]
    prev_tot = series[-2][1] if len(series) > 1 else None
    return {
        "date": d, "pool": len(CORE),
        "total_shares": tot, "base_date": base_d, "base_shares": base_tot,
        "index": round(100.0 * tot / base_tot, 2) if base_tot else 100.0,
        "chg_shares": (tot - prev_tot) if prev_tot is not None else 0,
        "days": len(series),
        "note": ("份额取自连接器 total_shares（非官方披露）；日度变化也可能来自其他机构或个人投资者；"
                 "2026 年起前十大持有人停止披露，名单无法再更新"),
    }


def collect(mx_path=None):
    rows = load()
    today = date.today().isoformat()
    rows = [r for r in rows if not (r["date"] == today)]   # 覆盖当天旧记录

    src = {}
    if mx_path:
        try:
            src = from_mx(mx_path)
            print("份额来源：连接器快照 %s（%d/%d 只）\n" % (mx_path, len(src), len(POOL)))
        except Exception as e:
            print("读快照失败（%s），改用东财兜底\n" % e)

    tot_aum = tot_flow = 0.0
    fam_stat, miss = {}, []
    print("%-10s %-22s %-8s %8s %12s %10s %10s" %
          ("指数族", "名称", "代码", "价格", "份额(亿份)", "规模(亿元)", "净流入(亿)"))
    print("-" * 92)
    for code, mkt, label, fam, tier in POOL:
        q = src.get(code)
        if not q:
            try:
                q = fetch(code, mkt)
            except Exception:
                q = None
        if not q:
            miss.append(code)
            print("%-10s %-22s %-8s  无数据，跳过" % (fam, label, code))
            continue
        prev, prev_d = last_rec(rows, code)
        gap = 0
        if prev is not None and prev_d:
            try:
                gap = (date.today() - date.fromisoformat(prev_d)).days
            except ValueError:
                gap = 0
        # 距上一记录超过 4 天（跨周末以上）就不是「当日净流入」，只能算跨期变化
        cross = prev is not None and gap > 4
        chg = (q["shares"] - prev) if prev is not None else 0.0
        flow = chg * q["price"] / 1e8
        aum = q["shares"] * q["price"] / 1e8
        rows.append({
            "date": today, "family": fam, "code": code, "name": q["name"],
            "price": "%.3f" % q["price"],
            "shares": "%.0f" % q["shares"],
            "aum": "%.2f" % aum,
            "chg_shares": "%.0f" % chg,
            "net_flow": "%.2f" % flow,
        })
        tot_aum += aum
        if not cross:                     # 跨期变化不计入当日合计，否则会把 11 天当 1 天
            tot_flow += flow
        f = fam_stat.setdefault(fam, [0.0, 0.0])
        f[0] += aum
        if not cross:
            f[1] += flow
        if prev is None:
            flag = "  (首日)"
        elif cross:
            flag = "  (跨 %d 日)" % gap
        else:
            flag = ""
        tag = "" if tier == "core" else " *"
        print("%-10s %-22s %-8s %8.3f %12.2f %10.2f %10.2f%s%s" % (
            fam, q["name"][:20], code, q["price"], q["shares"] / 1e8, aum, flow, flag, tag))

    print("-" * 92)
    print("%-10s %-22s %-8s %8s %12s %10.2f %10.2f" %
          ("合计", "", "", "", "", tot_aum, tot_flow))
    print("（* 为观察池：2025 年报未见汇金持仓，仅跟踪同族趋势）")
    print("\n按指数族：")
    for fam, (a, fl) in sorted(fam_stat.items(), key=lambda x: -x[1][0]):
        print("  %-10s 规模 %8.2f 亿   净流入 %+8.2f 亿" % (fam, a, fl))
    save(rows)

    idx = build_index(rows)
    if idx:
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1)
        print("\n加仓指数 %.2f（基准 %s = 100，主池 %d 只，有效观测 %d 日）"
              % (idx["index"], idx["base_date"], idx["pool"], idx["days"]))
        print("主池总份额 %.1f 亿份%s" % (
            idx["total_shares"] / 1e8,
            "，较上一记录 %+.2f 亿份" % (idx["chg_shares"] / 1e8) if idx["chg_shares"] else ""))
        print("已写入 %s" % INDEX_PATH)

    print("\n已写入 %s（累计 %d 行）" % (CSV_PATH, len(rows)))
    if miss:
        print("缺数 %d 只：%s（当日不写入，指数按前值顺延）" % (len(miss), "、".join(miss)))
    if idx and idx["days"] <= 1:
        print("提示：区间首日，指数恒为 100。份额差异需逐日累积，攒够 8 周才能看方向。")


def summary():
    rows = load()
    if not rows:
        print("还没有数据，先跑一次 python etf_flow.py --mx data/_mx_<date>.json")
        return
    dates = sorted({r["date"] for r in rows})
    last, series = {}, []
    for d in dates:
        sub = {r["code"]: float(r["shares"]) for r in rows
               if r["date"] == d and r["code"] in CORE}
        for c in CORE:
            if c in sub:
                last[c] = sub[c]
        series.append((d, sum(last.values()), len(last)))
    base = None
    for d, tot, n in series:
        if n == len(CORE):
            base = tot
            break
    print("%-12s %10s %14s %10s" % ("日期", "覆盖", "主池份额(亿份)", "加仓指数"))
    print("-" * 50)
    for d, tot, n in series[-10:]:
        print("%-12s %4d/%-4d %14.1f %10s" % (
            d, n, len(CORE), tot / 1e8,
            "%.2f" % (100.0 * tot / base) if base else "—"))


if __name__ == "__main__":
    if "--summary" in sys.argv:
        summary()
    else:
        mx = None
        if "--mx" in sys.argv:
            i = sys.argv.index("--mx")
            mx = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        collect(mx)
