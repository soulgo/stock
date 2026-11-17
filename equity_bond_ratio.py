#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股债比价触线监测
================
指标　沪深300全收益(H00300) ÷ 万得混合债券型二级基金指数(885007.WI)
含义　两线重合（比价=1.0）意味着「承担股票的风险，却拿不到超过债券的补偿」
阈值　1.0 为触线（历史级便宜区域的入口），0.95~0.97 为惯性区

两条腿的取数
------------
腿A 沪深300全收益：中证官网 H00300 日收盘，**精确可取**。
腿B 万得 885007：Wind 专有、无公开接口。做法是「锚点 + 代理推进」：
     B(t) = B_anchor × (1 + r_中证全债) × (1 + β × r_沪深300)
     B_anchor = 5121.91 @ 2026-11-13（博主公布值，可用 --bond 覆盖）
     β = 0.12（二级债基约一成股票仓位，对沪深300的经验弹性）
     代理只影响「比价向前走多快」，不影响锚点那一天的绝对值；
     若拿到万得新数字，改 data/equity_bond_anchor.json 重新锚定即可。

用法
----
    python equity_bond_ratio.py              # 打今天的状态
    python equity_bond_ratio.py --html       # 另存手机端看板到 reports/
    python equity_bond_ratio.py --bond 5098.4 --date 2026-12-05   # 用新公布值重新锚定
    python equity_bond_ratio.py --price 3900                      # 假设点位测算

产出
----
    data/equity_bond_ratio.csv               自建序列（每跑一次追加/更新当日行）
    reports/股债比价看板_<date>.html          --html 时生成
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

ANCHOR_F = os.path.join(BASE, "data", "equity_bond_anchor.json")
CSV_F = os.path.join(BASE, "data", "equity_bond_ratio.csv")
REPORT_DIR = os.path.join(BASE, "reports")

# 阈值档位：比价 → (名称, 说明)。从上往下排，取第一个 r >= 阈值 的档。
# 下沿（≤1.0）= 股票便宜，定量、可当扳机；上沿（≥1.3）= 股票贵过债，只能当温度计。
BANDS = [
    (1.80, "泡沫区", "2007 才出现过（2.4），股票腿清到最低"),
    (1.50, "贵·减股", "四次峰值水平，股票腿该降下来、新钱往债挪"),
    (1.30, "偏贵", "开始减仓，宽基不再新增"),
    (1.15, "观察区·远", "不因这个指标动仓"),
    (1.10, "观察区·近", "开始记周读数，宽基不新增大额"),
    (1.05, "预备区", "现金留足，宽基只做左侧准备"),
    (1.00, "第一档", "区间上沿，可投第一笔不超过计划额 1/3"),
    (0.97, "触线区", "分批推进，每批间隔 ≥10 个交易日或再跌 3%"),
    (0.95, "惯性区", "历史四次里有三次跌破此位，剩余弹药留到这里"),
    (-9e9, "极限区", "历史最深 0.78（2008），此时该想的是敢不敢，不是买不买"),
]

# 上沿要盯的两个水平（下沿的 1.00/0.97/0.95 在 BANDS 里）
UPPER = [1.50, 1.30]


