#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
kdas.py — 不依赖付费数据源的 KDAS 关键点成本线计算器

数据源：东方财富公开行情接口（免费、无需密钥）
算法：1:1 复刻 KDAS 公式源码
      日线  VWAP = Σ成交额 / Σ(成交量×100)        （从锚点次日起算，对应 SUM(X,BARSLAST)）
      分时  VWAP = Σ(成交量×典型价) / Σ成交量      （从锚点分钟起算，含锚点，对应 SUM(X,BARSLAST+1)）

用法：
  # 1) 看某天的五条线（校验/复盘）
  python kdas.py 561910 --anchors 2025-09-04,2025-08-01,2025-08-14,2025-08-07,2025-08-27 --asof 2025-09-12

  # 2) 看最新一条 K 线的五条线，并出图
  python kdas.py 510300 --anchors 2025-01-02,2025-04-07,2025-07-01,2025-09-01,2025-10-09 --chart out.html

  # 3) 让工具提示候选关键日（不知道锚点填哪天时用）
  python kdas.py 512000 --suggest

  # 4) 分时（当日 1 分钟线，盘中可用）
  python kdas.py 002568 --intraday --anchors 09:31,10:03,13:15

  # 5) 多标的批量看状态
  python kdas.py 512000,561910,159647 --anchors 2025-09-02,2025-08-22,2025-08-08,2025-07-09
