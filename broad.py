# -*- coding: utf-8 -*-
"""
宽基 ETF 全景 —— 只看宽基，重点标记国家队载具

口径说明：
  1. 宽基 = 纯指数跟踪，剔除 增强/成长/价值/低波/红利/等权/ESG/行业主题。
  2. 每个指数族只取规模最大的那只作「载具」，避免同一指数十几只重复。
  3. 国家队 = 汇金/证金实际参与申购的六族：沪深300、上证50、中证500、中证1000、科创50、创业板。
     这份名单需用基金季报「十大持有人」核对，代码里标 NEED_VERIFY。
  4. 宽基不用「距历史低点」定位（20 年历史的低点在 2014 年，毫无参考价值），
     只用 4 年区间分位 pos 和年线偏离 ma240_dev。

用法：
    python broad.py            # 打印宽基全景
    python broad.py --json     # 同时落盘 broad_snapshot.json
"""
import json
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

# 指数族：(族名, 匹配关键词, 是否国家队载具)
FAMILY = [
    ("沪深300", ["沪深300"], True),
    ("上证50", ["上证50"], True),
    ("中证500", ["中证500"], True),
    ("中证1000", ["中证1000"], True),
    ("中证2000", ["中证2000"], False),
    ("科创50", ["科创50"], True),
    ("科创100", ["科创100"], False),
    ("创业板指", ["创业板ETF", "创业板指"], True),
    ("创业板50", ["创业板50"], False),
    ("双创50", ["双创50"], False),
    ("中证A500", ["A500"], False),
    ("中证A50", ["A50"], False),
    ("上证180", ["上证180"], False),
    ("深证100", ["深证100"], False),
    ("中证800", ["中证800"], False),
    ("北证50", ["北证50"], False),
    ("中证100", ["中证100"], False),
    ("上证380", ["上证380"], False),
]
# 这些词出现即不是纯宽基
BAD = ["增强", "成长", "价值", "低波", "红利", "等权", "ESG", "行业", "主题",
       "龙头", "优选", "精选", "策略", "联接", "货币", "债", "黄金", "纳指",
       "标普", "恒生", "港股", "美国", "日本", "德国", "法国", "沙特", "越南"]


def get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode())


def fetch_all():
    out, pn = [], 1
    while True:
        u = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=200&po=1&np=1&fltt=2&invt=2"
             "&fid=f6&fs=b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827"
             "&fields=f2,f3,f6,f12,f13,f14,f20" % pn)
        d = get(u)["data"]
        if not d or not d.get("diff"):
            break
        out += d["diff"]
        if len(out) >= d["total"]:
            break
        pn += 1
    return out


def family_of(name):
    if any(b in name for b in BAD):
        return None
    for fam, kws, nat in FAMILY:
        if any(k in name for k in kws):
            return fam, nat
    return None


def main():
    items = fetch_all()
    print("全市场 ETF：%d 只" % len(items))

    best = {}   # 族名 -> item
    for it in items:
        name = it.get("f14") or ""
        r = family_of(name)
        if not r:
            continue
        fam, nat = r
        aum = it.get("f20") or 0
        if fam not in best or aum > (best[fam].get("f20") or 0):
            best[fam] = {**it, "_nat": nat}

    # 位置数据来自 sweep_result.json
    pos = {}
    sp = os.path.join(BASE, "sweep_result.json")
    if os.path.exists(sp):
        for r in json.load(open(sp, encoding="utf-8")):
            pos[r["code"]] = r

    rows = []
    for fam, it in best.items():
        code = it["f12"]
        s = pos.get(code, {})
        rows.append({
            "family": fam,
            "code": code,
            "name": it["f14"],
            "close": it.get("f2") or 0,
            "chg": it.get("f3") or 0,
            "amt": (it.get("f6") or 0) / 1e8,
            "aum": (it.get("f20") or 0) / 1e8,
            "pos": s.get("pos"),
            "ma240_dev": s.get("ma240_dev"),
            "nat": it["_nat"],
        })
    rows.sort(key=lambda r: -r["aum"])

    print("\n%-9s %-8s %-16s %7s %7s %9s %7s %8s %7s %s" %
          ("指数族", "代码", "场内最大载具", "收盘", "今日%", "规模(亿)", "4年分位", "距年线%", "成交/规模", "国家队"))
    print("-" * 104)
    for r in rows:
        p = "%3.0f%%" % r["pos"] if r["pos"] is not None else "   —"
        m = "%+6.1f%%" % r["ma240_dev"] if r["ma240_dev"] is not None else "     —"
        act = "%5.2f%%" % (r["amt"] / r["aum"] * 100) if r["aum"] else "    —"
        print("%-9s %-8s %-16s %7.3f %+6.2f%% %9.0f %7s %8s %7s %s" % (
            r["family"], r["code"], r["name"][:14], r["close"], r["chg"],
            r["aum"], p, m, act, "★" if r["nat"] else ""))

    nat = [r for r in rows if r["nat"]]
    tot = sum(r["aum"] for r in rows)
    ntot = sum(r["aum"] for r in nat)
    print("-" * 104)
    print("宽基合计规模 {:,.0f} 亿｜国家队六族载具 {:,.0f} 亿（占宽基 {:.0f}%）｜今日成交 {:,.0f} 亿".format(
        tot, ntot, ntot / tot * 100, sum(r["amt"] for r in nat)))

    if "--json" in sys.argv:
        with open(os.path.join(BASE, "broad_snapshot.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print("\n已写入 broad_snapshot.json（%d 个指数族）" % len(rows))


if __name__ == "__main__":
    main()
