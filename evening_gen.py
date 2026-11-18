#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
evening_gen.py — 把 brief.py evening 的文本输出 + 决策 JSON 渲染成手机端「晚报」HTML

与早/午盘的分工（2026-11-18 定）：
  晚报 = 当日复盘。只回答「今天发生了什么」，**不出做T动作、不给触发价**。
  段序固定：A股/港股/全球收盘 → 宽基ETF → 国家队与大单 → 两融 → 解读 → K05/K04 → 要闻 → 明日关注。
  版式由本文件固定，不要每次手写 HTML。

用法：
  python brief.py evening > reports/_evening_D.txt
  python evening_gen.py --data reports/_evening_D.txt \
                        --decision reports/_decision_evening_D.json

决策 JSON（除了没有 actions/skip，字段含义与早午盘一致）：
{
  "date": "2026-11-18",
  "subtitle": "21:00 · 数据：腾讯自选股连接器",
  "verdict": "一句话定调，允许 <b>",
  "reads": [{"tag","tone":"blue|green|","title","body":["段1","段2"],"src"}],
  "watch": [{"t":"明日关注（短标题）","d":"正文"}],
  "risks": [{"t":"短标题","d":"正文"}],
  "digest": [{"m":"HH:MM","t":"要闻要点，可用 <b>"}]
}

