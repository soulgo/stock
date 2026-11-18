#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""panel.py —— 面板契约：文本 ⇄ dict（ADR-003）。

面板文本是 brief.py 与渲染层之间唯一的接口。这份契约以前分散在 report_gen.py
（16 个 parse_*）与 evening_gen.py（5 个 parse_*）里，谁都能改格式、谁都不会报错，
结果就是报告静默少一块（HANDOFF 坑 #3/#4/#5 同一病根）。

现在集中到一处：
  - **解析**：parse_morning / parse_noon / parse_evening —— 文本 → dict（字段口径即渲染层要的形状）
  - **快照**：dump / load —— brief.py 每次跑完顺手落一份 JSON，渲染层可以直接读，不再碰正则
  - **校验**：validate(d, mode) —— 段数、行数、必备字段不对就抛 PanelError。
    这是关键：以前「格式漂移」表现为报告里少一块（没人发现），现在是早盘 08:30 直接报错。

边界：本模块只做文本 ⇄ 数据，**不产生任何 HTML**；样式全部留在 report_gen / evening_gen。
"""
import hashlib
import io
import json
import re
from datetime import datetime


class PanelError(Exception):
    """面板契约不满足。宁可当场失败，也不交付一份缺段的报告。"""


def num(s):
    """'3,654.95' -> 3654.95 ; '509万' -> 509（只去单位符号，量级换算交给 yuan）"""
    t = (str(s).replace(",", "").replace("%", "").replace("+", "")
         .replace("亿", "").replace("万", "").strip())
    try:
        return float(t)
    except Exception:
        return 0.0

def yuan(s):
    """'-2880万' / '+1.63亿' -> 元(float)"""
    try:
        s = str(s).strip()
        k = 1e8 if "亿" in s else 1e4 if "万" in s else 1.0
        return num(s) * k
    except Exception:
        return 0.0

def short_name(s):
    return s.replace("(待确认)", "").replace("汇添富", "").strip()

def sec_title(text, title, next_title=None):
    """按段标题关键字切段。序号只当「任意中文数字前缀」，不再当主键——
    brief.py 重排段号、插入新指标都不会让解析错位（旧 section() 靠「一/二」定位，会整链错位）。
    next_title=None 表示取到下一个段头或「附：」为止。
    注意：不匹配时会返回空串而不是抛错（该段缺失时报告自动跳过，不静默串位）。"""
    m = re.search(r"^\s*[一二三四五六七八九十]+、" + re.escape(title) + r".*$", text, re.M)
    if not m:
        return ""
    tail = text[m.end():]
    if next_title is None:
        n = re.search(r"^\s*(?:附[：:]|[一二三四五六七八九十]+、)", tail, re.M)
    else:
        n = re.search(r"^\s*[一二三四五六七八九十]+、" + re.escape(next_title), tail, re.M)
    return tail[:n.start()] if n else tail


# ------------------------------------------------------------------ 解析

def parse_common(text):
    m = re.search(r"【KDAS\s*(早盘|午盘|晚报)面板】(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", text)
    return {"date": m.group(2) if m else datetime.now().strftime("%Y-%m-%d"),
            "time": m.group(3) if m else "",
            "phase": m.group(1) if m else "",
            "holiday": ("休市" in text[:200])}

def parse_indices(block):
    out = []
    for ln in block.splitlines():
        m = re.match(r"^\s{2}(\S.*?)\s{1,}([\d,]+\.?\d*)\s+([+-][\d.]+%)(?:\s+(成交额\s+[\d,]+\s*亿))?", ln)
        if m:
            out.append({"name": m.group(1).strip(), "price": m.group(2),
                        "pct": num(m.group(3)), "amount": (m.group(4) or "").replace("成交额", "").strip()})
    return out

def parse_noon_quote(block):
    out = []
    pat = re.compile(r"^\s{2}(.+?)\s{1,}([\d.]+)\s+([+-][\d.]+%)\s+([\d.]+)\s+(均价上方|均价下方|—)\s+([\d.]+%)\s+([\d.]+)\s+([\d.]+亿)\s*$")
    for ln in block.splitlines():
        m = pat.match(ln)
        if m:
            out.append({"name": short_name(m.group(1)), "price": m.group(2),
                        "pct": num(m.group(3)), "avg": m.group(4),
                        "pos": m.group(5), "turn": m.group(6),
                        "vr": m.group(7), "amount": m.group(8)})
    return out

def parse_flow(block):
    out = []
    pat = re.compile(r"^\s{2}(.+?)\s+([+-]?[\d.]+[亿万])\s+([+-]?[\d.]+[亿万])\s+([+-]?[\d.]+[亿万])\s+([+-]?[\d.]+[亿万])\s+([+-]?[\d.]+[亿万])\s+(.+)$")
    for ln in block.splitlines():
        m = pat.match(ln)
        if m:
            out.append({"name": short_name(m.group(1)),
                        "main": yuan(m.group(2)), "huge": yuan(m.group(3)),
                        "big": yuan(m.group(4)), "mid": yuan(m.group(5)),
                        "small": yuan(m.group(6)), "tag": m.group(7).strip(),
                        "main_s": m.group(2)})
    return out

def parse_intraday(block):
    """解析「四、日内位置与量能」。
    分时接口不可用时 brief.py 只输出「… N%  振幅 X%」，没有「，较昨日同时点 …」，
    后半段必须是可选的，否则整行匹配失败、日内位置会退化成分组默认值 50%。"""
    out = {}
    pat = re.compile(r"^\s{2}(.+?)\s+日内\s+\S+\s+(\d+)%\s+振幅\s+([\d.]+%)(?:，\s*(.+?))?\s*$")
    for ln in block.splitlines():
        m = pat.match(ln)
        if m:
            out[short_name(m.group(1))] = {"pos": int(m.group(2)),
                                           "amp": m.group(3),
                                           "vol": (m.group(4) or "").strip() or "量比看右行"}
    return out

def parse_levels(block):
    out = {}
    # 支撑/压力/距离都可能是「—」（创新高无压力、或全线上方无支撑），不能用 [\d.]+ 卡死
    pat = re.compile(r"^\s{2}(.+?)\s+([\d.]+)\s+([\d.—]+)\s+([+-][\d.]+%|—)\s+"
                     r"([\d.—]+)\s+([+-][\d.]+%|—)\s+(\S+)\s*$")
    for ln in block.splitlines():
        m = pat.match(ln)
        if m:
            out[short_name(m.group(1))] = {"price": m.group(2), "sup": m.group(3),
                                           "d_sup": m.group(4), "res": m.group(5),
                                           "d_res": m.group(6), "state": m.group(7)}
    # 第二遍：接过表格下方的「强弱分界」行，把它挂回对应标的
    for ln in block.splitlines():
        if "强弱分界" not in ln:
            continue
        for seg in ln.split("强弱分界", 1)[1].split("｜"):
            m2 = re.match(r"\s*(.+?)\s+([\d.]+)\((up|down|—),(\d+)日\)", seg)
            if m2 and short_name(m2.group(1)) in out:
                out[short_name(m2.group(1))].update(
                    {"weak": m2.group(2), "weak_dir": m2.group(3),
                     "weak_age": m2.group(4)})
    return out

def parse_noon(text):
    d = parse_common(text)
    d["indices"] = parse_indices(sec_title(text, "A股上午", "持仓上午表现"))
    d["quotes"] = parse_noon_quote(sec_title(text, "持仓上午表现", "资金流（上午累计"))
    d["flows"] = parse_flow(sec_title(text, "资金流（上午累计", "日内位置与量能"))
    d["intraday"] = parse_intraday(sec_title(text, "日内位置与量能", "日线关键位"))
    d["levels"] = parse_levels(sec_title(text, "日线关键位", "异动提示"))
    body = sec_title(text, "异动提示")
    d["alerts_raw"] = " ".join(x.strip() for x in (body.split("；") if body else []) if x.strip())
    d["market"] = parse_market(text)
    d["margin"] = parse_margin(text)
    d["tstats"] = parse_tstats(text)   # 备用：报告暂不显示
    d["news"] = parse_news(text)
    d["impulse"] = parse_impulse(text)   # K05 信贷脉冲
    d["ratio"] = parse_ratio(text)     # K04 股债比价
    return d

def parse_morning_levels(block):
    # 末尾多一列 KDAS 六状态；支撑/压力/T空间可能是「—」（无线上方/创新高）
    pat = re.compile(r"^\s{2}(.+?)\s+([\d.]+)\s+([\d.]+)\s+([+-][\d.]+%)\s+"
                     r"([\d.—]+)\s+([\d.—]+)\s+([\d.]+%|—)\s+([\d.]+%|—)\s+(\S+)\s*$")
    out = []
    for ln in block.splitlines():
        m = pat.match(ln)
        if m:
            out.append({"name": short_name(m.group(1)), "price": m.group(2),
                        "cost": m.group(3), "pnl": num(m.group(4)),
                        "sup": m.group(5), "res": m.group(6),
                        "space": m.group(7), "amp5": m.group(8),
                        "state": m.group(9)})
    # 接过表格下方的「强弱分界」行
    weak = {}
    for ln in block.splitlines():
        if "强弱分界" not in ln:
            continue
        for seg in ln.split("强弱分界", 1)[1].split("｜"):
            m2 = re.match(r"\s*(.+?)\s+([\d.]+)\((up|down|—),(\d+)日\)", seg)
            if m2:
                weak[short_name(m2.group(1))] = (m2.group(2), m2.group(3))
    for r in out:
        if r["name"] in weak:
            r["weak"], r["weak_dir"] = weak[r["name"]]
    return out

def parse_focus(block):
    out = {}
    for ln in block.splitlines():
        m = re.match(r"^\s*·\s*(.+?)：(.+)$", ln)
        if m:
            out[short_name(m.group(1))] = m.group(2).strip()
    return out

def parse_morning(text):
    d = parse_common(text)
    d["us"] = parse_indices(sec_title(text, "隔夜美股", "亚洲早盘"))
    d["asia"] = parse_indices(sec_title(text, "亚洲早盘", "持仓今日关键位"))
    d["levels"] = parse_morning_levels(sec_title(text, "持仓今日关键位", "今日重点"))
    d["focus"] = parse_focus(sec_title(text, "今日重点"))
    m = re.search(r"持仓市值合计[^：]*：([\d,]+)\s*元", text)
    d["total"] = m.group(1) if m else None
    d["market"] = parse_market(text)
    d["margin"] = parse_margin(text)
    d["tstats"] = parse_tstats(text)   # 备用：报告暂不显示
    d["news"] = parse_news(text)
    d["impulse"] = parse_impulse(text)   # K05 信贷脉冲
    d["ratio"] = parse_ratio(text)     # K04 股债比价
    return d

def parse_impulse(text):
    """解析 brief.py 的「X、信贷脉冲（K05 ...）」块。缺失返回 None，报告自动跳过该段。"""
    m = re.search(r"^\s*[一二三四五六七八九十]+、信贷脉冲.*$", text, re.M)
    if not m:
        return None
    body = text[m.end():]
    n = re.search(r"^\s*(?:[一二三四五六七八九十]+、|附[：:])", body, re.M)
    if n:
        body = body[:n.start()]

    def f(pat, d=None):
        mm = re.search(pat, body)
        return float(mm.group(1).replace(",", "")) if mm else d

    def st(pat, d=""):
        mm = re.search(pat, body)
        return mm.group(1).strip() if mm else d

    p_all = f(r"脉冲\s+([+-][\d.]+)\s*pp")
    if p_all is None:
        return None
    return {
        "p": p_all,
        "band": st(r"脉冲\s+[+-][\d.]+\s*pp\s+档位\s+(\S+)"),
        "p_ex": f(r"私人部门\s+([+-][\d.]+)"),
        "yoy": f(r"同比\s+([+-][\d.]+)%"),
        "px": f(r"沪深300\s+([\d,]+)"),
        "px_tag": st(r"沪深300\s+[\d,]+\s*·\s*([^）]+)）"),
        "k": f(r"领先\s+(\d+)\s*个月"),
        "r": f(r"r=([+-][\d.]+)"),
        "turn": st(r"拐点\s+(\S+?)（"),
        "turn_why": st(r"拐点\s+\S+?（(.+?)）"),
        "act": st(r"含义\s+(.+)"),
        "src": st(r"数据\s+(.+)"),
    }

def parse_ratio(text):
    """解析 brief.py 的「八、/十、股债比价」块（K04）。缺失返回 None，报告自动跳过该段。"""
    m = re.search(r"^\s*[一二三四五六七八九十]+、股债比价.*$", text, re.M)
    if not m:
        return None
    body = text[m.end():]
    n = re.search(r"^\s*附[：:]", body, re.M)
    if n:
        body = body[:n.start()]

    def f(pat, d=None):
        mm = re.search(pat, body)
        return float(mm.group(1).replace(",", "")) if mm else d

    def st(pat, d=""):
        mm = re.search(pat, body)
        return mm.group(1).strip() if mm else d

    pair = lambda a, b, c: {"k": a, "p": float(b.replace(",", "")), "d": float(c)}
    lower = [pair(*m3) for m3 in re.findall(r"(1\.00|0\.97|0\.95)线\s+([\d,]+)（([+-][\d.]+)%）", body)]
    upper = [pair(*m3) for m3 in re.findall(r"(1\.30|1\.50)线\s+([\d,]+)（([+-][\d.]+)%）", body)]
    if not (lower and upper and f(r"比价\s+([\d.]+)")):
        return None
    return {"ratio": f(r"比价\s+([\d.]+)"), "band": st(r"档位\s+(\S+)"),
            "px": f(r"沪深300\s+([\d,.]+)"), "tr": f(r"全收益\s+([\d,.]+)"),
            "bond": f(r"债腿\s+([\d,.]+)"), "lower": lower, "upper": upper,
            "corr": f(r"走廊\s+([\d]+)%", 0),
            "hint": st(r"含义\s+(.+)"), "hold": st(r"持仓\s+(.+)"), "src": st(r"口径\s+(.+)"),
            "bond_t": re.findall(r"(51\d{4})\s+(\d+)%", body)}


# ------------------------------------------------------------------ 渲染片段

def parse_market(text):
    """解析「大盘资金与国家队」段。行格式：两空格 + 标签 + 2 空格以上 + 数值"""
    m = re.search(r"^\s*[一二三四五六七八九十]+、大盘资金与国家队.*$", text, re.M)
    if not m:
        return None
    tail = text[m.end():]
    n = re.search(r"^\s*(附：|[一二三四五六七八九十]+、)", tail, re.M)
    block = tail[:n.start()] if n else tail
    rows = {}
    for ln in block.splitlines():
        mm = re.match(r"^\s{2}(\S+)\s{2,}(.+?)\s*$", ln)
        if mm:
            rows[mm.group(1)] = mm.group(2)
    if not rows:
        return None

    def val(label, first=True):
        v = rows.get(label, "")
        m2 = re.search(r"[+-]?[\d,]+\.?\d*\s*[亿万]?", v)
        return yuan(m2.group(0)) if m2 else None

    def pair(label):
        v = rows.get(label, "")
        m2 = re.findall(r"[\d,]+", v)
        return (int(m2[0].replace(",", "")) if len(m2) > 0 else None,
                int(m2[1].replace(",", "")) if len(m2) > 1 else None)

    st = dict((k, yuan(v)) for k, v in
              re.findall(r"(\S+?)\s+([+-]?[\d.]+[亿万])", rows.get("结构", "")))
    up, down = pair("上涨/下跌")
    lu, ld = pair("涨停/跌停")
    amt_parts = re.findall(r"[\d,]+\.?\d*", rows.get("成交额", ""))
    amt = float(amt_parts[0].replace(",", "")) * 1e8 if amt_parts else None
    # 比例要单独抓「均量 xx%」，不能靠数字序号 —— 原文含「20日均量」会把 20 误当比例
    mratio = re.search(r"均量\s*([\d.]+)\s*%", rows.get("成交额", ""))
    ratio = float(mratio.group(1)) if mratio else None

    nat = []
    for seg in re.split(r"[｜|]", rows.get("载具明细", "")):
        seg = seg.strip()
        mm = re.match(r"(\d{6})\s*([+-][\d.]+[亿万])", seg)
        if mm:
            nat.append((mm.group(1), yuan(mm.group(2))))
    natsum = re.search(r"([+-]?[\d,.]+[亿万])（(\d+) 只，(\d+) 只净流入）", rows.get("载具今日", ""))

    # 同时点成交额倍数：0.80×（今日 547.0亿 / 基准 680.3亿，历史同时点已走完 94.4%）→ 量能持平
    # 晚报口径为「全天已走完 100%」，两种说法都要认，否则晚报这张卡会整块消失
    cut = None
    mt = re.search(r"([\d.]+)×（今日 ([\d,.]+)亿 / 基准 ([\d,.]+)亿，"
                   r"(?:历史同时点|全天)已走完 ([\d.]+)%）→ (.+?)\s*$", rows.get("载具同时点", ""))
    if mt:
        cut = {"x": float(mt.group(1)),
               "today": float(mt.group(2).replace(",", "")) * 1e8,
               "bench": float(mt.group(3).replace(",", "")) * 1e8,
               "prog": float(mt.group(4)), "tip": mt.group(5).strip()}

    fam = []
    for seg in re.split(r"[｜|]", rows.get("载具分族", "")):
        mm = re.match(r"(\S+)\s*([+-]?[\d.]+[亿万])", seg.strip())
        if mm:
            fam.append((mm.group(1), yuan(mm.group(2))))

    ext = None
    mo = re.search(r"([+-]?[\d.]+[亿万])（(\d+) 只，(\d+) 只净流入", rows.get("载具观察", ""))
    if mo:
        ext = {"main": yuan(mo.group(1)), "n": int(mo.group(2)), "in": int(mo.group(3))}

    k01 = None
    mk1 = re.search(r"(\d{4}-\d{2}-\d{2}) 主池 (\d+) 只合计 ([\d,.]+)亿份(.*?)｜加仓指数 ([\d.-]+)",
                    rows.get("载具份额", ""))
    if mk1:
        k01 = {"date": mk1.group(1), "pool": int(mk1.group(2)),
               "shares": float(mk1.group(3).replace(",", "")) * 1e8,
               "chg": mk1.group(4).strip("（）"), "index": float(mk1.group(5))}

    md = re.match(r"(\d{4}-\d{2}-\d{2})", rows.get("概览统计日", ""))

    return {"main": val("两市主力"), "huge": st.get("超大单"), "big": st.get("大单"),
            "mid": st.get("中单"), "small": st.get("小单"),
            "main5": val("主力5日"), "main10": val("主力10日"), "main20": val("主力20日"),
            "up": up, "down": down, "limit_up": lu, "limit_dn": ld,
            "amount": amt, "amount_ratio": ratio,
            "amount_date": md.group(1) if md else None,
            "nat_main": yuan(natsum.group(1)) if natsum else None,
            "nat_n": int(natsum.group(2)) if natsum else 0,
            "nat_in": int(natsum.group(3)) if natsum else 0,
            "nat5": val("载具5日"), "nat_size": val("载具规模"), "nat": nat,
            "cut": cut, "fam": fam, "ext": ext, "k01": k01}

def parse_margin(text):
    """解析「融资融券」段。行格式与大盘资金段一致：两空格 + 标签 + 2 空格以上 + 数值。
    两融是 T+1 披露的杠杆仓位表，和当日资金流不是同一个时间尺度，渲染时必须标日期。"""
    m = re.search(r"^\s*[一二三四五六七八九十]+、融资融券.*$", text, re.M)
    if not m:
        return None
    tail = text[m.end():]
    n = re.search(r"^\s*(附：|[一二三四五六七八九十]+、)", tail, re.M)
    rows = {}
    for ln in (tail[:n.start()] if n else tail).splitlines():
        mm = re.match(r"^\s{2,}(\S+)\s{2,}(.+?)\s*$", ln)
        if mm:
            rows[mm.group(1)] = mm.group(2)
    if not rows or "无数据" in rows.get("两融余额", "无数据"):
        return None

    def one(label):
        mm = re.search(r"[+-]?[\d,]+\.?\d*\s*[亿万]?", rows.get(label, ""))
        return yuan(mm.group(0)) if mm else None

    def pctof(label):
        mm = re.search(r"([\d.]+)%", rows.get(label, ""))
        return float(mm.group(1)) if mm else None

    md = re.search(r"(\d{4}-\d{2}-\d{2})", rows.get("两融余额", ""))
    mdp = re.search(r"(\d{4}-\d{2}-\d{2})", rows.get("两融数据日", ""))
    chg = {k: yuan(v) for k, v in
           re.findall(r"(日变化|5日|10日)\s*([+-]?[\d.]+[亿万])", rows.get("融资余额", ""))}
    bs = re.findall(r"([\d,.]+亿)", rows.get("融资买入/偿还", ""))
    ix = re.search(r"([\d,.]+)\s*([+-][\d.]+%)｜5日\s*([+-][\d.]+%)｜10日\s*([+-][\d.]+%)",
                   rows.get("指数同步", ""))
    pos = []
    for seg in re.split(r"[｜|]", rows.get("持仓两融", "")):
        mm = re.match(r"\s*(\d{6})\s+([\d.]+)亿\(净买([+-]?\d+)万/5日([+-]?\d+)万\)", seg)
        if mm:
            pos.append({"code": mm.group(1), "rzye": float(mm.group(2)) * 1e8,
                        "rzjme": int(mm.group(3)) * 1e4,
                        "rzjme5": int(mm.group(4)) * 1e4})
    return {"rzrqye": one("两融余额"), "date": md.group(1) if md else None,
            "rzye": one("融资余额"), "zb": pctof("融资余额占比"),
            "rzjme": chg.get("日变化"), "rzjme5": chg.get("5日"), "rzjme10": chg.get("10日"),
            "buy": yuan(bs[0]) if len(bs) > 0 else None,
            "sell": yuan(bs[1]) if len(bs) > 1 else None,
            "rqye": one("融券余额"),
            "idx": float(ix.group(1).replace(",", "")) if ix else None,
            "zdf": num(ix.group(2)) if ix else None,
            "zdf5": num(ix.group(3)) if ix else None,
            "zdf10": num(ix.group(4)) if ix else None,
            "dir": rows.get("杠杆方向", "").strip(),
            "pos": pos, "pos_date": mdp.group(1) if mdp else None,
            "note": rows.get("两融口径", "").strip()}

def parse_tstats(text):
    """解析「做T 战果与降本进度」段。逐只降本是多行表格，续行以代码开头、深缩进。"""
    m = re.search(r"^\s*[一二三四五六七八九十]+、做T 战果.*$", text, re.M)
    if not m:
        return None
    tail = text[m.end():]
    n = re.search(r"^\s*(附：|[一二三四五六七八九十]+、)", tail, re.M)
    rows = {}
    for ln in (tail[:n.start()] if n else tail).splitlines():
        mm = re.match(r"^\s{2,}(\S+)\s{2,}(.+?)\s*$", ln)
        if mm:
            rows[mm.group(1)] = mm.group(2)
    if not rows or "无数据" in rows.get("已实现收益", "无数据"):
        return None

    rl = re.search(r"([+-]?[\d,.]+)\s*元(?:（占总资产\s*([\d.]+)%）)?", rows.get("已实现收益", ""))
    parts = []
    for seg in re.split(r"[｜|]", rows.get("收益构成", "")):
        mm = re.match(r"\s*(\d{6})\s*([+-][\d,.]+)", seg)
        if mm:
            parts.append({"code": mm.group(1), "v": float(mm.group(2).replace(",", ""))})
    mr = re.search(r"完成 (\d+) 次｜未结 (\d+) 笔", rows.get("T往返", ""))
    mo = re.search(r"未结 \d+ 笔（(.+)）", rows.get("T往返", ""))
    hl = []
    for code, v in rows.items():
        mm = re.match(r"^(\S+)\s+([\d.]+)→([\d.]+)（([+-][\d.]+)%）(.*?)(?:｜(.+))?$", v)
        if re.match(r"^\d{6}$", code) and mm:
            hl.append({"code": code, "name": mm.group(1), "from": mm.group(2),
                       "to": mm.group(3), "chg": float(mm.group(4)),
                       "diluted": "加仓摊薄" in mm.group(5),
                       "tail": (mm.group(6) or "").strip()})
    eff = re.search(r"(\d{6})\s+一次 1\.5% 倒T（上限 ([\d,]+) 份）≈ ([+-]\d+) 元 "
                    r"≈ 成本降 ([\d.]+)｜回本涨幅每降 1 个百分点需 ([\d.]+) 次",
                    rows.get("降本效率", ""))
    return {"realized": float(rl.group(1).replace(",", "")) if rl else None,
            "pct": float(rl.group(2)) if (rl and rl.group(2)) else None,
            "parts": parts,
            "rounds": int(mr.group(1)) if mr else 0,
            "open_n": int(mr.group(2)) if mr else 0,
            "open_note": (mo.group(1) if mo else ""),
            "rows": hl,
            "eff": {"code": eff.group(1), "cap": int(eff.group(2).replace(",", "")),
                    "gain": float(eff.group(3)), "drop": float(eff.group(4)),
                    "need": float(eff.group(5))} if eff else None,
            "note": rows.get("做T口径", "").strip()}

def parse_news(text):
    """解析「实时快讯」段。条目行：两空格 + MM-DD HH:MM + 两空格 + 相关/快讯 + 标题｜来源"""
    m = re.search(r"^\s*[一二三四五六七八九十]+、实时快讯.*$", text, re.M)
    if not m:
        return None
    tail = text[m.end():]
    n = re.search(r"^\s*(附：|[一二三四五六七八九十]+、)", tail, re.M)
    block = tail[:n.start()] if n else tail
    rows = {}
    for ln in block.splitlines():
        mm = re.match(r"^\s{2,}(\S+)\s{2,}(.+?)\s*$", ln)
        if mm:
            rows.setdefault(mm.group(1), mm.group(2))
    if not rows or "无数据" in rows.get("更新时间", "无数据"):
        return None
    mt = re.search(r"(\d{2}:\d{2})（近 (\d+) 小时，去重后 (\d+) 条，相关 (\d+) 条）",
                   rows.get("更新时间", ""))
    items = []
    for ln in block.splitlines():
        mm = re.match(r"^\s{2}(\d{2}-\d{2} \d{2}:\d{2})\s{2,}(相关|快讯)\s+(.+?)｜(\S+)\s*$", ln)
        if mm:
            items.append({"time": mm.group(1), "hot": mm.group(2) == "相关",
                          "title": mm.group(3).strip(), "src": mm.group(4)})
    return {"time": mt.group(1) if mt else "",
            "hours": int(mt.group(2)) if mt else 0,
            "total": int(mt.group(3)) if mt else len(items),
            "hit": int(mt.group(4)) if mt else 0,
            "rows": items,
            "note": rows.get("快讯口径", "").strip()}

GROUPS = ("亚太收盘", "欧洲收盘", "美股昨夜", "期货当前", "汇率商品")


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


# ------------------------------------------------------------------ 快照
def text_sha(text):
    """面板文本的指纹。换行归一后取 sha256 前 16 位 —— Windows 重定向是 \\r\\n、
    内存里捕获的是 \\n，不归一就永远对不上。"""
    return hashlib.sha256((text or "").replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def dump(d, path, mode, text=None):
    """把一次面板读数落成 JSON 快照（brief.py 在打印完文本后调用）。

    带上文本指纹：渲染层据此确认「这份快照确实由这份文本解析而来」。
    只靠 mtime 不行——补跑历史日期时，旧文本会被更新的快照顶替，静默用错数据。
    """
    payload = {"mode": mode, "written": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "text_sha": text_sha(text) if text is not None else None,
               "panel": d}
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return path


def load(path):
    """读 JSON 快照，返回 (mode, panel, text_sha)。文件坏了返回全 None，调用方回落文本。"""
    try:
        with io.open(path, encoding="utf-8") as f:
            o = json.load(f)
        return o.get("mode"), o.get("panel"), o.get("text_sha")
    except Exception:
        return None, None, None


# ------------------------------------------------------------------ 校验
# 每个模式「最少要有什么」。数量不写死成 4/7（持仓会变），只约束下限与必备段。
REQUIRED = {
    "morning": ("date", "us", "levels", "focus"),
    "noon": ("date", "indices", "quotes", "flows", "intraday", "levels"),
    "evening": ("date", "ashare", "hk", "global", "broad"),
}


def validate(d, mode, strict=True):
    """字段级校验。返回问题列表；strict=True 时直接抛 PanelError。

    为什么要有它：格式漂移在过去只表现为「报告里少一块」，而少的那块往往是
    资金面或关键位——正是最影响下单判断的部分。这里让它变成启动即失败。
    """
    bad = []
    if not isinstance(d, dict):
        bad.append("面板数据不是 dict")
    else:
        for k in REQUIRED.get(mode, ()):
            v = d.get(k)
            if v in (None, [], {}, ""):
                bad.append("缺少必备段：%s" % k)
        lv = d.get("levels")
        if isinstance(lv, (list, dict)) and len(lv) == 0:
            bad.append("关键位为空（持仓表解析失败）")
        if d.get("holiday") and mode != "evening":
            bad = []          # 休市日只有一句提示，不校验
    if bad and strict:
        raise PanelError("%s 面板契约不满足：%s" % (mode, "；".join(bad)))
    return bad
