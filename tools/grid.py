#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
grid.py —— 基于历史日线的网格参数测算（纯标准库）

用法：
    python grid.py              # 读 portfolio.json，算全部持仓
    python grid.py 512170       # 只算单只

数据源：东财 push2his 日线，fqt=0（不复权，因为网格挂单价必须是真实成交价）
"""
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timedelta

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
EM_HIS = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from paths import data  # noqa: E402  路径唯一真源（ADR-001）



def secid(code):
    code = code.strip()
    if code.startswith(("5", "6", "9", "11")):
        return "1." + code
    return "0." + code


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": "https://quote.eastmoney.com/"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def fetch_daily(code, beg, end="20500101", fqt=0):
    url = (f"{EM_HIS}?secid={secid(code)}&ut=fa5fd1943c7b386f172d6893dbfba10b"
           f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=101&fqt={fqt}&beg={beg}&end={end}")
    d = _get(url).get("data")
    if not d or not d.get("klines"):
        raise SystemExit(f"取不到 {code} 的日线数据")
    bars = []
    for k in d["klines"]:
        p = k.split(",")
        bars.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                     "high": float(p[3]), "low": float(p[4]),
                     "vol": float(p[5]), "amount": float(p[6])})
    return d.get("name", code), bars


def pct(vals, q):
    """线性插值分位数"""
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def analyse(code, name, shares, cost, years=4):
    beg = (datetime.now() - timedelta(days=int(years * 365.25))).strftime("%Y%m%d")
    nm, bars = fetch_daily(code, beg)
    if len(bars) < 60:
        return None
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    last = bars[-1]["close"]

    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    dstd = math.sqrt(var)
    ann = dstd * math.sqrt(244)

    a = atr(bars)
    atr_pct = a / last * 100

    hi, lo = max(highs), min(lows)
    pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50

    # 最大回撤
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)

    # 近 20 日平均振幅
    amp20 = sum((b["high"] - b["low"]) / b["close"] * 100 for b in bars[-20:]) / 20

    # 近 60 日 / 250 日
    hi250, lo250 = max(highs[-250:]), min(lows[-250:])
    hi60, lo60 = max(highs[-60:]), min(lows[-60:])

    target = cost * 1.30
    stop = cost * 0.85

    return {
        "code": code, "name": name, "shares": shares, "cost": cost, "now": last,
        "pnl_pct": (last / cost - 1) * 100,
        "bars": len(bars), "from": bars[0]["date"],
        "hi4y": hi, "lo4y": lo, "pos4y": pos,
        "p10": pct(closes, .10), "p25": pct(closes, .25), "p50": pct(closes, .50),
        "p75": pct(closes, .75), "p90": pct(closes, .90),
        "hi250": hi250, "lo250": lo250, "hi60": hi60, "lo60": lo60,
        "vol_ann": ann * 100, "dstd": dstd * 100, "atr_pct": atr_pct, "amp20": amp20,
        "mdd": mdd * 100, "cagr": ((last / closes[0]) ** (244 / len(closes)) - 1) * 100,
        "target": target, "stop": stop,
        "target_vs_hi4y": (target / hi - 1) * 100,
        "stop_vs_lo4y": (stop / lo - 1) * 100,
        "target_hit_hist": target <= hi,
    }


def main():
    want = [a for a in sys.argv[1:] if not a.startswith("-")]
    with open(os.path.join(ROOT, "portfolio.json"), encoding="utf-8") as f:
        pf = json.load(f)

    print(f"【网格参数测算】{datetime.now():%Y-%m-%d %H:%M}  数据：东财日线 不复权\n")
    rows = []
    for h in pf["holdings"]:
        if want and h["code"] not in want:
            continue
        try:
            r = analyse(h["code"], h["name"], h["shares"], h["cost"])
        except SystemExit as e:
            print(f"  !! {h['code']} {e}")
            continue
        if r:
            rows.append(r)

    hdr = ("%-10s %6s %6s %7s %7s %7s %7s %7s %7s %6s %6s %6s %6s"
           % ("名称", "现价", "成本", "盈亏", "4y低", "4y高", "位置", "P25", "P75",
              "年波", "ATR", "振幅20", "回撤"))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("%-10s %6.3f %6.3f %+6.1f%% %7.3f %7.3f %6.0f%% %7.3f %7.3f %5.0f%% %5.1f%% %5.2f%% %6.1f%%"
              % (r["name"][:10], r["now"], r["cost"], r["pnl_pct"],
                 r["lo4y"], r["hi4y"], r["pos4y"], r["p25"], r["p75"],
                 r["vol_ann"], r["atr_pct"], r["amp20"], r["mdd"]))

    print("\n目标/止损可行性（+30% / −15%，均以成本为基准）")
    print("%-10s %7s %7s %7s %7s %8s %8s" % ("名称", "目标价", "止损价", "4y高", "4y低", "目标可及", "止损位距低"))
    print("-" * 68)
    for r in rows:
        ok = "是" if r["target_hit_hist"] else "否"
        print("%-10s %7.3f %7.3f %7.3f %7.3f %8s %+7.1f%%"
              % (r["name"][:10], r["target"], r["stop"], r["hi4y"], r["lo4y"], ok,
                 r["stop_vs_lo4y"]))

    with open(data("grid_data.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print("\n已写出 grid_data.json")


if __name__ == "__main__":
    main()