def band_of(r):
    return next(((n, s) for th, n, s in BANDS if r >= th), BANDS[-1][1:])

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ----------------------------------------------------------------- 取数
def _http(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Referer": "https://www.csindex.com.cn/"})
            with urllib.request.urlopen(req, timeout=12) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
    raise RuntimeError(f"取数失败 {url[:70]} ({last})")


def csindex_series(code, beg, end):
    """中证官网日收盘序列 → [(YYYY-MM-DD, close), ...]，按日期升序、去重"""
    url = ("https://www.csindex.com.cn/csindex-home/perf/index-perf"
           f"?indexCode={code}&startDate={beg}&endDate={end}")
    js = json.loads(_http(url))
    if str(js.get("code")) != "200" or not js.get("data"):
        raise RuntimeError(f"中证 {code} 返回异常: {str(js)[:120]}")
    out = {}
    for r in js["data"]:
        c = r.get("close")
        d = r.get("tradeDate") or ""
        if c and len(d) == 8:
            out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = float(c)
    return sorted(out.items())


def load_anchor():
    with open(ANCHOR_F, encoding="utf-8") as f:
        return json.load(f)


def realtime_hs300():
    """沪深300 价格实时（连接器快照 → 东财 → 新浪，复用 brief.py 的降级链）"""
    try:
        import brief
        brief.load_mx()
        q = brief.index_quote("1.000300", "沪深300")
        if q.get("ok") and q.get("price"):
            return float(q["price"]), (q.get("src") or "实时")
    except Exception:
        pass
    return None, None


# ----------------------------------------------------------------- 计算
K_BAND = 0.003          # 全收益/价格 换算系数的容差（±0.3%）
K_REF_FALLBACK = 1.50595


def fetch_pool(code, beg, end, tries=3):
    """中证官网会返回滞后一天、月份边界重复值的坏窗口（实测 2026-11-01/02 同值），
    单次取数不可信 —— 连取多次，把每个日期的**候选值**都留下，后面用 k 系数配对。"""
    pool = {}
    for _ in range(tries):
        try:
            for d, v in csindex_series(code, beg, end):
                pool.setdefault(d, [])
                if v not in pool[d]:
                    pool[d].append(v)
        except Exception:
            pass
        time.sleep(0.35)
    return pool


def pair_series(tr_pool, px_pool, k_ref):
    """按 k=全收益/价格 配对。错位一天的两个序列，k 会偏 1% 以上，直接露馅；
    一格里有多候选值时，取最贴 k_ref 的那对。"""
    paired = {}
    for d in set(tr_pool) & set(px_pool):
        best = None
        for a in tr_pool[d]:
            for p in px_pool[d]:
                if not p:
                    continue
                e = abs(a / p / k_ref - 1)
                if e < K_BAND and (best is None or e < best[0]):
                    best = (e, a, p)
        if best:
            paired[d] = (best[1], best[2])
    return paired


def build(anchor, price_override=None, tries=3):
    a_date = anchor["anchor_date"]
    beg = a_date.replace("-", "")
    end = datetime.now().strftime("%Y%m%d")

    tr_pool = fetch_pool("H00300", beg, end, tries)       # 全收益
    px_pool = fetch_pool("000300", beg, end, tries)       # 价格
    bd_pool = fetch_pool(anchor.get("bond_proxy", "H11001"), beg, end, tries)  # 债腿代理

    # k 参照优先用锚点里博主给的两个数（H00300 & 000300 同日），最可靠
    k_ref = K_REF_FALLBACK
    if anchor.get("hs300_tr") and anchor.get("hs300_price"):
        k_ref = float(anchor["hs300_tr"]) / float(anchor["hs300_price"])

    paired = pair_series(tr_pool, px_pool, k_ref)
    # 锚点日的数博主已公布，以锚点 JSON 为准（防止中证当天返回错位把锚点带歪）
    if anchor.get("hs300_tr") and anchor.get("hs300_price"):
        paired[a_date] = (float(anchor["hs300_tr"]), float(anchor["hs300_price"]))
    if a_date not in paired:
        raise RuntimeError(f"锚点日 {a_date} 的 H00300/000300 无法配对，检查数据源")

    # 债腿：一个日期多候选值时取中位数（错位一天只差 0.03%，不影响判断）
    bd = {d: sorted(v)[len(v) // 2] for d, v in bd_pool.items() if v}

    beta = float(anchor.get("beta", 0.12))
    b0 = float(anchor["bond_index"])

    # 逐日推进债腿：日期对齐三列，缺就沿用前值（中证全债常 T+1 才出）
    days = sorted(d for d in (set(paired) | set(bd)) if d >= a_date)
    bd_prev = bd.get(a_date) or next((bd[d] for d in sorted(bd) if d >= a_date), None)
    tr_prev = paired[a_date][0]
    b_cur = b0
    rows = []
    for d in days:
        if d != a_date:
            r_b = (bd[d] / bd_prev - 1.0) if (bd.get(d) and bd_prev) else 0.0
            r_e = (paired[d][0] / tr_prev - 1.0) if (d in paired and tr_prev) else 0.0
            b_cur = b_cur * (1 + r_b) * (1 + beta * r_e)
            if bd.get(d):
                bd_prev = bd[d]
            if d in paired:
                tr_prev = paired[d][0]
        if d not in paired:
            continue
        a_v, p = paired[d]
        rows.append({
            "date": d,
            "hs300_tr": a_v,
            "hs300_px": p,
            "bond": round(b_cur, 2),
            "ratio": a_v / b_cur,
            "p100": b_cur / (a_v / p),
            "k": a_v / p,
        })

    if not rows:
        raise RuntimeError("没有可用交易日")

    last = rows[-1]
    out = {
        "anchor_date": a_date,
        "anchor_bond": b0,
        "anchor_ratio_rep": anchor.get("ratio_reported"),
        "asof": last["date"],
        "hs300_tr": last["hs300_tr"],
        "hs300_px": last["hs300_px"],
        "bond": last["bond"],
        "ratio": last["ratio"],
        "k": last["k"],
        "p100": last["p100"],
        "p097": last["bond"] * 0.97 / last["k"],
        "p095": last["bond"] * 0.95 / last["k"],
        "beta": beta,
        "rows": rows,
    }

    # 实时价覆盖（盘中估算：债腿视作不动，只让股票腿动）
    rt, rt_src = (None, None) if price_override else realtime_hs300()
    if price_override:
        rt, rt_src = float(price_override), "手动"
    # 实时价与官网收盘一致 → 说明收盘数据已出，没必要走盘中估算
    if rt and not price_override and abs(rt / last["hs300_px"] - 1) < 0.0015:
        out["verify"] = f"实时报价 {rt:,.2f} 与官网收盘一致"
        rt = None
    if rt:
        r_e = rt / last["hs300_px"] - 1.0
        a = last["hs300_tr"] * (1 + r_e)
        b = last["bond"] * (1 + beta * r_e)
        out.update({
            "live": True, "live_src": rt_src,
            "hs300_px_live": rt, "hs300_tr_live": a, "bond_live": b,
            "ratio_live": a / b,
            "p100_live": b / last["k"],
            "p097_live": b * 0.97 / last["k"],
            "p095_live": b * 0.95 / last["k"],
        })
    else:
        out["live"] = False

    r = out["ratio_live"] if out["live"] else out["ratio"]
    px_now = out["hs300_px_live"] if out["live"] else out["hs300_px"]
    b_now = out["bond_live"] if out["live"] else out["bond"]
    out["band"] = band_of(r)
    out["dist100"] = (out["p100_live"] / out["hs300_px_live"] - 1) if out["live"] \
        else (out["p100"] / out["hs300_px"] - 1)
    # 上沿点位（股票贵过债的水平）
    out["p_upper"] = {th: th * b_now / out["k"] for th in UPPER}
    # 股债走廊：下沿 1.00 线 → 上沿 1.50 线，现在落在几成位置
    lo_p, hi_p = (out["p100_live"] if out["live"] else out["p100"]), out["p_upper"][1.50]
    out["corridor"] = {
        "lo": lo_p, "hi": hi_p, "px": px_now,
        "frac": max(0.0, min(1.0, (px_now - lo_p) / (hi_p - lo_p))),
    }
    return out


def bond_temp():
    """债端温度：国债 ETF 的 52 周分位。这条比价线答不了「债本身贵不贵」，
    用这个当粗代理 —— 分位越高 = 债越贵 = 收益率越低。"""
    out = []
    try:
        import brief
        for code, nm in [("511260", "十年国债ETF"), ("511010", "国债ETF"), ("511090", "30年国债ETF")]:
            try:
                q = brief.quote(code)
                p = q["price"]
                h, l = q.get("high_52week"), q.get("low_52week")
                yearly = None
                if not h or not l:
                    bars = (brief.daily(code) or ([], ""))[0][-250:]
                    if not bars:
                        continue
                    h = max(b["high"] for b in bars)
                    l = min(b["low"] for b in bars)
                    yearly = p / bars[0]["close"] - 1
                frac = (p - l) / (h - l) if h > l else None
                out.append({"code": code, "name": nm, "px": p, "frac": frac, "yearly": yearly})
            except Exception:
                pass
    except Exception:
        pass
    return out


def to_csv(o):
    os.makedirs(os.path.dirname(CSV_F), exist_ok=True)
    head = ["date", "hs300_tr", "hs300_px", "bond_idx", "ratio", "p_line100", "p_line097", "p_line095", "k"]
    old = []
    if os.path.exists(CSV_F):
        with open(CSV_F, encoding="utf-8-sig") as f:
            old = [r for r in csv.DictReader(f) if r.get("date")]
    new = [r for r in old if r["date"] not in {x["date"] for x in o["rows"]}]
    for r in o["rows"]:
        new.append({"date": r["date"], "hs300_tr": f"{r['hs300_tr']:.2f}",
                    "hs300_px": f"{r['hs300_px']:.2f}", "bond_idx": f"{r['bond']:.2f}",
                    "ratio": f"{r['ratio']:.4f}", "p_line100": f"{r['p100']:.1f}",
                    "p_line097": f"{r['p100'] * 0.97:.1f}", "p_line095": f"{r['p100'] * 0.95:.1f}",
                    "k": f"{r['k']:.5f}"})
    new.sort(key=lambda x: x["date"])
    with open(CSV_F, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=head)
        w.writeheader()
        w.writerows(new)
    return len(new)


# ----------------------------------------------------------------- 输出
def pct(x, d=2):
    return f"{x * 100:+.{d}f}%"


def text_report(o):
    r = o["ratio_live"] if o["live"] else o["ratio"]
    px = o["hs300_px_live"] if o["live"] else o["hs300_px"]
    p100 = o["p100_live"] if o["live"] else o["p100"]
    p097 = o["p097_live"] if o["live"] else o["p097"]
    p095 = o["p095_live"] if o["live"] else o["p095"]
    L = []
    L.append(f"股债比价触线　{o['asof']}" + ("　（盘中估算）" if o["live"] else "　（官网收盘）"))
    L.append("=" * 46)
    L.append(f"比价　　　{r:.3f}　　（锚点 {o['anchor_date']} 报 {o['anchor_ratio_rep']}）")
    L.append(f"档位　　　{o['band'][0]} —— {o['band'][1]}")
    L.append(f"沪深300　{px:,.2f}（全收益 {o['hs300_tr_live'] if o['live'] else o['hs300_tr']:,.2f}）")
    L.append(f"债腿代理　{o['bond_live'] if o['live'] else o['bond']:,.2f}"
             f"　（β={o['beta']}，代理非万得原指数）")
    L.append("")
    L.append(f"1.00 线　　{p100:,.0f} 点　距现价 {pct(o['dist100'])}")
    L.append(f"0.97 线　　{p097:,.0f} 点　距现价 {pct(p097 / px - 1)}")
    L.append(f"0.95 线　　{p095:,.0f} 点　距现价 {pct(p095 / px - 1)}")
    L.append("　────────── 上沿（股票贵过债）──────────")
    for th in sorted(o["p_upper"], reverse=True):
        pv = o["p_upper"][th]
        L.append(f"{th:.2f} 线　　{pv:,.0f} 点　距现价 {pct(pv / px - 1)}")
    c = o["corridor"]
    L.append(f"走廊位置　{c['frac'] * 100:.0f}%　（0%={c['lo']:,.0f} 触线，100%={c['hi']:,.0f} 偏贵）")
    if o.get("bond_temp"):
        L.append("")
        L.append("债端温度（国债ETF 52 周分位，越高越贵）")
        for t in o["bond_temp"]:
            if t["frac"] is None:
                continue
            L.append(f"  {t['code']} {t['name']:<10} {t['px']:.3f}　52周分位 {t['frac'] * 100:.0f}%"
                     + (f"　近一年 {pct(t['yearly'])}" if t.get("yearly") is not None else ""))
    L.append("")
    etfs = [("510300", "沪深300ETF华泰柏瑞"), ("510310", "沪深300ETF易方达"), ("159919", "沪深300ETF嘉实")]
    try:
        import brief
        for code, name in etfs:
            try:
                q = brief.quote(code)
                p = q["price"]
                m = p / px
                L.append(f"  {code} {name[:12]:<12} {p:.3f}　1.00线≈{p100 * m:.3f}　0.97线≈{p097 * m:.3f}　0.95线≈{p095 * m:.3f}")
            except Exception:
                pass
    except Exception:
        pass
    if o["live"]:
        L.append("")
        L.append("※ 盘中口径：债腿当不动，只让股票腿动，收盘后重跑才是官方值。")
    return "\n".join(L)


# ----------------------------------------------------------------- 看板
CSS = r"""
:root{--bg:#f2f3f6;--card:#fff;--line:#e2e5eb;--soft:#eef0f4;--ink:#14171c;--ink2:#4a5058;
--ink3:#8b929d;--up:#cf3126;--upbg:#fdecea;--down:#0d8f66;--downbg:#e7f5f0;
--buy:#1d4ed8;--buybg:#eaf0fe;--sell:#b45309;--sellbg:#fdf3e4;
--mono:"SF Mono","JetBrains Mono",Consolas,Menlo,monospace;
--sh:0 1px 2px rgba(18,23,32,.05),0 10px 26px -16px rgba(18,23,32,.22)}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:15px/1.62 "PingFang SC","Hiragino Sans GB","Microsoft YaHei",-apple-system,sans-serif;padding:14px 14px 80px;-webkit-font-smoothing:antialiased}
.wrap{max-width:680px;margin:0 auto}
header{margin-bottom:14px;padding:0 2px}
.eyebrow{font-size:11px;letter-spacing:.14em;color:var(--ink3);font-weight:700;margin-bottom:5px}
h1{font-size:clamp(21px,5.6vw,27px);font-weight:800;letter-spacing:-.02em;line-height:1.28}
.meta{margin-top:8px;display:flex;flex-wrap:wrap;gap:6px;font-size:11.5px;color:var(--ink3)}
.card{background:var(--card);border-radius:14px;padding:16px 15px;box-shadow:var(--sh);margin-bottom:12px}
.hero{text-align:center;padding:18px 15px 16px}
.h-lbl{font-size:11.5px;color:var(--ink3);font-weight:700;letter-spacing:.1em}
.h-px{font-family:var(--mono);font-size:clamp(44px,14vw,60px);font-weight:800;line-height:1.05;letter-spacing:-.035em;margin:2px 0 4px}
.h-sub{font-size:12.5px;color:var(--ink2)}
.tag{display:inline-block;font-size:11px;font-weight:800;padding:2px 9px;border-radius:99px;background:var(--buybg);color:var(--buy);margin-top:8px}
.gauge{margin:14px 0 0;position:relative;height:74px}
.g-track{position:absolute;left:0;right:0;top:36px;height:10px;border-radius:6px;
background:linear-gradient(90deg,#f1d8d2 0%,#f8e6dd 26%,#eef0f4 55%,#e3ecfd 80%,#d6e2fd 100%)}
.g-mark{position:absolute;top:26px;width:2px;height:30px;background:var(--ink3);opacity:.55}
.g-mark.g1{height:24px;top:30px}
.g-lab{position:absolute;top:58px;transform:translateX(-50%);font-family:var(--mono);font-size:10.5px;color:var(--ink3);font-weight:700;white-space:nowrap}
.g-now{position:absolute;top:0;transform:translateX(-50%);text-align:center}
.g-now i{display:block;width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-top:8px solid var(--buy);margin:0 auto}
.g-now b{font-family:var(--mono);font-size:12px;font-weight:800;color:var(--buy);display:block;margin-top:2px}
h2{font-size:13.5px;font-weight:800;margin-bottom:10px;display:flex;align-items:center;gap:7px}
h2:before{content:"";width:3px;height:14px;border-radius:2px;background:var(--buy)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-size:10.5px;color:var(--ink3);font-weight:700;text-align:right;padding:0 0 7px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
td{padding:9px 0;border-bottom:1px solid var(--soft);text-align:right;font-family:var(--mono);font-weight:700;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}
td .nm{font-family:inherit;font-weight:800}
td .sm{display:block;font-family:inherit;font-size:10px;color:var(--ink3);font-weight:600;margin-top:1px}
.row-hit td{color:var(--buy)}
.neg{color:var(--down)}.pos{color:var(--up)}
.notes{font-size:12px;color:var(--ink2);line-height:1.75}
.notes b{color:var(--ink)}
.notes li{margin-left:16px;margin-bottom:5px}
.hist{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;-webkit-overflow-scrolling:touch}
.hit{flex:0 0 auto;min-width:104px;background:var(--soft);border-radius:10px;padding:9px 10px}
.hit b{display:block;font-family:var(--mono);font-size:17px;font-weight:800}
.hit span{display:block;font-size:10.5px;color:var(--ink3);font-weight:700;margin-top:1px}
.hit em{display:block;font-style:normal;font-size:11px;color:var(--ink2);margin-top:3px}
.warn{background:var(--sellbg);border-radius:12px;padding:12px 14px;font-size:12px;color:#7c4a09;line-height:1.7}
.warn b{color:#5c3506}
footer{margin-top:16px;font-size:10.5px;color:var(--ink3);line-height:1.7;text-align:center}
.corr{position:relative;height:60px;margin:2px 0 0}
.corr-bar{position:absolute;left:0;right:0;top:22px;height:10px;border-radius:6px;
background:linear-gradient(90deg,#1d4ed8 0%,#8fa8ec 16%,#dfe3ea 48%,#e8b48c 80%,#cf3126 100%)}
.corr-pt{position:absolute;top:8px;transform:translateX(-50%);text-align:center}
.corr-pt i{display:block;width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:7px solid var(--ink);margin:0 auto}
.corr-pt b{display:block;font-family:var(--mono);font-size:11.5px;font-weight:800;margin-top:1px}
.corr-end{position:absolute;top:38px;font-family:var(--mono);font-size:10.5px;font-weight:700;color:var(--ink3);line-height:1.35}
.corr-end.l{left:0}
.corr-end.r{right:0;text-align:right}
.corr-end b{display:block;font-size:12px}
.brow{display:flex;align-items:center;gap:9px;padding:7px 0;border-bottom:1px solid var(--soft);font-size:12px}
.brow:last-child{border-bottom:0}
.brow .bn{flex:0 0 84px;font-weight:700}
.brow .bp{flex:0 0 56px;text-align:right;font-family:var(--mono);font-weight:700}
.brow .bb{flex:1;height:6px;border-radius:3px;background:var(--soft);position:relative;overflow:hidden}
.brow .bb i{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--sell)}
.brow .bf{flex:0 0 38px;text-align:right;font-family:var(--mono);font-weight:800;color:var(--sell)}
"""


def html_report(o):
    r = o["ratio_live"] if o["live"] else o["ratio"]
    px = o["hs300_px_live"] if o["live"] else o["hs300_px"]
    p100 = o["p100_live"] if o["live"] else o["p100"]
    p097 = o["p097_live"] if o["live"] else o["p097"]
    p095 = o["p095_live"] if o["live"] else o["p095"]
    bond = o["bond_live"] if o["live"] else o["bond"]
    p150 = o["p_upper"][1.50]
    p130 = o["p_upper"][1.30]
    corr = o["corridor"]

    # 刻度尺：1.55（左·贵）→ 0.92（右·便宜）
    hi, lo = 1.55, 0.92
    pos = lambda v: max(0.0, min(100.0, (hi - v) / (hi - lo) * 100))
    marks = "".join(
        f'<div class="g-mark{" g1" if v in (1.0, 1.5) else ""}" style="left:{pos(v):.1f}%"></div>'
        + (f'<div class="g-lab" style="left:{pos(v):.1f}%">{lab}</div>' if lab else "")
        for v, lab in [(1.50, "1.50 贵"), (1.30, "1.30"), (1.15, "1.15"),
                       (1.00, "1.00 触线"), (0.95, "")])

    # 双向档位表
    rows = [(1.50, "1.50", "贵·减股"), (1.30, "1.30", "偏贵"),
            (None, "现在", o["band"][0]),
            (1.05, "1.05", "预备"), (1.00, "1.00", "触线"), (0.97, "0.97", ""), (0.95, "0.95", "惯性")]
    t1 = ""
    for th, name, sub in rows:
        if th is None:
            ratio_v, p_v = r, px
        else:
            ratio_v, p_v = th, p100 * th
        d = p_v / px - 1
        cls = ' class="row-hit"' if th is None else ""
        dtxt = "—" if th is None else pct(d, 1)
        dcls = "" if th is None else ("neg" if d < 0 else "pos")
        t1 += (f"<tr{cls}><td><span class='nm'>{name}</span><span class='sm'>{sub}</span></td>"
               f"<td style='color:var(--ink3)'>{ratio_v:.3f}</td>"
               f"<td>{p_v:,.0f}</td><td class='{dcls}'>{dtxt}</td></tr>")

    # 换算到载具
    etf, t2 = [], ""
    try:
        import brief
        for code, nm in [("510300", "华泰柏瑞300"), ("510310", "易方达300"), ("159919", "嘉实300")]:
            q = brief.quote(code)
            e, m = q["price"], q["price"] / px
            etf.append((code, nm, e, p100 * m, p150 * m))
    except Exception:
        pass
    for code, nm, e, a1, a2 in etf:
        t2 += (f"<tr><td><span class='nm'>{nm}</span><span class='sm'>{code}</span></td>"
               f"<td>{e:.3f}</td><td style='color:var(--buy)'>{a1:.3f}</td>"
               f"<td>{a2:.3f}</td></tr>")

    # 债端温度
    t3 = ""
    for t in o.get("bond_temp") or []:
        if t["frac"] is None:
            continue
        t3 += (f'<div class="brow"><span class="bn">{t["name"]}</span>'
               f'<span class="bp">{t["px"]:.3f}</span>'
               f'<span class="bb"><i style="width:{max(3, t["frac"] * 100):.0f}%"></i></span>'
               f'<span class="bf">{t["frac"] * 100:.0f}%</span></div>')

    hist = [("2008-11", "0.78", "金融危机，沪深300 见 1606"),
            ("2014-06", "0.87", "牛市启动前，2100 一线"),
            ("2016-01", "0.95", "熔断底 2821"),
            ("2024", "1.017", "未真正跌破，随后快速反弹")]
    hits = "".join(f"<div class='hit'><b>{v}</b><span>{d}</span><em>{n}</em></div>"
                   for d, v, n in hist)

    asof_txt = o["asof"] + ("　盘中估算" if o["live"] else "　官网收盘")
    live_note = ("　·　盘中口径：债腿当不动，收盘后重跑为准" if o["live"]
                 else ("　·　" + o["verify"] if o.get("verify") else ""))
    a500 = ""
    try:
        import brief
        q = brief.quote("563800")
        d = p100 / px - 1
        a500 = (f"<div class='notes' style='margin-top:8px;border-top:1px dashed var(--line);padding-top:9px'>"
                f"<b>563800（中证A500，不是沪深300）</b>　现价 {q['price']:.3f}。"
                f"若沪深300 跌到 1.00 线（{pct(d, 1)}），按同比例约 <b>{q['price'] * (1 + d):.3f}</b>；"
                f"A500 中盘权重更高，弱市里通常跌得更多，实际可能更低 2%~4%。</div>")
    except Exception:
        pass

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>股债比价触线 {o['asof']}</title><style>{CSS}</style></head><body><div class="wrap">
<header><div class="eyebrow">股债比价 · 双向 · H00300 ÷ 885007</div>
<h1>沪深300全收益 ÷ 二级债基指数</h1>
<div class="meta"><span>{asof_txt}</span><span>锚点 {o['anchor_date']} = {o['anchor_ratio_rep']}</span>{live_note}</div></header>

<div class="card hero">
<div class="h-lbl">当前比价</div>
<div class="h-px">{r:.3f}</div>
<div class="h-sub">沪深300 {px:,.2f} 点　·　债腿 {bond:,.2f}（代理）</div>
<div class="tag">{o['band'][0]}　{o['band'][1]}</div>
<div class="gauge"><div class="g-track"></div>{marks}
<div class="g-now" style="left:{pos(r):.1f}%"><i></i><b>{r:.3f}</b></div></div>
</div>

<div class="card"><h2>股债走廊</h2>
<div class="corr"><div class="corr-bar"></div>
<div class="corr-pt" style="left:{max(0, min(100, corr['frac'] * 100)):.1f}%"><i></i><b>{px:,.0f}</b></div>
<div class="corr-end l"><b>{corr['lo']:,.0f}</b>触线 · 买股</div>
<div class="corr-end r"><b>{corr['hi']:,.0f}</b>偏贵 · 减股</div></div>
<div class="notes" style="margin-top:6px">现在在走廊的 <b>{corr['frac'] * 100:.0f}%</b> 位置。
下沿是"地板"，会跟着债腿自己往上长 → <b>可以当扳机</b>；上沿是"泡沫顶"，历史上从 1.5 到过 2.4 → <b>只能当温度计</b>。</div></div>

<div class="card"><h2>双向档位（比价 → 沪深300）</h2>
<table><thead><tr><th>位置</th><th>比价</th><th>沪深300</th><th>距现价</th></tr></thead>
<tbody>{t1}</tbody></table>
<div class="notes" style="margin-top:10px">
跌 <b>{abs(1 - p100 / px) * 100:.1f}%</b> 到触线（{p100:,.0f}），涨 <b>{(p150 / px - 1) * 100:.1f}%</b> 才到偏贵（{p150:,.0f}）。
两端都远，中间这段没有信号 —— 正是"两边都不便宜"的状态。</div></div>

<div class="card"><h2>债端温度（这条线答不了的部分）</h2>
{t3}
<div class="notes" style="margin-top:9px">这条比价线只回答"股相对债贵不贵"，<b>不回答"债本身贵不贵"</b>。
国债 ETF 的 52 周分位是粗代理：三只都在 77%~93%，说明债在一年高位区、收益率在低位 —— 债本身也不便宜。</div></div>

<div class="card"><h2>换算到手上的载具</h2>
<table><thead><tr><th>载具</th><th>现价</th><th>触线价</th><th>1.50 线</th></tr></thead>
<tbody>{t2}</tbody></table>{a500}</div>

<div class="card"><h2>历史触线点（原图读值）</h2>
<div class="hist">{hits}</div>
<div class="notes" style="margin-top:10px">
四次里最深 0.78、最浅 1.017。触线是<b>区域信号不是时点信号</b>——跌破 1.0 后往往还要磨 3~12 个月，
分批才接得住；一次打满就是把「区域」当成了「时点」。</div></div>

<div class="card"><h2>怎么用它管仓位</h2>
<div class="notes"><ul>
<li><b>≥1.50</b>：股票贵过债，股票腿降到最低，新钱进债/货币。历史上 2009/2015/2021 都到过，2026 这轮 52 周高 5064 也几乎踩到。</li>
<li><b>1.30~1.50</b>：开始减，宽基不再新增。</li>
<li><b>1.10~1.30</b>（现在 {r:.3f}）：不看这条线，按 K03「④减」执行。</li>
<li><b>1.00~1.05</b>：第一笔不超过计划额 1/3。</li>
<li><b>跌破 1.00</b>：分批推进，每批间隔 ≥10 个交易日或再跌 3%；跌破 0.95 把剩余弹药拿出来。</li>
</ul></div></div>

<div class="warn"><b>口径提醒</b>　债腿 885007 是 Wind 专有指数、没有公开接口，脚本用的是
「锚点 {o['anchor_date']} = {o['anchor_bond']} ＋ 中证全债推进 ＋ β={o['beta']}」的代理，
比价绝对值的误差约 ±1%，<b>距触线的百分比</b>受影响更小。
拿到博主或万得的新数字，改 <code>data/equity_bond_anchor.json</code> 重新锚定即可，不动代码。</div>

<footer>腿A 中证官网 H00300（精确）· 腿B 代理推进 · β={o['beta']}<br>
0.78 / 0.87 / 0.95 / 1.017 为原图读值，非精确数据 · 不构成投资建议</footer>
</div></body></html>"""


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bond", type=float, help="用新的债腿值重新锚定（配合 --date）")
    ap.add_argument("--date", help="新锚点日期 YYYY-MM-DD")
    ap.add_argument("--ratio", type=float, help="直接用公布的比价反推债腿（bond=H00300/ratio）")
    ap.add_argument("--price", type=float, help="假设沪深300点位测算")
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    anchor = load_anchor()
    if a.bond and a.date:
        tr = dict(csindex_series("H00300", a.date.replace("-", ""), a.date.replace("-", "")))
        if a.date not in tr:
            print(f"× {a.date} 无 H00300 数据，锚点未改"); return 1
        anchor.update({"anchor_date": a.date, "bond_index": a.bond})
        with open(ANCHOR_F, "w", encoding="utf-8") as f:
            json.dump(anchor, f, ensure_ascii=False, indent=2)
        print(f"√ 锚点已更新为 {a.date} 债腿 {a.bond}")
    elif a.ratio and a.date:
        tr = dict(csindex_series("H00300", a.date.replace("-", ""), a.date.replace("-", "")))
        if a.date not in tr:
            print(f"× {a.date} 无 H00300 数据"); return 1
        anchor.update({"anchor_date": a.date, "bond_index": round(tr[a.date] / a.ratio, 2)})
        with open(ANCHOR_F, "w", encoding="utf-8") as f:
            json.dump(anchor, f, ensure_ascii=False, indent=2)
        print(f"√ 锚点已更新为 {a.date} 比价 {a.ratio} → 债腿 {anchor['bond_index']}")

    o = build(anchor, a.price)
    o["bond_temp"] = bond_temp()
    n = to_csv(o)
    print(text_report(o))
    print(f"\n序列已写 data/equity_bond_ratio.csv（{n} 行）")

    if a.html:
        os.makedirs(REPORT_DIR, exist_ok=True)
        p = os.path.join(REPORT_DIR, f"股债比价看板_{o['asof']}.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(html_report(o))
        print(f"看板已写 {p}")
    if a.json:
        print(json.dumps({k: v for k, v in o.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