复用 report_gen 的：CSS / 解析器（大盘资金、两融、K05、K04、快讯）/ 渲染器（同名）。
早午盘链路不受影响：本文件只读 report_gen，不改它的渲染流程。
"""

import argparse
import json
import os
import re
import sys

from report_gen import (
    CSS, E, OUT_DIR, cls, num, plain, read_text, render_impulse, render_indices,
    render_margin, render_market, render_news, render_ratio, render_reads,
    render_risks, rich, sec_title, sign, yuan, parse_common, parse_indices,
    parse_market, parse_margin, parse_impulse, parse_ratio, parse_news,
)

BASE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ 晚报补充样式
# 只加早午盘没用到的两类：族别表 / 全球分组卡。颜色沿用 report_gen 的 CSS 变量，
# 保证三份报告视觉一致（用户手机端会连着看）。
CSS_EXT = r"""
.tb{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.tb .hd,.tb .rw{display:grid;gap:5px;align-items:center;padding:8px 11px}
.tb .hd{background:#fafbfc;border-bottom:1px solid var(--soft);font-size:10.5px;
  font-weight:800;color:var(--ink3);letter-spacing:.03em}
.tb .rw{border-top:1px dashed var(--soft);color:var(--ink2)}
.tb .rw.sum{background:#fafbfc}
.tb .rw.sum .nm,.tb .rw.sum .v{font-weight:800}
.tb .nm{font-weight:800;color:var(--ink);font-size:13px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.tb .v{font-family:var(--mono);text-align:right;font-variant-numeric:tabular-nums;
  font-weight:700;font-size:11.5px;white-space:nowrap}
.c-fam{grid-template-columns:1fr 48px 52px 58px 62px}
.c-hold{grid-template-columns:1fr 42px 48px 52px 74px}
.gp{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:8px}
.gp .gh{display:flex;align-items:baseline;gap:8px;padding:9px 12px 5px;font-size:11.5px;
  font-weight:800;color:var(--ink3);letter-spacing:.08em}
.gp .gh span{margin-left:auto;font-weight:600;letter-spacing:0;font-size:11.5px}
.gp .gr{display:grid;grid-template-columns:1fr 90px 60px;gap:6px;padding:7px 12px;
  border-top:1px dashed var(--soft);align-items:center;font-size:13px;color:var(--ink2)}
.gp .gr .n{font-weight:700;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gp .gr .p{font-family:var(--mono);text-align:right;font-variant-numeric:tabular-nums}
.gp .gr .c{font-family:var(--mono);text-align:right;font-weight:800;
  font-variant-numeric:tabular-nums;white-space:nowrap}
.st{font-size:10.5px;font-weight:800;padding:1px 5px;border-radius:4px;
  background:#f2f4f7;color:var(--ink3);white-space:nowrap;display:inline-block}
.st.s1{background:#fdecea;color:#cf3126}.st.s2{background:#eaf0fe;color:#1d4ed8}
.st.s3{background:#e7f5f0;color:#0d8f66}.st.s4{background:#fdf3e4;color:#b45309}
.st.s5{background:#fbe8d4;color:#9a5006}.st.s6{background:#f1f5ff;color:#3c62c9}
.wt{background:#fff;border:1px solid var(--line);border-radius:13px;overflow:hidden;margin-bottom:8px}
.wt .wi{display:grid;grid-template-columns:22px 1fr;gap:10px;padding:12px 14px}
.wt .wn{font-family:var(--mono);font-size:11.5px;font-weight:800;color:#1d4ed8;
  background:#eaf0fe;width:21px;height:21px;border-radius:6px;display:grid;place-items:center;margin-top:1px}
.wt .wt2{font-size:15px;font-weight:800;color:var(--ink);line-height:1.5}
.wt .wd{font-size:13.5px;color:var(--ink2);line-height:1.75;margin-top:3px}
.wt .wd b{color:var(--ink)}
@media(max-width:380px){.c-fam{grid-template-columns:1fr 44px 48px 54px 58px}
  .c-hold{grid-template-columns:1fr 38px 44px 48px 68px}}
"""

STATE_CLASS = {
    "自然寻底": "s1", "右侧买点": "s2", "震荡区间": "s3",
    "右侧空间": "s4", "右侧空间·窄": "s5", "右侧趋势": "s6",
}

GROUPS = ("亚太收盘", "欧洲收盘", "美股昨夜", "期货当前", "汇率商品")


# ------------------------------------------------------------------ 解析
def _rows(block):
    """两空格 + 标签 + 两空格以上 + 正文 → dict（同 report_gen 的口径）"""
    out = {}
    for ln in block.splitlines():
        m = re.match(r"^\s{2}(\S+)\s{2,}(.+?)\s*$", ln)
        if m:
            out.setdefault(m.group(1), m.group(2))
    return out


def parse_ashare(block):
    rows = _rows(block)
    amt = re.search(r"([\d,]+)\s*亿（沪 ([\d,]+) \+ 深 ([\d,]+)", rows.get("两市成交额", ""))
    return {
        "indices": parse_indices(block),
        "amount": float(amt.group(1).replace(",", "")) if amt else None,
        "sh": float(amt.group(2).replace(",", "")) if amt else None,
        "sz": float(amt.group(3).replace(",", "")) if amt else None,
        "note": rows.get("成交额口径", "").strip(),
    }


def parse_hk(block):
    rows = _rows(block)
    sb = re.search(r"([+-]?[\d,.]+[亿万])（沪港通 ([+-]?[\d,.]+[亿万]) / "
                   r"深港通 ([+-]?[\d,.]+[亿万])，(.*?)）", rows.get("南向资金", ""))
    south = None
    if sb:
        south = {"total": yuan(sb.group(1)), "sh": yuan(sb.group(2)),
                 "sz": yuan(sb.group(3)), "date": sb.group(4).strip()}
    return {"indices": parse_indices(block), "south": south,
            "south_raw": rows.get("南向资金", "").strip(),
            "note": rows.get("港股口径", "").strip()}


def parse_global(block):
    """按「亚太收盘/欧洲收盘/美股昨夜/期货当前/汇率商品」分组；组内行 4 空格缩进。"""
    out, cur = [], None
    for ln in block.splitlines():
        t = ln.strip()
        if not t:
            continue
        if t in GROUPS:
            cur = {"name": t, "rows": []}
            out.append(cur)
            continue
        m = re.match(r"^(\S+)\s+([\d,]+\.\d+)\s+([+-][\d.]+%)\s*$", t)
        if m and cur is not None:
            cur["rows"].append({"name": m.group(1), "price": m.group(2),
                                "pct": num(m.group(3))})
    note = ""
    for ln in block.splitlines():
        m = re.match(r"^\s{2}时点说明\s{2,}(.+?)\s*$", ln)
        if m:
            note = m.group(1)
    return {"groups": [g for g in out if g["rows"]], "note": note}


def parse_broad(block):
    """两张表：族别聚合（含「合计」行）+ 持仓当日。数值列保留脚本给的字符串，
    只在需要上色时转数值，避免二次换算引入偏差。"""
    fams, holds, mode = [], [], None
    for ln in block.splitlines():
        if "族别" in ln and "涨跌" in ln:
            mode = "fam"
            continue
        if "持仓当日" in ln:
            mode = "hold"
            continue
        if mode == "fam":
            m = re.match(r"^\s{2}(\S+)\s+([+-][\d.]+%|—)\s+([\d,.]+)亿\s+"
                         r"([+-]?[\d.]+[亿万]|—)\s+([+-]?[\d.]+亿份|—)\s*$", ln)
            if m:
                fams.append({"name": m.group(1), "pct": m.group(2),
                             "amt": m.group(3), "main": m.group(4),
                             "shares": m.group(5), "sum": m.group(1) == "合计"})
        elif mode == "hold":
            m = re.match(r"^\s{2}(\S+)\s+([\d.]+)\s+([+-][\d.]+%)\s+([\d.]+)亿\s+"
                         r"([+-]?[\d.]+[亿万]|—)\s+(\S+)\s*$", ln)
            if m:
                holds.append({"name": m.group(1), "price": m.group(2),
                              "pct": m.group(3), "amt": m.group(4),
                              "main": m.group(5), "state": m.group(6)})
    rows = _rows(block)
    return {"fams": fams, "holds": holds,
            "shares_note": rows.get("份额口径", "").strip(),
            "main_note": rows.get("主力口径", "").strip(),
            "state_note": rows.get("状态口径", "").strip()}


def parse_evening(text):
    d = parse_common(text)
    d["ashare"] = parse_ashare(sec_title(text, "A股收盘", "港股收盘"))
    d["hk"] = parse_hk(sec_title(text, "港股收盘", "全球市场"))
    d["global"] = parse_global(sec_title(text, "全球市场", "宽基ETF全景"))
    d["broad"] = parse_broad(sec_title(text, "宽基ETF全景", "大盘资金与国家队"))
    d["market"] = parse_market(text)
    d["margin"] = parse_margin(text)
    d["impulse"] = parse_impulse(text)
    d["ratio"] = parse_ratio(text)
    d["news"] = parse_news(text)
    return d


# ------------------------------------------------------------------ 渲染
def kv_row(css_cls, cells):
    """cells: [(文本, class)] → 一行 .rw"""
    tds = []
    for i, (txt, c) in enumerate(cells):
        klass = "nm" if i == 0 else "v"
        tds.append(f'<span class="{klass} {c}">{E(txt)}</span>')
    return f'<div class="rw {css_cls}">{"".join(tds)}</div>'


def render_ashare(a):
    if not a or not a["indices"]:
        return '<div class="empty">A股收盘数据未取到</div>'
    pills = []
    if a.get("amount"):
        pills.append(f'<span class="pill">两市成交 <b>{a["amount"]:,.0f}</b>亿</span>')
        if a.get("sh") and a.get("sz"):
            pills.append(f'<span class="pill">沪 <b>{a["sh"]:,.0f}</b>'
                         f' 深 <b>{a["sz"]:,.0f}</b></span>')
    note = f'<div class="mb-sub">{E(a["note"])}</div>' if a.get("note") else ""
    pills_html = (f'<div class="mb-pills" style="padding-top:10px">{"".join(pills)}</div>'
                  if pills else "")
    return ('<div class="mbox"><div style="padding:12px 13px 2px">'
            + render_indices(a["indices"]) + "</div>" + pills_html + note + "</div>")


def render_hk(h):
    if not h or not h["indices"]:
        return '<div class="empty">港股数据未取到</div>'
    if h.get("south"):
        s = h["south"]
        c = cls(s["total"] or 0)
        tail = (f'<div class="mb-sub"><b>南向资金</b> '
                f'<span class="{c}">{s["total"]/1e8:+,.1f}亿</span>'
                f'（沪港通 {s["sh"]/1e8:+,.1f}亿 / 深港通 {s["sz"]/1e8:+,.1f}亿，'
                f'{E(s["date"])}）· 正=内地资金净流入港股</div>')
    else:
        tail = (f'<div class="mb-sub"><b>南向资金</b> {E(h.get("south_raw") or "无数据")}</div>')
    note = f'<div class="mb-sub">{E(h["note"])}</div>' if h.get("note") else ""
    return (f'{plain("hk")}<div class="mbox"><div style="padding:12px 13px 2px">'
            f'{render_indices(h["indices"])}</div>{tail}{note}</div>')


def render_global(g):
    if not g or not g["groups"]:
        return '<div class="empty">全球市场数据未取到</div>'
    parts = []
    for grp in g["groups"]:
        rows = []
        for r in grp["rows"]:
            c = cls(r["pct"])
            rows.append(f'<div class="gr"><span class="n">{E(r["name"])}</span>'
                        f'<span class="p">{E(r["price"])}</span>'
                        f'<span class="c {c}">{sign(r["pct"])}</span></div>')
        parts.append(f'<div class="gp"><div class="gh">{E(grp["name"])}'
                     f'<span>{len(grp["rows"])} 个</span></div>{"".join(rows)}</div>')
    note = f'<div class="mb-sub">{E(g["note"])}</div>' if g.get("note") else ""
    return plain("global") + "".join(parts) + note


def render_broad(b):
    if not b or (not b["fams"] and not b["holds"]):
        return '<div class="empty">宽基 ETF 数据未取到</div>'
    head = ('<div class="hd c-fam"><span>指数族</span><span class="v">涨跌</span>'
            '<span class="v">成交·亿</span><span class="v">主力·亿</span>'
            '<span class="v">份额·亿份</span></div>')
    body = []
    for f in b["fams"]:
        c = cls(num(f["pct"])) if f["pct"] != "—" else "flat"
        mv = yuan(f["main"]) if f["main"] != "—" else None
        sv = yuan(f["shares"].replace("亿份", "亿")) if f["shares"] != "—" else None
        row = (f'<div class="rw c-fam {"sum" if f["sum"] else ""}">'
               f'<span class="nm">{E(f["name"])}</span>'
               f'<span class="v {c}">{E(f["pct"])}</span>'
               f'<span class="v">{num(f["amt"]):,.1f}</span>'
               f'<span class="v {cls(mv) if mv is not None else "flat"}">'
               f'{"—" if mv is None else format(mv/1e8, "+.2f")}</span>'
               f'<span class="v {cls(sv) if sv is not None else "flat"}">'
               f'{"—" if sv is None else format(sv/1e8, "+.2f")}</span></div>')
        body.append(row)
    fam_tb = f'<div class="tb">{head}{"".join(body)}</div>'

    hold_tb = ""
    if b["holds"]:
        hh = ('<div class="hd c-hold"><span>持仓</span><span class="v">现价</span>'
              '<span class="v">涨跌</span><span class="v">主力·亿</span>'
              '<span class="v">状态</span></div>')
        hr = []
        for x in b["holds"]:
            c = cls(num(x["pct"]))
            mv = yuan(x["main"]) if x["main"] != "—" else None
            main_c = cls(mv) if mv is not None else "flat"
            st_cls = STATE_CLASS.get(x["state"], "")
            hr.append(f'<div class="rw c-hold"><span class="nm">{E(x["name"])}</span>'
                      f'<span class="v">{E(x["price"])}</span>'
                      f'<span class="v {c}">{E(x["pct"])}</span>'
                      f'<span class="v {main_c}">'
                      f'{"—" if mv is None else format(mv/1e8, "+.2f")}</span>'
                      f'<span class="v"><i class="st {st_cls}">{E(x["state"])}</i></span></div>')
        hold_tb = ('<div class="sec-head" style="margin:14px 0 8px">'
                   '<h2 style="font-size:15px">持仓当日</h2>'
                   '<span class="hint">状态见策略 §4.0</span></div>'
                   f'<div class="tb">{hh}{"".join(hr)}</div>')

    notes = "".join(f'<div class="mb-sub">{E(t)}</div>'
                    for t in (b.get("shares_note"), b.get("main_note"),
                              b.get("state_note")) if t)
    return plain("broad") + fam_tb + hold_tb + notes


def render_watch(items):
    """明日关注：只列条件，不给动作。用蓝色编号卡，与「风险」的橙色卡区分开。"""
    if not items:
        return '<div class="empty">没有需要特别盯的事，按既定纪律执行。</div>'
    s = []
    for i, it in enumerate(items, 1):
        if isinstance(it, dict):
            t, d = it.get("t", ""), it.get("d", "")
        else:
            t, d = str(it), ""
        s.append(f'<div class="wt"><div class="wi"><div class="wn">{i}</div>'
                 f'<div><div class="wt2">{rich(t)}</div>'
                 f'<div class="wd">{rich(d)}</div></div></div></div>')
    return "".join(s)


def render(mode, d, decision):
    date = decision.get("date") or d["date"]
    title = "晚间复盘"
    subtitle = decision.get("subtitle") or f'{d["time"]} · 21:00 收盘口径'
    verdict = decision.get("verdict", "—")

    n = 0

    def nxt():
        nonlocal n
        n += 1
        return n

    a_no = nxt()
    sec_a = f'''<section id="ashare"><div class="sec-head"><span class="n">{a_no}</span>
  <h2>A股收盘</h2><span class="hint">指数口径 · 收盘即准</span></div>{render_ashare(d["ashare"])}</section>'''

    h_no = nxt()
    sec_h = f'''<section id="hk"><div class="sec-head"><span class="n">{h_no}</span>
  <h2>港股收盘</h2><span class="hint">16:00 收盘 · 南向资金</span></div>{render_hk(d["hk"])}</section>'''

    g_no = nxt()
    sec_g = f'''<section id="global"><div class="sec-head"><span class="n">{g_no}</span>
  <h2>全球市场</h2><span class="hint">亚太/欧洲收盘 · 美股昨夜 · 期货当前</span></div>{render_global(d["global"])}</section>'''

    b_no = nxt()
    sec_b = f'''<section id="broad"><div class="sec-head"><span class="n">{b_no}</span>
  <h2>宽基 ETF</h2><span class="hint">国家队载具 20 只 · 成交额加权</span></div>{render_broad(d["broad"])}</section>'''

    mk_no = nxt()
    sec_mk = ""
    if d.get("market"):
        sec_mk = (f'<section id="fund"><div class="sec-head"><span class="n">{mk_no}</span>'
                  '<h2>国家队与大单</h2><span class="hint">两市主力 · 载具明细 · 份额 K01</span>'
                  f'</div>{render_market(d["market"], full=True)}</section>')
    else:
        n -= 1

    mg_no = nxt()
    sec_mg = ""
    if d.get("margin"):
        sec_mg = (f'<section id="margin"><div class="sec-head"><span class="n">{mg_no}</span>'
                  '<h2>融资融券</h2><span class="hint">杠杆资金 · T+1 披露</span>'
                  f'</div>{render_margin(d["margin"])}</section>')
    else:
        n -= 1

    reads = decision.get("reads", [])
    sec_rd = ""
    if reads:
        rd_no = nxt()
        sec_rd = (f'<section id="reads"><div class="sec-head"><span class="n">{rd_no}</span>'
                  '<h2>资金面解读</h2><span class="hint">谁在买、谁在卖、明天怎么看</span>'
                  f'</div>{render_reads(reads)}</section>')
    else:
        n -= 1

    sec_im = ""
    if d.get("impulse"):
        im_no = nxt()
        sec_im = (f'<section id="imp"><div class="sec-head"><span class="n">{im_no}</span>'
                  '<h2>信贷脉冲</h2><span class="hint">K05 · 社融存量同比差分</span>'
                  f'</div>{render_impulse(d["impulse"])}</section>')
    else:
        n -= 1

    sec_rt = ""
    if d.get("ratio"):
        rt_no = nxt()
        sec_rt = (f'<section id="ratio"><div class="sec-head"><span class="n">{rt_no}</span>'
                  '<h2>股债比价</h2><span class="hint">K04 · 全收益 ÷ 二级债基</span>'
                  f'</div>{render_ratio(d["ratio"])}</section>')
    else:
        n -= 1

    sec_nw = ""
    if d.get("news"):
        nw_no = nxt()
        sec_nw = (f'<section id="news"><div class="sec-head"><span class="n">{nw_no}</span>'
                  '<h2>晚间要闻</h2>'
                  f'<span class="hint">近 {d["news"]["hours"]} 小时 · '
                  f'{E(d["news"]["time"])} 更新</span></div>'
                  f'{plain("news")}{render_news(d["news"], decision.get("digest"))}</section>')
    else:
        n -= 1

    watch = decision.get("watch", [])
    w_no = nxt()
    sec_w = f'''<section id="watch"><div class="sec-head"><span class="n">{w_no}</span>
  <h2>明日关注</h2><span class="hint">不带动作，只列要盯的条件</span></div>{render_watch(watch)}</section>'''

    r_no = nxt()
    risks = decision.get("risks", [])
    sec_r = ""
    if risks:
        sec_r = f'''<section id="risk"><div class="sec-head"><span class="n">{r_no}</span>
  <h2>风险</h2></div>{render_risks(risks)}</section>'''
    else:
        n -= 1

    dock_l = decision.get("dock") or decision.get("dock_text") or "晚报只做复盘，动作留给次日盘前"
    dock_href = "#watch" if watch else "#out"

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#f2f3f6"><title>{E(title)} · {E(date)}</title>
<style>{CSS}{CSS_EXT}</style></head><body><div class="wrap">
<header>
  <div class="eyebrow">KDAS · 当日复盘 · 21:00</div>
  <h1>{E(title)}<em>/</em>{E(date)}</h1>
  <div class="meta"><i>{E(subtitle)}</i><i>数据来自 brief.py evening</i>
    <i>A股 · 港股 · 全球</i><i>不做T</i></div>
</header>

<div class="verdict"><span class="lab">一句话定调</span>{verdict}</div>

{sec_a}
{sec_h}
{sec_g}
{sec_b}
{sec_mk}
{sec_mg}
{sec_rd}
{sec_im}
{sec_rt}
{sec_nw}
{sec_w}
{sec_r}

<footer>
  <b>数据来源：</b>A股/港股/全球指数与 ETF 报价取腾讯自选股连接器快照（westock-mcp），
  失败自动降级腾讯行情 / 东方财富 / 新浪；指数资金流与 ETF 主力净额同理；
  两融为东财数据中心 T+1 披露口径；K01 份额取连接器 total_shares；K04/K05 见
  knowledge/K04、K05；要闻取东财 + 新浪 7x24。所有数字由脚本解析，未人工估算。<br>
  <b>口径提醒：</b>「主力净额」是场内交易口径，不等于申购份额，两者背离时以份额为准；
  美股在 A 股 21:00 时尚未开盘，报的是<b>昨夜收盘</b>，期货只是方向参考。<br>
  本报告为当日复盘记录，<b>不出做T动作、不构成投资建议</b>。市场有风险，决策请自行复核。
</footer>
</div>
<div class="dock"><div class="in"><div class="l"><b>{E(str(dock_l)[:34])}</b>
  晚报不含做T动作，明早 08:30 出计划</div>
  <a href="{dock_href}">看明日关注</a></div></div>
</body></html>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="evening", choices=["evening"])
    ap.add_argument("--data", required=True, help="brief.py evening 的文本输出")
    ap.add_argument("--decision", required=True, help="决策 JSON")
    ap.add_argument("--out", help="输出 HTML 路径，缺省按日期生成")
    a = ap.parse_args()

    text = read_text(a.data)
    decision = json.loads(read_text(a.decision))
    d = parse_evening(text)

    os.makedirs(OUT_DIR, exist_ok=True)
    date = decision.get("date") or d["date"]
    out = a.out or os.path.join(OUT_DIR, f"晚报_{date}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(a.mode, d, decision))

    def has(x):
        return "有" if x else "缺失"

    print(f"OK {out}")
    print(f"A股：{len(d['ashare']['indices'])} 条指数 / 成交额 "
          f"{'有' if d['ashare'].get('amount') else '缺失'}")
    print(f"港股：{len(d['hk']['indices'])} 条指数 / 南向 "
          f"{'有' if d['hk'].get('south') else '无'}")
    print(f"全球：{len(d['global']['groups'])} 组")
    print(f"宽基：{len(d['broad']['fams'])} 行族别 / {len(d['broad']['holds'])} 只持仓")
    print(f"资金面：大盘资金 {has(d.get('market'))} / 两融 {has(d.get('margin'))}"
          f" / K05 {has(d.get('impulse'))} / K04 {has(d.get('ratio'))}")
    print(f"要闻：{'已解析' if d.get('news') else '缺失'} / "
          f"{len((d.get('news') or {}).get('rows') or [])} 条")
    print(f"决策：解读 {len(decision.get('reads', []))} 张 / "
          f"明日关注 {len(decision.get('watch', []))} 条 / "
          f"风险 {len(decision.get('risks', []))} 条")


if __name__ == "__main__":
    main()
