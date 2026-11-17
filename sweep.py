#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sweep.py —— 全市场 ETF 大级别位置扫描（交易员视角，不做基本面分析）

逻辑：只回答两个问题
  1) 现在离「多年大支撑」还有多远？
  2) 现在离「多年大压力」还有多远？

用法：
    python sweep.py                # 全市场
    python sweep.py --min-amt 3000 # 只扫成交额>=3000万的
输出：sweep_result.json
"""
import json
import math
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
      "Referer": "https://quote.eastmoney.com/"}
EM_LIST = "https://push2.eastmoney.com/api/qt/clist/get"
EM_HIS = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
HERE = os.path.dirname(os.path.abspath(__file__))


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def secid(code, mkt):
    return ("1." if str(mkt) == "1" else "0.") + code


def etf_list():
    out, pn = [], 1
    while True:
        u = (f"{EM_LIST}?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f6"
             f"&fs=b:MK0021,b:MK0022,b:MK0023,b:MK0024"
             f"&fields=f12,f13,f14,f2,f3,f6")
        d = _get(u)["data"]
        if not d or not d.get("diff"):
            break
        for x in d["diff"]:
            try:
                out.append({"code": x["f12"], "mkt": x["f13"], "name": x["f14"],
                            "close": float(x["f2"]), "chg": float(x["f3"]),
                            "amt": float(x["f6"])})
            except (TypeError, ValueError):
                continue
        if len(out) >= d["total"]:
            break
        pn += 1
    return out


BASE_RE = re.compile(r"^(.*?ETF)")


def base_name(name):
    m = BASE_RE.match(name)
    return m.group(1) if m else name


def weekly(code, mkt):
    u = (f"{EM_HIS}?secid={secid(code, mkt)}&ut=fa5fd1943c7b386f172d6893dbfba10b"
         f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
         f"&klt=102&fqt=2&beg=20050101&end=20500101")
    try:
        d = _get(u).get("data")
        if not d or not d.get("klines"):
            return None
        bars = []
        for k in d["klines"]:
            p = k.split(",")
            bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                         "h": float(p[3]), "l": float(p[4]),
                         "v": float(p[5]), "a": float(p[6])})
        return bars
    except Exception:
        return None


def stats(item, bars):
    # 后复权：历史连续、不会出现负价；再按最新真实价整体缩放到可交易价位
    k = item["close"] / bars[-1]["c"] if bars[-1]["c"] else 1.0
    bars = [{**b, "o": b["o"] * k, "c": b["c"] * k, "h": b["h"] * k, "l": b["l"] * k}
            for b in bars]
    n = len(bars)
    if n < 156:          # 少于 3 年，谈不上大级别
        return None
    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    last = closes[-1]
    hi, lo = max(highs), min(lows)

    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, n) if closes[i - 1] > 0]
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - mu) ** 2 for x in rets) / (len(rets) - 1))
    vol_ann = sd * math.sqrt(52) * 100

    ma240 = sum(closes[-240:]) / 240 if n >= 240 else None
    ma120 = sum(closes[-120:]) / 120 if n >= 120 else None

    dist_low = (last / lo - 1) * 100
    dist_hi = (last / hi - 1) * 100
    pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50

    v4 = sum(b["v"] for b in bars[-4:]) / 4
    v52 = sum(b["v"] for b in bars[-52:]) / min(52, n)
    shrink = v4 / v52 if v52 else 1.0

    # 近 26 周涨跌
    chg26 = (last / closes[-27] - 1) * 100 if n > 27 else None

    # 最近一次触及历史低点距今多少周
    lo_idx = lows.index(lo)
    since_lo = n - 1 - lo_idx

    # 支撑带判定：距历史低点 12% 内，或距 MA240 在 8% 内
    near_low = dist_low <= 12
    near_ma240 = (ma240 is not None and abs(last / ma240 - 1) <= 0.08)
    support = (near_low and since_lo >= 26) or near_ma240

    years = n / 52.0

    # 打分（做多候选：跌得深 + 离底近 + 历史长 + 流动性好 + 缩量）
    s = 0.0
    s += max(0.0, 40 - dist_low * 2.2)          # 离底越近越高，满分 40
    s += min(25.0, abs(min(dist_hi, 0)) * 0.42)  # 回撤越深越高，满分 25
    s += min(15.0, years * 2.2)                  # 历史越长越可信
    s += min(12.0, math.log10(max(item["amt"], 1)) * 1.6)
    if shrink < 0.85:
        s += 8.0 if shrink < 0.6 else 4.0        # 缩量代表卖压衰竭
    if support:
        s += 6.0
    if since_lo >= 52:
        s += 6.0                       # 一年以上的老底被回踩，含金量高
    if since_lo < 20:
        s -= 15.0                      # 这个底是刚创的，还在下跌通道里
    if vol_ann < 12:
        return None                              # 货币/债券类，不参与
    if years < 3:
        s -= 10.0                                # 历史太短，谈不上大级别

    return {
        "code": item["code"], "name": item["name"], "base": base_name(item["name"]),
        "close": last, "chg": item["chg"], "amt": item["amt"],
        "years": years, "bars": n,
        "hi": hi, "lo": lo, "pos": pos,
        "dist_low": dist_low, "dd": dist_hi,
        "ma240": ma240, "ma120": ma120,
        "ma240_dev": (last / ma240 - 1) * 100 if ma240 else None,
        "vol_ann": vol_ann, "shrink": shrink, "chg26": chg26,
        "since_lo": since_lo, "support": support,
        "score": s,
    }


def main():
    min_amt = 1500.0
    if "--min-amt" in sys.argv:
        min_amt = float(sys.argv[sys.argv.index("--min-amt") + 1])

    print("拉取全市场 ETF 列表 ...")
    lst = etf_list()
    print(f"  共 {len(lst)} 只")

    lst = [x for x in lst if x["amt"] >= min_amt * 1e4]
    print(f"  成交额 ≥ {min_amt:.0f} 万：{len(lst)} 只")

    # 同名指数只留成交额最大的一只
    best = {}
    for x in lst:
        b = base_name(x["name"])
        if b not in best or x["amt"] > best[b]["amt"]:
            best[b] = x
    lst = sorted(best.values(), key=lambda x: -x["amt"])
    print(f"  按跟踪指数去重后：{len(lst)} 只\n")

    results = []

    def work(it):
        bars = weekly(it["code"], it["mkt"])
        if not bars:
            return None
        return stats(it, bars)

    with ThreadPoolExecutor(max_workers=24) as ex:
        for i, r in enumerate(ex.map(work, lst), 1):
            if r:
                results.append(r)
            if i % 50 == 0:
                print(f"  进度 {i}/{len(lst)}", flush=True)

    results.sort(key=lambda x: -x["score"])

    print(f"\n有效样本 {len(results)} 只\n")
    print("%-10s %-8s %6s %7s %6s %7s %6s %5s %6s %6s"
          % ("名称", "代码", "现价", "回撤", "位置", "距底", "MA240", "年数", "波动", "评分"))
    print("-" * 78)
    for r in results[:30]:
        print("%-10s %-8s %6.3f %6.0f%% %5.0f%% %6.1f%% %6.1f%% %5.1f %5.0f%% %6.1f"
              % (r["name"][:10], r["code"], r["close"], r["dd"], r["pos"],
                 r["dist_low"], r["ma240_dev"] or 0, r["years"], r["vol_ann"], r["score"]))

    json.dump(results, open(os.path.join(HERE, "sweep_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n已写出 sweep_result.json（{len(results)} 条）")


if __name__ == "__main__":
    main()