"""

import argparse
import json
import sys
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
EM_HIS = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_TREND = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"

# 与通达信/同花顺配色对应（为白底可读性做了微调）
COLORS = ["#FF7F00", "#E00000", "#C9A227", "#00A050", "#909090"]
COLOR_NAMES = ["橙", "红", "黄", "绿", "灰"]


def secid(code):
    code = code.strip()
    if code.startswith(("5", "6", "9", "11")):
        return "1." + code, "SH"
    return "0." + code, "SZ"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": "https://quote.eastmoney.com/"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def fetch_daily(code, beg="20150101", end="20500101", fqt=1):
    sid, mkt = secid(code)
    url = (f"{EM_HIS}?secid={sid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
           f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=101&fqt={fqt}&beg={beg}&end={end}")
    d = _get(url).get("data")
    if not d or not d.get("klines"):
        raise SystemExit(f"取不到 {code} 的日线数据（代码或市场可能不对）")
    bars = []
    for k in d["klines"]:
        p = k.split(",")
        bars.append({
            "date": p[0], "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]),
            "vol": float(p[5]), "amount": float(p[6]),
        })
    return d.get("name", code), mkt, bars


def fetch_intraday(code, ndays=1):
    sid, mkt = secid(code)
    url = (f"{EM_TREND}?secid={sid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
           f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
           f"&ndays={ndays}&iscr=0")
    d = _get(url).get("data")
    if not d or not d.get("trends"):
        raise SystemExit(f"取不到 {code} 的分时数据")
    bars = []
    for t in d["trends"]:
        p = t.split(",")
        # f51 时间, f52 开, f53 收, f54 高, f55 低, f56 成交量, f57 成交额
        bars.append({
            "date": p[0][-5:], "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]),
            "vol": float(p[5]), "amount": float(p[6]),
        })
    return d.get("name", code), mkt, bars


def vwap_daily(bars, anchor, asof=None):
    """从锚点次日起算到 asof 的成交量加权均价（对应 SUM(X, BARSLAST) 的窗口）"""
    dates = [b["date"] for b in bars]
    if anchor not in dates:
        raise SystemExit(f"锚点 {anchor} 不是交易日，请换一个")
    i0 = dates.index(anchor) + 1
    i1 = dates.index(asof) if asof else len(bars) - 1
    if i0 > i1:
        return None
    seg = bars[i0:i1 + 1]
    amt = sum(b["amount"] for b in seg)
    vol = sum(b["vol"] for b in seg)
    if vol <= 0:
        return None
    return amt / (vol * 100.0)


def vwap_intraday(bars, anchor, asof=None):
    """从锚点分钟（含）起算的典型价加权均价"""
    times = [b["date"] for b in bars]
    if anchor not in times:
        raise SystemExit(f"锚点 {anchor} 不在分时数据里")
    i0 = times.index(anchor)
    i1 = times.index(asof) if asof else len(bars) - 1
    seg = bars[i0:i1 + 1]
    pv = sum(((b["high"] + b["low"] + b["close"]) / 3.0) * b["vol"] for b in seg)
    v = sum(b["vol"] for b in seg)
    return pv / v if v > 0 else None


def classify(price, lines):
    """六状态初筛（启发式，仅用于快速定位，最终仍需人工判断）"""
    if not lines:
        return "—", "缺锚点"
    vals = [v for _, v in lines if v is not None]
    if not vals:
        return "—", "缺数据"
    above = [v for v in vals if v > price]
    below = [v for v in vals if v <= price]
    near = min(vals, key=lambda v: abs(v - price) / price)
    gap = (price - near) / near

    if len(above) == len(vals):
        return "自然寻底", "价在全部线下方，等站上最后一根"
    if len(above) == 0:
        if gap > 0.15:
            return "右侧追涨", f"离最近支撑 {gap*100:.1f}%，盈亏比差"
        if gap > 0.06:
            return "右侧趋势", f"全部线在下方，距最近支撑 {gap*100:.1f}%"
        return "右侧空间/趋势", f"贴着最近支撑 {gap*100:+.1f}%"
    if len(above) >= 2:
        return "右侧买点/震荡区间", f"上方还有 {len(above)} 条压力线"
    return "右侧买点", "刚站上最近一条，上方仅剩 1 条压力"


def suggest(bars, win=8):
    """提示候选关键日：阶段高点、阶段低点、显著放量日"""
    cands = []
    n = len(bars)
    for i in range(win, n - win):
        seg = bars[i - win:i + win + 1]
        b = bars[i]
        if b["high"] >= max(x["high"] for x in seg):
            cands.append((b["date"], "阶段高点", b["close"]))
        elif b["low"] <= min(x["low"] for x in seg):
            cands.append((b["date"], "阶段低点", b["close"]))
    # 近 120 日放量前 5（排除重复）
    vols = sorted(bars[-120:], key=lambda x: -x["vol"])[:5]
    for b in vols:
        cands.append((b["date"], "显著放量", b["close"]))
    seen, out = set(), []
    for c in sorted(cands, key=lambda x: x[0], reverse=True):
        if c[0] not in seen:
            seen.add(c[0])
            out.append(c)
    return out[:10]


def render_svg(name, code, bars, lines, asof, path, tail=90):
    bars = [b for b in bars if b["date"] <= asof]
    data = bars[-tail:]
    W, H, ML, MR, MT, MB = 1180, 640, 62, 150, 56, 46
    pw, ph = W - ML - MR, H - MT - MB
    prices = []
    for b in data:
        prices += [b["high"], b["low"]]
    prices += [v for _, v in lines if v is not None]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.08 or hi * 0.02
    lo, hi = lo - pad, hi + pad

    def X(i):
        return ML + pw * (i + 0.5) / len(data)

    def Y(p):
        return MT + ph * (hi - p) / (hi - lo)

    bw = max(1.6, pw / len(data) * 0.62)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="Microsoft YaHei,Segoe UI,sans-serif">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    # 网格
    for k in range(6):
        p = lo + (hi - lo) * k / 5
        y = Y(p)
        svg.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{ML+pw}" y2="{y:.1f}" stroke="#eef1f5"/>')
        svg.append(f'<text x="{ML-8}" y="{y+4:.1f}" font-size="11" fill="#8892a0" '
                   f'text-anchor="end">{p:.3f}</text>')
    # K 线
    for i, b in enumerate(data):
        x = X(i)
        col = "#d33a2c" if b["close"] >= b["open"] else "#1f9d55"
        svg.append(f'<line x1="{x:.1f}" y1="{Y(b["high"]):.1f}" x2="{x:.1f}" '
                   f'y2="{Y(b["low"]):.1f}" stroke="{col}" stroke-width="1"/>')
        y1, y2 = Y(max(b["open"], b["close"])), Y(min(b["open"], b["close"]))
        svg.append(f'<rect x="{x-bw/2:.1f}" y="{y1:.1f}" width="{bw:.1f}" '
                   f'height="{max(1.2, y2-y1):.1f}" fill="{"#fff" if b["close"]>=b["open"] else col}" '
                   f'stroke="{col}" stroke-width="1"/>')
    # 五条线
    labels = []
    for idx, (anchor, val) in enumerate(lines):
        if val is None:
            continue
        y = Y(val)
        svg.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{ML+pw}" y2="{y:.1f}" '
                   f'stroke="{COLORS[idx%5]}" stroke-width="2"/>')
        labels.append([y, f'{COLOR_NAMES[idx%5]} {val:.4f}', COLORS[idx%5], idx])
    # 标签防重叠：按 y 排序后推开
    labels.sort(key=lambda t: t[0])
    gapmin = 16
    for i in range(1, len(labels)):
        if labels[i][0] - labels[i - 1][0] < gapmin:
            labels[i][0] = labels[i - 1][0] + gapmin
    for y, text, col, idx in labels:
        svg.append(f'<line x1="{ML+pw}" y1="{Y(lines[idx][1]):.1f}" x2="{ML+pw+5}" '
                   f'y2="{y:.1f}" stroke="{col}" stroke-width="1" opacity="0.6"/>')
        svg.append(f'<text x="{ML+pw+8}" y="{y+4:.1f}" font-size="12" font-weight="600" '
                   f'fill="{col}">{text}</text>')
    # 标题
    svg.append(f'<text x="{ML}" y="26" font-size="16" font-weight="700" fill="#1c2430">'
               f'{name}（{code}） · KDAS 成本线</text>')
    svg.append(f'<text x="{ML}" y="44" font-size="12" fill="#6b7684">'
               f'截至 {asof} ｜ 锚点：{", ".join(a for a, _ in lines)} ｜ '
               f'数据：东方财富公开行情</text>')
    # 日期刻度
    for i in range(0, len(data), max(1, len(data) // 6)):
        svg.append(f'<text x="{X(i):.1f}" y="{MT+ph+18}" font-size="10.5" fill="#8892a0" '
                   f'text-anchor="middle">{data[i]["date"][2:]}</text>')
    svg.append(f'<text x="{ML}" y="{H-8}" font-size="10.5" fill="#a0a8b4">'
               f'线值 = 从锚点次日起的成交量加权均价（VWAP），与同花顺 KDAS 公式一致；仅供研究，不构成投资建议。</text>')
    svg.append("</svg>")
    body = "\n".join(svg)
    if path.lower().endswith((".html", ".htm")):
        body = ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                f"<title>{name} KDAS</title><style>body{{margin:0;background:#fff;"
                "display:flex;justify-content:center;padding:12px}}</style></head>"
                f"<body>{body}</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def main():
    ap = argparse.ArgumentParser(description="KDAS 成本线计算器（免费公开数据源）")
    ap.add_argument("codes", nargs="+", help="股票/ETF 代码，可多个（逗号或空格分隔）")
    ap.add_argument("--anchors", help="锚点日期，逗号分隔，最多 5 个；分时模式用 HH:MM")
    ap.add_argument("--asof", help="计算到哪一天/哪一分钟，默认最新")
    ap.add_argument("--intraday", action="store_true", help="用当日 1 分钟数据（分时版）")
    ap.add_argument("--chart", help="输出 SVG 图路径（仅单标的、日线）")
    ap.add_argument("--tail", type=int, default=90, help="图上显示最近多少根 K 线")
    ap.add_argument("--suggest", action="store_true", help="提示候选关键日")
    ap.add_argument("--beg", default="20150101", help="日线起始日期")
    args = ap.parse_args()

    codes = []
    for c in args.codes:
        codes += [x.strip() for x in c.split(",") if x.strip()]

    anchors = [a.strip() for a in args.anchors.split(",")] if args.anchors else []
    if len(anchors) > 5:
        raise SystemExit("最多 5 个锚点（对应源码 5 组参数）")

    for code in codes:
        if args.intraday:
            name, mkt, bars = fetch_intraday(code)
            fn = vwap_intraday
            asof = args.asof or (bars[-1]["date"] if bars else None)
        else:
            beg = args.beg
            if anchors:
                try:
                    d0 = datetime.strptime(min(anchors), "%Y-%m-%d")
                    beg = min(beg, (d0.replace(day=1)).strftime("%Y%m%d"))
                except ValueError:
                    pass
            name, mkt, bars = fetch_daily(code, beg=beg)
            fn = vwap_daily
            asof = args.asof or bars[-1]["date"]

        if not bars:
            print(f"{code}: 无数据"); continue

        price = [b for b in bars if b["date"] == asof]
        if not price:
            print(f"{code}: 找不到 {asof} 的数据"); continue
        price = price[-1]["close"]

        if args.suggest:
            print(f"\n=== {name}（{code}）候选关键日 ===")
            for d, why, c in suggest(bars):
                print(f"  {d}  {why:<10} 收 {c:.3f}")
            if not anchors:
                continue

        if not anchors:
            print(f"{code}: 请用 --anchors 指定锚点，或先加 --suggest 看候选")
            continue

        lines = []
        for a in anchors:
            try:
                lines.append((a, fn(bars, a, asof)))
            except SystemExit as e:
                print(f"  ! {e}")
                lines.append((a, None))

        vals = [v for _, v in lines if v is not None]
        st, why = classify(price, lines)
        print(f"\n=== {name}（{code}·{mkt}） {asof} 收盘 {price:.4f} ===")
        print(f"{'线':<4}{'锚点':<14}{'线值':<10}{'距现价':<10}{'角色'}")
        for i, (a, v) in enumerate(lines):
            if v is None:
                print(f"{COLOR_NAMES[i]:<4}{a:<14}{'—':<10}")
                continue
            gap = (price - v) / v * 100
            role = "压力" if v > price else "支撑"
            print(f"{COLOR_NAMES[i]:<4}{a:<14}{v:<10.4f}{gap:+7.2f}%   {role}")
        print(f"机器初筛状态：{st} —— {why}")

        if args.chart and len(codes) == 1:
            render_svg(name, code, bars, lines, asof, args.chart, args.tail)
            print(f"图已输出：{args.chart}")


if __name__ == "__main__":
    main()
