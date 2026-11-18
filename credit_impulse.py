#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""K05 信贷脉冲 —— 社融存量同比动量口径 + 与沪深300同比的领先期实测

为什么要这个脚本：
    图（ocmacro「信贷脉冲 vs 沪深300同比收益率」）给的是方向，不是可复现的数字。
    看图能得出「信用收缩 → 3 个月后股指同比转负」，但要进体系就必须能自己算、自己验。

口径（本脚本，明确写死，别和别家混）：
    脉冲(t) = 社融存量同比(t) − 社融存量同比(t−12)
    ├─ 含政府债：社融存量同比
    └─ 剔除政府债：(社融存量 − 政府债券存量) 的同比，再取 12 个月差
    单位 pp（百分点），不是增速。

    这不是 BIS/高盛那个「新增信贷/GDP 的 12 个月变化」，两者量级差 1.5~2 倍
    （2026-10：本口径 −1.3pp，卖方增量/GDP 口径 −1.7% ~ −3.2%）。
    方向、拐点一致，绝对值不可互换。要绝对可比就把原图口径写进 CSV 再重锚。

数据：
    data/social_financing.csv  由连接器 data_macro(names=cn_financing) 每月拉一次后追加
    沪深300 日线走 brief.daily（东财 → 腾讯兜底）

用法：
    python credit_impulse.py                    # 当前读数 + 领先期实测
    python credit_impulse.py --lag 12           # 把互相关窗口拉到 12 个月
    python credit_impulse.py --add 2026-11 4700000 1070000 7.2   # 追加当月社融
"""
import os
import sys
import math
import argparse
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(BASE, "data", "social_financing.csv")


# ---------------------------------------------------------------- 数据
def load():
    rows = []
    with open(CSV, encoding="utf-8") as f:
        head = f.readline()
        for ln in f:
            p = [x.strip() for x in ln.split(",")]
            if len(p) < 4 or not p[0]:
                continue
            rows.append({"m": p[0], "size": float(p[1]), "gov": float(p[2]),
                         "yoy": float(p[3])})
    rows.sort(key=lambda r: r["m"])
    return rows


def add_row(m, size, gov, yoy):
    rows = load()
    for r in rows:
        if r["m"] == m:
            r.update(size=size, gov=gov, yoy=yoy)
            break
    else:
        rows.append({"m": m, "size": size, "gov": gov, "yoy": yoy})
    rows.sort(key=lambda r: r["m"])
    with open(CSV, "w", encoding="utf-8", newline="") as f:
        f.write("date,size,gov_bond,yoy\n")
        for r in rows:
            f.write(f"{r['m']},{r['size']:.0f},{r['gov']:.0f},{r['yoy']:.1f}\n")
    print(f"已写入 {m}：社融存量 {size/1e4:.2f} 万亿，政府债 {gov/1e4:.2f} 万亿，同比 {yoy}%")


def shift_month(m, k):
    y, mo = int(m[:4]), int(m[5:7])
    mo += k
    y += (mo - 1) // 12
    mo = (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"


def pulses(rows):
    """给每个月补上：剔除政府债同比、两种 12 个月差分（脉冲）"""
    idx = {r["m"]: r for r in rows}
    ex_yoy = {}
    for r in rows:
        p = idx.get(shift_month(r["m"], -12))
        if p:
            ex_yoy[r["m"]] = ((r["size"] - r["gov"]) / (p["size"] - p["gov"]) - 1) * 100
    out = []
    for r in rows:
        prev = idx.get(shift_month(r["m"], -12))
        if not prev:
            continue
        m = r["m"]
        p_ex = None
        if m in ex_yoy and shift_month(m, -12) in ex_yoy:
            p_ex = ex_yoy[m] - ex_yoy[shift_month(m, -12)]
        out.append({
            "m": m, "size": r["size"], "gov": r["gov"], "yoy": r["yoy"],
            "ex_yoy": ex_yoy.get(m),
            "p_all": r["yoy"] - prev["yoy"],
            "p_ex": p_ex,
        })
    return out


# ---------------------------------------------------------------- 沪深300 同比
HS300 = os.path.join(BASE, "data", "hs300_month.csv")


def hs300_monthly(refresh=False):
    """月末收盘价。优先读缓存（由连接器 data_kline 月线落盘），--refresh 时走网络重写。"""
    if not refresh and os.path.exists(HS300):
        out = {}
        with open(HS300, encoding="utf-8") as f:
            f.readline()
            for ln in f:
                p = ln.strip().split(",")
                if len(p) >= 2 and p[0]:
                    out[p[0]] = float(p[1])
        if out:
            return out
    try:
        from brief import daily
        bars, _ = daily("000300", beg="20200101")
        last = {}
        for b in bars:
            last[b["date"][:7]] = b["close"]
        if last:
            with open(HS300, "w", encoding="utf-8", newline="") as f:
                f.write("month,close\n")
                for m in sorted(last):
                    f.write(f"{m},{last[m]:.2f}\n")
            return last
    except Exception as e:
        print(f"⚠ 沪深300 月线取数失败（{e}），用缓存")
    raise SystemExit("沪深300 月线缓存不存在且网络不可用：先用连接器取 data_kline(sh000300, month) 落盘")


def hs300_yoy(close_by_m):
    out = {}
    for m, c in close_by_m.items():
        p = close_by_m.get(shift_month(m, -12))
        if p:
            out[m] = (c / p - 1) * 100
    return out


# ---------------------------------------------------------------- 统计
def corr(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def lead_scan(pl, yy, max_lag=9, key="p_all"):
    """脉冲(t) 对 同比(t+k) 做相关，k>0 表示脉冲领先"""
    rows = []
    months = sorted(set(pl) & set(yy))
    for k in range(0, max_lag + 1):
        xs, ys = [], []
        for m in months:
            m2 = shift_month(m, k)
            if m2 in yy:
                xs.append(pl[m][key])
                ys.append(yy[m2])
        r = corr(xs, ys)
        if r is not None:
            rows.append((k, r, len(xs)))
    return rows


def band(p):
    if p is None:
        return "无数据", "—"
    if p >= 2:
        return "强扩张", "信用在放，右侧可加；但要看钱进了哪（K01 份额）"
    if p >= 0:
        return "温和扩张", "持有为主，回调可接"
    if p >= -1.5:
        return "轻度收缩", "只减不加，做T以减仓方向为主"
    if p >= -3:
        return "收缩", "④减 区，等拐点；左侧只小额、且必须 K04 同时触线"
    return "深度收缩", "不接刀，等脉冲本身拐头（连续 2 个月回升）"


# ---------------------------------------------------------------- 报告模块
def turn_state(ps):
    """拐点判定：单月回升≠拐点。含政府债连升 3 个月且剔除版同步，才算确认。"""
    a, b = ps[-2]["p_all"], ps[-1]["p_all"]
    ea, eb = ps[-2].get("p_ex"), ps[-1].get("p_ex")
    if len(ps) >= 4:
        c, ec = ps[-3]["p_all"], ps[-3].get("p_ex")
        up3 = c < a < b
        ex3 = None if None in (ec, ea, eb) else (ec < ea < eb)
        if up3 and ex3:
            return "确认拐头", f"连升 3 月（{c:+.2f}→{a:+.2f}→{b:+.2f}），含/剔除同步"
    if b > a:
        if ea is not None and eb is not None and eb <= ea:
            return "未确认", f"首月回升 {a:+.2f}→{b:+.2f}，剔除版未同步 {ea:+.2f}→{eb:+.2f}"
        return "未确认", f"首月回升 {a:+.2f}→{b:+.2f}，需连升 3 月"
    return "仍下行", f"较上月 {a:+.2f}→{b:+.2f}"


def next_release(m):
    y, mo = int(m[:4]), int(m[5:7]) + 1
    if mo > 12:
        y, mo = y + 1, 1
    return f"{y}-{mo:02d} 中旬"


def stale_warn(m):
    """社融每月 10~15 日发布上月数据。过了 16 号还停在上上个月，说明该更新了。"""
    now = datetime.now()
    ym = now.strftime("%Y-%m")
    expect = shift_month(ym, -1 if now.day >= 16 else -2)
    return f"⚠ 待更新（{expect} 应已发布，最新仅到 {m}）" if m < expect else ""


def snapshot(mode="morning"):
    """一次算全，给 brief.py 的报告块用；不打印、不抛异常外的副作用。"""
    rows = load()
    ps = pulses(rows)
    if len(ps) < 6:
        raise RuntimeError("社融序列不足")
    cur = ps[-1]
    close = hs300_monthly()

    px, mon, tag = None, None, ""
    try:                                   # 实时点位：让同比每天有变化，而不是月更
        from brief import index_quote
        q = index_quote("1.000300", "沪深300")
        if q.get("ok") and q.get("price"):
            px = q["price"]
            mon = datetime.now().strftime("%Y-%m")
            tag = "昨收" if mode == "morning" else "实时"
    except Exception:
        pass
    if px is None:                          # 退回月末缓存
        mon = max(close)
        px, tag = close[mon], f"{mon} 月末"

    base = close.get(shift_month(mon, -12)) if mon else None
    yoy = (px / base - 1) * 100 if base else None

    pl = {r["m"]: r for r in ps}
    yy = hs300_yoy(close)
    scan = lead_scan(pl, yy, max_lag=12)
    best = max(scan, key=lambda t: abs(t[1])) if scan else (None, None, None)

    lv, act = band(cur["p_all"])
    st, why = turn_state(ps)
    return {"m": cur["m"], "p_all": cur["p_all"], "p_ex": cur["p_ex"], "yoy": cur["yoy"],
            "band": lv, "act": act, "turn": st, "turn_why": why,
            "px": px, "px_tag": tag, "hs_yoy": yoy,
            "k": best[0], "r": best[1], "n": best[2],
            "next": next_release(cur["m"]), "warn": stale_warn(cur["m"])}


def block(no="八", mode="morning"):
    """K05 信贷脉冲：月频指标，进日报只做「背景板 + 否决位」，不参与日内择时。
    数据来自 credit_impulse.py / knowledge K05，不在这里重算。"""
    print()
    print(f"{no}、信贷脉冲（K05 社融存量同比差分 · 月频）")
    try:
        s = snapshot(mode)
    except Exception as e:
        print(f"  取数失败：{e}（本指标不可用，按无信号处理）")
        return

    ex = f"{s['p_ex']:+.2f} pp（剔除政府债）" if s["p_ex"] is not None else "—"
    hy = f"{s['hs_yoy']:+.1f}%（沪深300 {s['px']:,.0f} · {s['px_tag']}）" if s["hs_yoy"] is not None else "—"
    lead = (f"{s['k']} 个月（r={s['r']:+.2f}，n={s['n']}）→ 拐头后 3~5 个月传到同比"
            if s["k"] is not None else "样本不足，未估")

    print(f"  脉冲        {s['p_all']:+.2f} pp  档位 {s['band']}")
    print(f"  私人部门    {ex}")
    print(f"  同比        {hy}")
    print(f"  领先        {lead}")
    print(f"  拐点        {s['turn']}（{s['turn_why']}）")
    print(f"  含义        {s['act']}")
    print(f"  数据        {s['m']} 社融 · 下次更新 {s['next']}{s['warn']}")
    return s      # 供 brief.py 登记 signals.md 台账（含 p_all / band / px）


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lag", type=int, default=9, help="互相关最大滞后月数")
    ap.add_argument("--add", nargs=4, metavar=("YYYY-MM", "SIZE", "GOV", "YOY"))
    a = ap.parse_args()
    if a.add:
        add_row(a.add[0], float(a.add[1]), float(a.add[2]), float(a.add[3]))
        return

    rows = load()
    ps = pulses(rows)
    if not ps:
        print("数据不足：至少需要 25 个月（留出 12 个月做差分）")
        return
    cur = ps[-1]

    close_by_m = hs300_monthly()
    yy = hs300_yoy(close_by_m)

    print("=" * 62)
    print("K05 信贷脉冲（社融存量同比动量口径）  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 62)
    print(f"数据截止      {cur['m']}")
    print(f"社融存量      {cur['size']/1e4:.2f} 万亿   同比 {cur['yoy']:+.1f}%")
    print(f"政府债券      {cur['gov']/1e4:.2f} 万亿   占存量 {cur['gov']/cur['size']*100:.1f}%")
    print(f"脉冲·含政府债   {cur['p_all']:+.2f} pp   ← 图上 −2% 对应的量级")
    if cur["p_ex"] is not None:
        print(f"脉冲·剔除政府债 {cur['p_ex']:+.2f} pp   ← 私人部门信用，才是真需求")
    lv, act = band(cur["p_all"])
    print(f"档位          {lv}　→ {act}")

    print("-" * 62)
    print("近 13 个月脉冲轨迹（含 / 剔除）")
    for r in ps[-13:]:
        ex = f"{r['p_ex']:+.2f}" if r["p_ex"] is not None else "  n/a"
        print(f"  {r['m']}   {r['p_all']:+.2f}   {ex}   存量同比 {r['yoy']:+.1f}%")

    print("-" * 62)
    cur_m = max(m for m in yy if m <= cur["m"])
    print(f"沪深300 同比（{cur_m} 收盘 {close_by_m[cur_m]:.2f}）  {yy[cur_m]:+.1f}%")

    pl = {r["m"]: r for r in ps}
    print("-" * 62)
    print(f"领先期实测：corr(脉冲含政府债(t), 沪深300同比(t+k))，样本 {len(pl)} 个月")
    scan = lead_scan(pl, yy, max_lag=a.lag)
    for k, r, n in scan:
        bar = "#" * int(abs(r) * 40)
        print(f"  k={k:>2}  r={r:+.3f}  n={n:<3} {bar}")
    if scan:
        best = max(scan, key=lambda t: abs(t[1]))
        print(f"  → 最优滞后 {best[0]} 个月（r={best[1]:+.3f}，n={best[2]}）")
        if best[0] == 0:
            print("  ⚠ 峰值在 k=0：这批数据里脉冲和同比是同步的，『领先 3 个月』没有复现。")
    print("-" * 62)
    print("口径提醒：本脚本是『存量同比动量』，卖方常用『增量/GDP 的 12 个月变化』，")
    print("         两者量级差 1.5~2 倍（-1.3pp vs -1.7%~-3.2%），方向一致、绝对值不可互换。")
    print("样本提醒：只有 %d 个脉冲点，相关系数的置信区间很宽，只能当方向参考。" % len(pl))


if __name__ == "__main__":
    main()
