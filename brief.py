#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
brief.py — 持仓做T数据面板（免密钥）

数据源（2026-11-16 起）：
  报价    腾讯自选股同源 qt.gtimg.cn   为主，东财 push2 交叉校验（价差 >0.2% 报警）
  日线    东财 kline（含真实成交额）   为主，腾讯 ifzq qfq 兜底
  分时/资金流  东财 trends2 / fflow （腾讯无对应免费接口）

  python brief.py morning    # 08:30 用：隔夜美股 + 亚洲早盘 + 今日关键位
  python brief.py noon       # 12:30 用：A股上午 + 资金流 + 日内位置 + 关键位
  python brief.py evening    # 21:00 用：A股/港股/全球收盘 + 宽基ETF + 国家队与大单 + 两融 + 要闻
  python brief.py morning --src em     # 强制走东财（腾讯异常时用）
  python brief.py morning --selftest 512170   # 打印腾讯原始字段，核对字段位
  python brief.py --signals backfill   # 回填 signals.md 的 fwd20（离线，用表内未来点位）

扩展指标（2026-11-18 起）：在 BLOCKS 注册表加一行 (key, 标题关键字, 函数, 模式) 就行，
段号自动顺延；report_gen 按标题关键字解析，重排/插入都不会错位。新指标只要在函数里调一次
signal(key, value, band, hs300)，读数就自动进 signals.md 台账，供半年后按档位回溯收益。

设计原则：脚本只给数据与事实，不做买卖决策；决策由使用者（或上层策略）完成。
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from paths import DATA, ROOT, data    # 路径唯一真源（ADR-001），不要自己拼 data/
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TOKEN = "fa5fd1943c7b386f172d6893dbfba10b"


# ---------------------------------------------------------------- 网络
EM_FAIL = [0]        # 东财连续失败计数：≥3 次后本次运行直接走兜底，避免每只标的都等超时
EM_PREF = [None]     # 记住本次运行实际可用的东财主机，后续请求直接走它

# 东财同一份数据有多个主机镜像，但实测它们各自混用缓存：
# 2026-11-16 10:53 同一秒请求 push2 四个镜像，f43 报价与 f86 时间戳在「实时（滞后 1s）」
# 与「10:38 的旧快照（滞后 899s）」之间随机跳，且四个镜像没有稳定规律。
# 结论：东财**不能**单独作为报价源，报价改由腾讯 qt.gtimg.cn 担当（与新浪逐分一致）；
# 东财保留在日线（成交额真实）与分时/资金流上，并用 f86 做新鲜度门禁。
EM_HOSTS = ["push2.eastmoney.com", "push2delay2.eastmoney.com",
            "push2his.eastmoney.com", "push2delay.eastmoney.com"]


def _swap_host(url, host):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, host, p.path, p.query, p.fragment))


def _get(url, ref="https://quote.eastmoney.com/", decode="utf-8", tries=2):
    em = "eastmoney.com" in url
    if em and EM_FAIL[0] >= 3:
        raise RuntimeError("eastmoney 本次运行已判定不可用，跳过")

    # 组装候选主机：东财 URL 走主机轮换，其它源（新浪/腾讯）保持原样
    if em:
        host = urllib.parse.urlsplit(url).hostname
        order = list(EM_HOSTS)
        if host in order:
            order.remove(host)
        first = EM_PREF[0] or host
        order = [first] + [h for h in order if h != first]
        cands = [(h, url if h == host else _swap_host(url, h)) for h in order]
    else:
        cands = [(None, url)]

    last = None
    for cand_host, cand_url in cands:
        for i in range(tries):
            try:
                req = urllib.request.Request(cand_url, headers={"User-Agent": UA, "Referer": ref})
                with urllib.request.urlopen(req, timeout=8) as r:
                    out = r.read().decode(decode, "ignore")
                if em:
                    EM_FAIL[0] = 0
                    if cand_host:
                        EM_PREF[0] = cand_host
                return out
            except Exception as e:          # 网络代理偶发 502，重试
                last = e
                time.sleep(0.3 * (i + 1))
    if em:
        EM_FAIL[0] += 1
    raise RuntimeError(f"取数失败: {url[:80]} ({last})")


def jget(url, ref="https://quote.eastmoney.com/"):
    return json.loads(_get(url, ref))


def secid(code):
    return ("1." if code.startswith(("5", "6", "9")) else "0.") + code


# ---------------------------------------------------------------- 行情
def sina_code(code):
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


# 报价源：auto=腾讯为主+东财校验 / tx=只用腾讯 / em=只用东财
SRC = ["auto"]


# ---------------------------------------------------------------- 腾讯（自选股同源）
# 腾讯自选股 App 的行情就是 qt.gtimg.cn 这一份，字段用 ~ 分隔、位置固定。
# 位置索引错一位会整列偏移，所以解析后必须过三重自检（时间戳 / 涨跌幅自洽 / 高低价包络），
# 任一条不过就抛错、由上层回落东财 —— 宁可回落，也不让错位的字段进报告。
TX_FAIL = [0]        # 腾讯连续失败计数，≥3 后本次运行不再尝试
XLOG = []            # 交叉校验差异 (code, 项目, 腾讯值, 东财值, 差%)
XSTALE = []          # 东财返回陈旧快照 (code, 滞后秒)
SRC_STAT = {"mx": 0, "tx": 0, "em": 0, "sina": 0}


def _f(lst, i, default=None):
    """安全取第 i 个字段并转 float"""
    try:
        v = (lst[i] or "").strip()
        return float(v) if v not in ("", "-") else default
    except Exception:
        return default


def quote_tx(code):
    """腾讯自选股同源报价。字段位：3 现价 / 4 昨收 / 5 今开 / 30 时间 /
    31 涨跌 / 32 涨跌% / 33 最高 / 34 最低 / 36 成交量(手) / 37 成交额(万元) /
    38 换手率 / 49 量比。"""
    if TX_FAIL[0] >= 3:
        raise RuntimeError("腾讯本次运行已判定不可用，跳过")
    sym = sina_code(code)
    txt = _get(f"https://qt.gtimg.cn/q={sym}", ref="https://gu.qq.com/", decode="gbk")
    if '="' not in txt:
        TX_FAIL[0] += 1
        raise RuntimeError("腾讯返回格式异常")
    body = txt.split('="', 1)[1].rstrip().rstrip(";").rstrip('"')
    f = body.split("~")
    if len(f) < 40:
        TX_FAIL[0] += 1
        raise RuntimeError(f"腾讯字段不足({len(f)})")

    price = _f(f, 3, 0.0) or 0.0
    prev = _f(f, 4, 0.0) or 0.0
    op = _f(f, 5, 0.0) or 0.0
    high = _f(f, 33, 0.0) or 0.0
    low = _f(f, 34, 0.0) or 0.0
    chg = _f(f, 31)
    pctv = _f(f, 32)
    vol = _f(f, 36, 0.0) or 0.0            # 手
    amt = (_f(f, 37, 0.0) or 0.0) * 1e4     # 万元 → 元
    turn = _f(f, 38)
    vr = _f(f, 49)
    t = (f[30] or "").strip()

    # 自检 1：时间戳 14 位 yyyymmddHHMMSS
    ok_time = len(t) == 14 and t.isdigit() and t[:2] in ("19", "20")
    # 自检 2：涨跌幅与 现价/昨收 自洽（盘前 price=0 时跳过）
    ok_pct = True
    if price and prev:
        ok_pct = pctv is None or abs((price / prev - 1) * 100 - pctv) < 0.06
        tol = max(0.0031, prev * 0.002)
        ok_pct = ok_pct and chg is not None and abs((price - prev) - chg) <= tol
    # 自检 3：最高/最低必须包住现价与开盘
    ok_box = True
    if price and high and low:
        ok_box = high >= max(price, op or price) - 1e-9 and low <= min(price, op or price) + 1e-9
    if not (ok_time and ok_pct and ok_box):
        TX_FAIL[0] += 1
        raise RuntimeError(f"腾讯字段自检未过 time={ok_time} pct={ok_pct} range={ok_box}")

    TX_FAIL[0] = 0
    return {
        "code": code, "name": (f[1] or "").strip() or None,
        "price": price, "high": high, "low": low, "open": op, "prev": prev,
        "avg": (amt / (vol * 100)) if vol else None,
        "vol": vol, "amount": amt or None,
        "vr": vr, "turn": turn,
        "chg": chg, "pct": pctv,
        "src": "tx", "ts": t,
    }


def _xcheck(code, tx, em):
    """腾讯 vs 东财 逐项比对，价差 >0.2% 记入 XLOG（不改数，只报警）
    东财快照若已滞后（实测会出现十几分钟前的缓存），差值是假差异，直接跳过比对。"""
    lag = em.get("lag")
    if lag is not None and lag > 120:
        XSTALE.append((code, lag))
        return
    for label, ka in (("现价", "price"), ("昨收", "prev")):
        a, b = tx.get(ka), em.get(ka)
        a = a or (tx.get("prev") if ka == "price" else None)
        b = b or (em.get("prev") if ka == "price" else None)
        if not a or not b:
            continue
        XCNT[0] += 1
        d = abs(a - b) / b * 100
        if d > 0.2:
            XLOG.append((code, label, a, b, d))


def close_check(code, q, lv):
    """报价昨收 vs 日线末根收盘：复权口径/停牌错位会让整组 KDAS 线偏移，>0.15% 就报。
    盘中运行时末根 K 线是「今天」的半根，其收盘≈现价，与昨收本来就不等，故跳过。"""
    a, b = (lv or {}).get("price"), (q or {}).get("prev")
    if not a or not b:
        return
    if (lv or {}).get("date") == datetime.now().strftime("%Y-%m-%d"):
        return
    d = abs(a - b) / b * 100
    if d > 0.15:
        XLOG.append((code, "日线收盘vs昨收", a, b, d))


# ---------------------------------------------------------------- 连接器快照
# 腾讯自选股连接器（westock-mcp）只能由上层 agent 调用，Python 脚本无法直连 MCP；
# 因此跑报告前先把 data_quote / data_fund_flow 的返回落成 data/_mx_<date>.json，
# 本脚本读它。超过 TTL 即视为过期、自动回落实时接口，绝不拿旧快照冒充实时。
MX = {"data": None, "age": None}
MX_TTL = 3          # 分钟


def _mx_path():
    return data(f"_mx_{datetime.now():%Y-%m-%d}.json")


def load_mx():
    p = _mx_path()
    if not os.path.exists(p):
        return
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        age = (datetime.now() - datetime.strptime(d["written"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        MX["age"] = age
        MX["data"] = d if age <= MX_TTL else None
    except Exception:
        MX["data"] = None


def quote_mx(code):
    """连接器报价 → 与 quote_em/quote_tx 同构。volume 单位股，这里折成手。"""
    d = ((MX["data"] or {}).get("quotes") or {}).get(code)
    if not d:
        return None
    vol = d.get("volume") or 0
    return {
        "code": code, "name": d.get("name"),
        "price": d.get("price"), "high": d.get("high"), "low": d.get("low"),
        "open": d.get("open"), "prev": d.get("prev_close"),
        "avg": d.get("avg_price"), "vol": vol / 100 if vol else None,
        "amount": d.get("amount"),
        "vr": d.get("volume_ratio"), "turn": d.get("turnover_rate"),
        "chg": d.get("change"), "pct": d.get("change_percent"),
        "src": "mx",
    }


# ---------------------------------------------------------------- 行情
def quote(code):
    """连接器快照 → 腾讯 → 东财 → 新浪，逐级降级；全挂才算失败。"""
    q = quote_mx(code)
    if q and (q.get("price") or q.get("prev")):
        SRC_STAT["mx"] = SRC_STAT.get("mx", 0) + 1
        return q
    tx = em = None
    if SRC[0] != "em":
        try:
            tx = quote_tx(code)
        except Exception:
            tx = None
    if SRC[0] != "tx":
        try:
            em = quote_em(code)
        except Exception:
            em = None
    if tx and em:
        _xcheck(code, tx, em)
    q = tx or em
    if not q:                                   # 两家都挂，最后试新浪（仅价格，无换手/量比）
        try:
            q = quote_sina(code)
        except Exception:
            q = None
    if not q:
        raise RuntimeError(f"{code} 报价取数失败（腾讯/东财/新浪均不可用）")
    SRC_STAT[q["src"]] = SRC_STAT.get(q["src"], 0) + 1
    return q


def quote_em(code):
    f = "f43,f44,f45,f46,f47,f48,f50,f57,f58,f59,f60,f71,f86,f168,f169,f170"
    d = jget(f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid(code)}&fields={f}").get("data")
    if not d:
        return None
    p = 10 ** d.get("f59", 2)
    g = lambda k: d.get(k) if d.get(k) in ("-", None) else d[k] / p
    # f86 = 快照时间戳（秒）。东财多镜像混用缓存，实测会返回十几分钟前的旧快照，
    # 用它算 lag，让对方（腾讯）做时效裁决，陈旧的一律不参与交叉校验。
    lag = None
    if isinstance(d.get("f86"), (int, float)) and d["f86"] > 0:
        lag = max(0, time.time() - d["f86"])
    return {
        "code": code, "name": d.get("f58"),
        "price": g("f43"), "high": g("f44"), "low": g("f45"), "open": g("f46"),
        "prev": g("f60"), "avg": g("f71"),
        "vol": d.get("f47"), "amount": d.get("f48"),
        "vr": d.get("f50") / 100 if isinstance(d.get("f50"), (int, float)) else None,
        "turn": d.get("f168") / 100 if isinstance(d.get("f168"), (int, float)) else None,
        "chg": g("f169"), "pct": d["f170"] / 100,   # f170 恒为百分比×100，与小数位无关
        "src": "em", "lag": lag,
    }


def quote_sina(code):
    """东财不可用时的兜底：新浪 hq（无换手/量比/均价字段，均价用 成交额/成交量 反算）"""
    v = sina([sina_code(code)]).get(sina_code(code))
    if not v or len(v) < 10 or not v[0]:
        return None
    f = lambda i: float(v[i] or 0)
    vol, amt = f(8), f(9)          # 股 / 元
    price, prev = f(3), f(2)
    return {
        "code": code, "name": v[0],
        "price": price, "high": f(4), "low": f(5), "open": f(1),
        "prev": prev, "avg": (amt / vol) if vol else None,
        "vol": vol / 100 if vol else None, "amount": amt,
        "vr": None, "turn": None,
        "chg": (price - prev) if prev else None,
        "pct": ((price / prev - 1) * 100) if prev else None,
        "src": "sina",
    }


# 东财挂了才用：只对已验证可靠的代码做兜底，宁缺勿错
SINA_INDEX = {"100.DJIA": "gb_$dji", "100.NDX": "gb_$ixic", "100.SPX": "gb_$inx",
              "100.HSI": "int_hangseng"}


def index_quote_em(sid, name):
    d = jget(f"https://push2.eastmoney.com/api/qt/stock/get?secid={sid}"
             f"&fields=f43,f59,f169,f170,f47,f48").get("data")
    if not d:
        return {"name": name, "ok": False}
    p = 10 ** d.get("f59", 2)
    return {"name": d.get("f58") or name, "ok": True,
            "price": d["f43"] / p, "pct": d["f170"] / 100,
            "chg": d["f169"] / p, "amount": d.get("f48")}


# 东财 secid → 连接器代码。只覆盖 A 股指数；美股/恒指/日经仍走原接口。
SID_MX = {"1.000001": "sh000001", "0.399001": "sz399001",
          "0.399006": "sz399006", "1.000688": "sh000688"}


def index_quote_mx(sid, name):
    """连接器快照里的指数报价（quotes_full 按 sh000001 这类带前缀的 key 存）"""
    code = SID_MX.get(sid)
    if not code or not MX["data"]:
        return {"name": name, "ok": False}
    qs = MX["data"].get("quotes_full") or {}
    d = qs.get(code) or {}
    if not d.get("price"):
        return {"name": name, "ok": False}
    return {"name": d.get("name") or name, "ok": True, "price": d["price"],
            "pct": d.get("change_percent"), "chg": d.get("change"),
            "amount": d.get("amount"), "src": "mx"}


def index_quote_sina(sid, name):
    sc = SINA_INDEX.get(sid)
    if not sc:
        return {"name": name, "ok": False}
    v = sina([sc]).get(sc) or sina([sc]).get(sc.split("_")[-1])
    if not v or len(v) < 3 or not v[1]:
        return {"name": name, "ok": False}
    # gb_*: 名称,现价,涨跌幅,时间,涨跌额…   int_*: 名称,现价,涨跌额,涨跌幅
    if sc.startswith("gb_"):
        pct, chg = float(v[2]), (float(v[4]) if len(v) > 4 and v[4] else None)
    else:
        chg, pct = float(v[2]), float(v[3])
    return {"name": v[0] or name, "ok": True, "price": float(v[1]),
            "pct": pct, "chg": chg, "amount": None, "src": "sina"}


def index_quote(sid, name):
    """连接器快照 → 东财 → 新浪（美股/恒指/日经连接器未覆盖，自动走后者）"""
    try:
        q = index_quote_mx(sid, name)
        if q.get("ok"):
            return q
    except Exception:
        pass
    try:
        q = index_quote_em(sid, name)
        if q.get("ok"):
            return q
    except Exception:
        pass
    try:
        return index_quote_sina(sid, name)
    except Exception:
        return {"name": name, "ok": False}


def sina(codes):
    txt = _get("https://hq.sinajs.cn/list=" + ",".join(codes),
               ref="https://finance.sina.com.cn/", decode="gbk")
    out = {}
    for line in txt.strip().split("\n"):
        if "=" not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        body = line.split('"')[1] if '"' in line else ""
        out[key] = body.split(",")
    return out


def daily_em(code, beg="20250101"):
    d = jget(f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid(code)}"
             f"&ut={TOKEN}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
             f"&klt=101&fqt=1&beg={beg}&end=20500101").get("data")
    if not d:
        return [], None
    bars = []
    for k in d["klines"]:
        p = k.split(",")
        bars.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                     "high": float(p[3]), "low": float(p[4]),
                     "vol": float(p[5]), "amount": float(p[6])})
    return bars, d.get("name")


def daily_tx(code, beg="20250101", n=320):
    """东财兜底：腾讯前复权日线 [date,open,close,high,low,vol(手)]。
    接口不返回成交额，用 收盘 × 成交量 作 VWAP 代理（KDAS 均线为量加权均价，偏差约千分之一）。
    主机轮换：2026-11-16 15:00 起 ifzq.gtimg.cn 对高频请求返回 501 反爬页，
    web.ifzq.gtimg.cn 仍正常且数据完全一致，故放前面。"""
    sym = sina_code(code)
    urls = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},qfq",
            f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},qfq")
    node, last = None, None
    for u in urls:
        for attempt in range(2):
            try:
                d = jget(u, ref="https://gu.qq.com/")
            except Exception as e:
                last = e
                time.sleep(0.4 * (attempt + 1))
                continue
            cand = ((d.get("data") or {}).get(sym) or {})
            if cand.get("qfqday") or cand.get("day"):
                node = cand
                break
        if node:
            break
    if not node:
        raise RuntimeError(f"腾讯日线不可用: {code} ({last})")
    arr = node.get("qfqday") or node.get("day") or []
    bars = []
    for it in arr:
        if len(it) < 6 or it[0].replace("-", "") < beg:
            continue
        vol = float(it[5])                       # 手
        bars.append({"date": it[0], "open": float(it[1]), "close": float(it[2]),
                     "high": float(it[3]), "low": float(it[4]),
                     "vol": vol, "amount": float(it[2]) * vol * 100})
    nm = None
    try:
        nm = node["qt"][sym][1]
    except Exception:
        pass
    return bars, nm


def daily(code, beg="20250101"):
    try:
        bars, nm = daily_em(code, beg)
        if bars:
            return bars, nm
    except Exception:
        pass
    return daily_tx(code, beg)


def trends(code, ndays=2):
    d = jget(f"https://push2his.eastmoney.com/api/qt/stock/trends2/get?secid={secid(code)}"
             f"&ut={TOKEN}&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
             f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&ndays={ndays}&iscr=0").get("data")
    out = []
    for t in (d or {}).get("trends", []):
        p = t.split(",")
        out.append({"dt": p[0], "open": float(p[1]), "close": float(p[2]),
                    "high": float(p[3]), "low": float(p[4]),
                    "vol": float(p[5]), "amount": float(p[6])})
    return out


def fflow(code):
    """当日分钟级资金流，返回最新累计值（元）。连接器快照优先：东财这份实测滞后 15 分钟。"""
    d = ((MX["data"] or {}).get("flows") or {}).get(code)
    if d:
        return d
    d = jget(f"https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?secid={secid(code)}"
             f"&ut={TOKEN}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&klt=1&lmt=0")
    ks = (d.get("data") or {}).get("klines") or []
    if not ks:
        return None
    p = ks[-1].split(",")
    return {"time": p[0][-5:], "main": float(p[1]), "small": float(p[2]),
            "mid": float(p[3]), "big": float(p[4]), "huge": float(p[5])}


def fflow_lag(fl):
    """东财资金流末端时间与现在相差多少分钟（东财缓存实测会滞后十几分钟，须明示）"""
    if not fl or not fl.get("time"):
        return None
    try:
        h, m = fl["time"].split(":")
        now = datetime.now()
        return max(0, int((now - now.replace(hour=int(h), minute=int(m), second=0,
                                             microsecond=0)).total_seconds() // 60))
    except Exception:
        return None


# ---------------------------------------------------------------- 大盘资金与国家队
# 载具名单口径：2025 年报「前十大持有人」最后一次实名披露（2026 年起停止披露，无法再更新）
#   NATIONAL     主池 20 只 —— 年报确认汇金/证金持仓
#   NATIONAL_EXT 观察池 4 只 —— 年报未见持仓，但同族主力，一并跟踪趋势
# 注：科创50 的汇金持仓在 588080（易方达），588000（华夏）年报未见，故后者移入观察池。
NATIONAL = [
    ("sh510300", "沪深300"), ("sh510310", "沪深300"), ("sh510330", "沪深300"),
    ("sz159919", "沪深300"), ("sh510050", "上证50"),
    ("sh510500", "中证500"), ("sz159922", "中证500"), ("sh512500", "中证500"),
    ("sh512100", "中证1000"), ("sz159845", "中证1000"), ("sh560010", "中证1000"),
    ("sz159629", "中证1000"), ("sh588080", "科创50"), ("sh588050", "科创50"),
    ("sz159915", "创业板"), ("sz159952", "创业板"),
    ("sh510180", "上证180"), ("sh515800", "中证800"),
    ("sz159901", "深证100"), ("sh510230", "金融"),
]
NATIONAL_EXT = [
    ("sh588000", "科创50"), ("sz159338", "中证A500"),
    ("sh512050", "中证A500"), ("sz159339", "中证A500"),
]
NATIONAL_ALL = [c for c, _ in NATIONAL] + [c for c, _ in NATIONAL_EXT]
FAM_ORDER = ["沪深300", "上证50", "中证500", "中证1000", "科创50", "创业板",
             "上证180", "中证800", "深证100", "金融"]

# 日内成交额累计分布（A 股 U 型节奏），把「今日累计成交额」折到可比的进度基准。
# 校准点：14:33 ≈ 82.4%（与公开同类跟踪工具一致）；同时刻线性时间进度是 88.8%，两者差 6 个点。
INTRADAY_CURVE = [(570, 0.00), (600, 0.22), (630, 0.34), (660, 0.45),
                  (690, 0.55), (780, 0.55), (810, 0.65), (840, 0.73),
                  (870, 0.81), (885, 0.88), (900, 1.00)]


def intraday_progress(dt=None):
    """当前时点历史平均已走完全天成交额的比例（0~1）。午休 11:30-13:00 恒为 0.55。"""
    dt = dt or datetime.now()
    cur = dt.hour * 60 + dt.minute
    if cur <= INTRADAY_CURVE[0][0]:
        return 0.0
    if cur >= INTRADAY_CURVE[-1][0]:
        return 1.0
    for i in range(1, len(INTRADAY_CURVE)):
        t0, p0 = INTRADAY_CURVE[i - 1]
        t1, p1 = INTRADAY_CURVE[i]
        if t0 <= cur <= t1:
            return p0 + (p1 - p0) * (cur - t0) / (t1 - t0) if t1 > t0 else p1
    return 1.0


BASE_CACHE = data("pool_baseline.json")


def pool_baseline(codes):
    """池内各只的 20 日均成交额。日更缓存，避免每次跑面板都拉 24 次日线。"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(BASE_CACHE, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("date") == today and c.get("base"):
            return c["base"]
    except Exception:
        pass
    base = {}
    for c in codes:
        code = c[2:] if c[:2].lower() in ("sh", "sz") else c
        try:
            bars, _ = daily(code)
        except Exception:
            continue
        if not bars:
            continue
        hist = [b["amount"] for b in bars[-21:-1] if b.get("amount")]
        if hist:
            base[c] = sum(hist) / len(hist)
    if base:
        try:
            os.makedirs(os.path.dirname(BASE_CACHE), exist_ok=True)
            with open(BASE_CACHE, "w", encoding="utf-8") as fp:
                json.dump({"date": today, "base": base}, fp, ensure_ascii=False)
        except Exception:
            pass
    return base


def market_block(no, cfg=None, mode="noon"):
    """大盘资金流向 + 国家队载具动向。数据全部来自连接器快照，缺失就明说，不估算。
    行格式固定为「  标签<2+空格>数值」，report_gen 按标签解析。"""
    d = MX["data"] or {}
    ff = d.get("flows_full") or {}
    qs = d.get("quotes_full") or {}
    mk = d.get("market") or {}
    etf = d.get("etf") or {}

    print(f"\n{no}、大盘资金与国家队")
    if not ff and not mk and not etf:
        print("  无数据（连接器快照未含 market / etf / 指数资金流）")
        return

    idx = [ff.get(c) for c in ("sh000001", "sz399001") if ff.get(c)]
    if idx:
        main = sum(x["main"] for x in idx)
        huge = sum(x["huge"] for x in idx)
        big = sum(x["big"] for x in idx)
        mid = sum(x["mid"] for x in idx)
        small = sum(x["small"] for x in idx)
        amt = (mk.get("amount") or 0) * 1e8          # 概览给的是「亿」
        ratio = f"（占成交额 {main/amt*100:+.1f}%）" if amt else ""
        print(f"  两市主力        {wan(main)}{ratio}")
        print(f"  结构            超大单 {wan(huge)}　大单 {wan(big)}　"
              f"中单 {wan(mid)}　小单 {wan(small)}")
        print(f"  主力5日         {wan(sum(x.get('main5') or 0 for x in idx))}")
        print(f"  主力10日        {wan(sum(x.get('main10') or 0 for x in idx))}")
        print(f"  主力20日        {wan(sum(x.get('main20') or 0 for x in idx))}")
    else:
        print("  两市主力        无数据（指数资金流缺失）")

    if mk:
        print(f"  上涨/下跌       {mk.get('up') or '—'} / {mk.get('down') or '—'}")
        print(f"  涨停/跌停       {mk.get('limit_up') or '—'} / {mk.get('limit_dn') or '—'}")
        r = mk.get("amount_ratio20")
        amt = mk.get("amount") or 0
        tail = f"，20日均量 {r:.0f}%" if r else ""
        print(f"  成交额          {amt:,.0f}亿（{mk.get('date') or '—'} 收盘{tail}）")
        note = ("（收盘口径；日期若为昨日说明概览未更新，以「A股收盘」的指数成交额为准）"
                if mode == "evening" else "（收盘口径，非当日盘中）")
        print(f"  概览统计日      {mk.get('date') or '—'}{note}")

    # 同时点成交量倍数：国家队进场的必要不充分信号（真进场必经放量）。
    # 午盘按日内进度折算；晚报是收盘复盘，「全天」就按 100% 算 —— 不读时钟，
    # 否则 15:30 之后重跑、或补跑历史日期时会算出「已走完 100% 却只统计了半日」的假倍数。
    prog = 1.0 if mode == "evening" else intraday_progress()
    if mode in ("noon", "evening") and prog > 0.02 and qs:
        base = pool_baseline(NATIONAL_ALL)
        today = a20 = 0.0
        nq = 0
        for c in NATIONAL_ALL:
            q = qs.get(c)
            if not q or not q.get("amount") or not base.get(c):
                continue
            today += q["amount"]
            a20 += base[c]
            nq += 1
        if nq and a20 > 0:
            bench = a20 * prog
            x = today / bench
            tip = ("未见进场迹象" if x < 0.85 else
                   "量能持平" if x < 1.2 else
                   "明显放量" if x < 1.8 else "异常放量，重点看资金方向")
            span = "全天已走完" if mode == "evening" else "历史同时点已走完"
            print(f"  载具同时点      {x:.2f}×（今日 {today/1e8:,.1f}亿 / 基准 {bench/1e8:,.1f}亿，"
                  f"{span} {prog*100:.1f}%）→ {tip}")
        else:
            print(f"  载具同时点      无数据（{nq} 只有效）")

    nats = [(c, fam, ff[c], etf.get(c)) for c, fam in NATIONAL if ff.get(c) or etf.get(c)]
    exts = [(c, fam, ff[c], etf.get(c)) for c, fam in NATIONAL_EXT if ff.get(c) or etf.get(c)]
    if nats:
        m1 = sum((x[2] or {}).get("main", 0) for x in nats)
        m5 = sum((x[2] or {}).get("main5", 0) for x in nats)
        size = sum((x[3] or {}).get("size") or 0 for x in nats)
        inflow = [x for x in nats if (x[2] or {}).get("main", 0) > 0]
        print(f"  载具今日        {wan(m1)}（{len(nats)} 只，{len(inflow)} 只净流入）")
        print(f"  载具5日         {wan(m5)}")
        print(f"  载具规模        {size/1e8:,.0f}亿")
        if exts:
            em = sum((x[2] or {}).get("main", 0) for x in exts)
            esize = sum((x[3] or {}).get("size") or 0 for x in exts)
            ein = len([x for x in exts if (x[2] or {}).get("main", 0) > 0])
            print(f"  载具观察        {wan(em)}（{len(exts)} 只，{ein} 只净流入"
                  + (f"；规模 {esize/1e8:,.0f}亿" if esize else "") + "）")
        fam_sum = {}
        for c, fam, fl, et in nats:
            fam_sum[fam] = fam_sum.get(fam, 0) + ((fl or {}).get("main", 0))
        print("  载具分族        " + "｜".join(
            f"{f} {wan(fam_sum[f])}" for f in FAM_ORDER if f in fam_sum))
        print("  载具明细        " + "｜".join(
            f"{c[2:] if c.startswith(('sh', 'sz')) else c} {wan((fl or {}).get('main', 0))}"
            for c, fam, fl, et in nats + exts))
        print(f"  载具说明        主力净额是场内交易口径，不等于申购份额；"
              f"份额变化看 K01（etf_flow.py），两者背离时以份额为准")

    # K01 份额指数（etf_flow.py 每日写一份汇总，缺失就跳过）
    try:
        with open(data("k01_index.json"), encoding="utf-8") as f:
            k01 = json.load(f)
        ch = k01.get("chg_shares")
        print(f"  载具份额        {k01.get('date')} 主池 {k01.get('pool')} 只合计 "
              f"{(k01.get('total_shares') or 0)/1e8:,.1f}亿份"
              + (f"，较上一记录 {ch/1e8:+.2f}亿份" if ch not in (None, 0) else "（区间首日，无前值）")
              + f"｜加仓指数 {k01.get('index', 100):.1f}")
        if k01.get("note"):
            print(f"  份额口径        {k01['note']}")
    except Exception:
        pass


# ---------------------------------------------------------------- 融资融券（两融）
# 两融是杠杆资金的仓位表：融资余额升 = 有人借钱买，降 = 去杠杆。
# 它比资金流慢（T+1 披露），但能回答资金流答不了的问题——「这波是谁加的杠杆、加了多少」。
MARGIN_MK_CACHE = data("margin_market.json")
MARGIN_ETF_CACHE = data("margin_etf.json")


def dcget(report, **kw):
    """东财数据中心（datacenter-web）查询。

    与 push2 行情接口不同主机，因此单独取数、单独重试，**不参与 EM_FAIL / EM_PREF 的行情降级判断**：
    两者共用会互相污染——push2 挂掉不代表数据中心挂掉，反之亦然。
    """
    q = {"reportName": report, "columns": "ALL", "source": "WEB", "client": "WEB"}
    q.update({k: v for k, v in kw.items() if v is not None})
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(q)
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                      "Referer": "https://data.eastmoney.com/"})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            return (d.get("result") or {}).get("data") or []
        except Exception as e:
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"datacenter 取数失败 {report}: {last}")


def margin_market(n=30):
    """沪深两市两融汇总（RPTA_RZRQ_LSHJ）。T+1 披露，最新一条恒为上一交易日。按天缓存。"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(MARGIN_MK_CACHE, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("fetched") == today and c.get("rows"):
            return c["rows"]
    except Exception:
        pass
    rows = []
    try:
        for x in dcget("RPTA_RZRQ_LSHJ", sortColumns="dim_date", sortTypes="-1",
                       pageSize=str(n), pageNumber="1"):
            rows.append({"date": (x.get("DIM_DATE") or "")[:10],
                         "rzye": x.get("RZYE"), "rzye_zb": x.get("RZYEZB"),
                         "rzmre": x.get("RZMRE"), "rzche": x.get("RZCHE"),
                         "rzjme": x.get("RZJME"), "rzjme5": x.get("RZJME5D"),
                         "rzjme10": x.get("RZJME10D"),
                         "rqye": x.get("RQYE"), "rzrqye": x.get("RZRQYE"),
                         "ltsz": x.get("LTSZ"), "idx": x.get("NEW"),
                         "zdf": x.get("ZDF"), "zdf5": x.get("ZDF5D"),
                         "zdf10": x.get("ZDF10D")})
    except Exception:
        rows = []
    rows.sort(key=lambda r: r["date"], reverse=True)
    if rows:
        try:
            os.makedirs(os.path.dirname(MARGIN_MK_CACHE), exist_ok=True)
            with open(MARGIN_MK_CACHE, "w", encoding="utf-8") as fp:
                json.dump({"fetched": today, "rows": rows}, fp, ensure_ascii=False)
        except Exception:
            pass
    return rows


def margin_etf(codes):
    """持仓 ETF 的两融明细（RPTA_WEB_RZRQ_GGMX，按 SCODE 过滤）。

    只有两融标的才有记录，非标的直接缺席，**不补 0、不外推**——没有就是没有。
    """
    bare = [c[2:] if c[:2].lower() in ("sh", "sz") else c for c in codes]
    out = {}
    try:
        flt = "(SCODE in (" + ",".join(f'"{b}"' for b in bare) + "))"
        for x in dcget("RPTA_WEB_RZRQ_GGMX", sortColumns="DATE", sortTypes="-1",
                       pageSize=str(max(60, len(bare) * 12)), pageNumber="1", filter=flt):
            code = (x.get("SCODE") or "").strip()
            d = (x.get("DATE") or "")[:10]
            if not code or (code in out and out[code]["date"] >= d):
                continue
            out[code] = {"date": d, "name": x.get("SECNAME"),
                         "rzye": x.get("RZYE"), "rzjme": x.get("RZJME"),
                         "rzjme5": x.get("RZJME5D"), "rzjme10": x.get("RZJME10D"),
                         "rqye": x.get("RQYE"), "rzrqye": x.get("RZRQYE")}
    except Exception:
        out = {}
    if out:
        try:
            os.makedirs(os.path.dirname(MARGIN_ETF_CACHE), exist_ok=True)
            with open(MARGIN_ETF_CACHE, "w", encoding="utf-8") as fp:
                json.dump({"fetched": datetime.now().strftime("%Y-%m-%d"), "data": out},
                          fp, ensure_ascii=False)
        except Exception:
            pass
        return out
    try:              # 取数失败回落缓存：两融本身 T+1，旧一天不影响方向判断
        with open(MARGIN_ETF_CACHE, encoding="utf-8") as f:
            return json.load(f).get("data") or {}
    except Exception:
        return {}


def margin_block(no, cfg, mode="noon"):
    """融资融券段：回答「这批钱是不是借来的」。资金流只看买卖方向，看不出杠杆成分。"""
    print(f"\n{no}、融资融券（两融杠杆）")
    rows = margin_market()
    if not rows:
        print("  无数据（东财数据中心取数失败；两融为 T+1 披露，次日再看）")
        return
    cur = rows[0]

    def _yi(v, d=0):
        return "—" if v is None else f"{v/1e8:,.{d}f}亿"

    def _chg(v):
        return "—" if v is None else f"{v/1e8:+,.0f}亿"

    rz = cur.get("rzye")
    print(f"  两融余额        {_yi(cur.get('rzrqye'))}（{cur['date']}，T+1 披露）")
    print(f"  融资余额        {_yi(rz)}｜日变化 {_chg(cur.get('rzjme'))}"
          f"｜5日 {_chg(cur.get('rzjme5'))}｜10日 {_chg(cur.get('rzjme10'))}")
    if cur.get("rzye_zb"):
        print(f"  融资余额占比    {cur['rzye_zb']:.2f}%（占 A 股流通市值，衡量杠杆拥挤度）")
    buy, sell = cur.get("rzmre"), cur.get("rzche")
    if buy and sell:
        print(f"  融资买入/偿还   {_yi(buy)} / {_yi(sell)}"
              f"（{'偿还多于买入，杠杆净收缩' if sell > buy else '买入多于偿还，杠杆净加仓'}）")
    if cur.get("rqye") and rz:
        print(f"  融券余额        {_yi(cur['rqye'])}（为融资余额的 {cur['rqye']/rz*100:.1f}%，"
              f"做空意愿的代理，规模小则方向信号弱）")
    if cur.get("idx"):
        print(f"  指数同步        {cur['idx']:,.2f} {(cur.get('zdf') or 0):+.2f}%"
              f"｜5日 {(cur.get('zdf5') or 0):+.2f}%｜10日 {(cur.get('zdf10') or 0):+.2f}%")
    m5, m10 = cur.get("rzjme5") or 0, cur.get("rzjme10") or 0
    if m5 > 0 and m10 > 0:
        d_ = "5日与10日同向净买入，杠杆资金在持续加仓"
    elif m5 < 0 and m10 < 0:
        d_ = "5日与10日同向净卖出，杠杆资金在持续撤离"
    elif m5 > 0 > m10:
        d_ = "5日回补、10日仍在撤，短期反弹不改中期去杠杆"
    else:
        d_ = "5日继续撤、10日仍为正，杠杆正在从高位回落"
    print(f"  杠杆方向        {d_}")

    me = margin_etf([h["code"] for h in cfg["holdings"]])
    if me:
        segs = []
        for h in cfg["holdings"]:
            c = h["code"][2:] if h["code"][:2].lower() in ("sh", "sz") else h["code"]
            x = me.get(c)
            if not x or not x.get("rzye"):
                continue
            segs.append(f"{c} {x['rzye']/1e8:.2f}亿(净买{(x.get('rzjme') or 0)/1e4:+.0f}万"
                        f"/5日{(x.get('rzjme5') or 0)/1e4:+.0f}万)")
        print("  持仓两融        " + ("｜".join(segs) if segs else "无记录（均非两融标的）"))
        md = sorted({v.get("date") for v in me.values() if v.get("date")})
        print(f"  两融数据日      {md[-1] if md else '—'}（T+1，比报价慢一天，只能用来看趋势）")
    print("  两融口径        融资余额升=借钱加仓、降=去杠杆；只覆盖两融标的，非标的缺席不补值；"
          "与资金流背离时以资金流看当下、以两融看仓位")


# ---------------------------------------------------------------- 做T 战果
# 成本下降有两种来源，必须分开记账，否则「加仓摊薄」会被误当成「做T赚钱」：
#   ① 做T 差价：同一标的低买高卖实现的钱 → 计入 realized
#   ② 加仓摊薄：低位补仓把均价拉低（份额变多、总成本变高）→ 不计收益，标 `diluted`
TLEDGER = data("t_ledger.json")


def tledger():
    try:
        with open(TLEDGER, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": []}


def tstats_block(no, cfg, mode="noon"):
    """做T 战果与降本进度：已实现收益、往返次数、逐只降本、一次 T 能降多少。"""
    print(f"\n{no}、做T 战果与降本进度")
    ld = tledger()
    items = {x.get("code"): x for x in (ld.get("items") or [])}
    if not items:
        print("  无数据（data/t_ledger.json 缺失或为空）")
        return
    ta = (cfg.get("cash") or {}).get("total_assets") or 0
    realized = sum((x.get("realized") or 0) for x in items.values())
    print(f"  已实现收益      {realized:+,.2f} 元"
          + (f"（占总资产 {realized/ta*100:.2f}%）" if ta else ""))
    parts = [f"{c} {(x.get('realized') or 0):+,.2f}" for c, x in items.items() if x.get("realized")]
    if parts:
        print(f"  收益构成        {'｜'.join(parts)}")
    rounds = sum(x.get("rounds") or 0 for x in items.values())
    opens = [f"{c} {x['open_note']}" for c, x in items.items() if x.get("open_note")]
    print(f"  T往返           完成 {rounds} 次｜未结 {len(opens)} 笔"
          + (f"（{'；'.join(opens)}）" if opens else ""))

    # 逐只降本：ledger 的 cost_orig 对比 portfolio 的当前成本，价格用实时价
    print("  逐只降本        （原始→现在，负值＝成本已降；※＝加仓摊薄，不是T赚的）")
    rows = []
    for h in cfg["holdings"]:
        it = items.get(h["code"])
        co = (it or {}).get("cost_orig")
        if not co:
            continue
        try:
            q = quote(h["code"])
            px = q["price"] or q["prev"]
        except Exception:
            px = None
        cur = h.get("cost") or 0
        d = (cur / co - 1) * 100 if co else 0
        tag = "※加仓摊薄非T收益" if (it or {}).get("diluted") else ""
        tail = f"距成本 {(px/cur-1)*100:+.1f}%" if (px and cur) else "—"
        rows.append((h, co, cur, d, tag, tail, px))
        print(f"                  {h['code']}  {h['name']:<8} {co:.3f}→{cur:.3f}（{d:+.1f}%）{tag}｜{tail}")

    # 降本效率：只算浮亏最深的那只——它才是「靠做T 磨回本」的真实成本
    loss = [r for r in rows if r[6] and r[2] > r[6]]
    if loss:
        h, co, cur, d, tag, tail, px = min(loss, key=lambda r: r[6] / r[2])
        cap = int(min(15000, h["shares"] * 0.3) // 100 * 100)
        gain = 0.015 * px * cap                     # 一次 1.5% 价差的倒T 毛利（未扣佣金）
        drop = gain / h["shares"] if h["shares"] else 0
        need = (px * 0.01 / drop) if drop else 0
        print(f"  降本效率        {h['code']} 一次 1.5% 倒T（上限 {cap:,} 份）≈ {gain:+.0f} 元 "
              f"≈ 成本降 {drop:.4f}｜回本涨幅每降 1 个百分点需 {need:.1f} 次成功 T")
    print("  做T口径        T 收益只算同一标的的买卖差价；加仓摊薄的成本变化不计入，"
          "避免把加仓记成降本；往返次数只算「买回+卖出」都完成的")


# ---------------------------------------------------------------- 实时快讯
# 东财 / 新浪 7x24 直播流：只回答「现在正在发生什么」，不做解读、不做预测。
# 注意：这两个接口是固定域名，**不能走 _get() 的东财镜像轮换**（会被换成 push2 而必然失败）。
NEWS_KW2 = ("ETF", "汇金", "国家队", "证金", "降准", "降息", "逆回购", "MLF", "LPR",
            "两融", "北向", "南向", "两市", "成交额", "主力", "净流入", "净流出",
            "医药", "创新药", "医疗", "医保", "白酒", "消费", "红利",
            "标普", "纳指", "港股", "美联储", "CPI", "PPI", "PMI", "国债",
            "证监会", "央行", "国常会", "政治局", "财政部")
NEWS_KW1 = ("银行", "保险", "美股", "出口", "关税", "汇率", "人民币",
            "A股", "沪指", "创业板", "科创", "融资", "降费")


def news_score(t):
    """2 分=强相关（资金/政策/持仓主题），1 分=弱相关（泛财经）。≥2 才进「相关」车道，
    这样能挡掉「XX银行上调某消费品牌目标价」这类字面命中、实际无关的公司/机构快评。"""
    return (sum(2 for k in NEWS_KW2 if k in t)
            + sum(1 for k in NEWS_KW1 if k in t))


def plain_get(url, ref="", decode="utf-8", tries=2):
    """固定域名直连（newsapi / zhibo 这类接口），不做主机轮换、不计入 EM_FAIL。"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ref})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read().decode(decode, "ignore")
        except Exception as e:
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"取数失败 {url[:60]} ({last})")


def _news_title(s):
    """去掉【标题】包裹、压掉换行，超长截断——手机上超过 46 字就不是快讯了"""
    s = re.sub(r"^【([^】]{2,60})】.*$", r"\1", (s or "").strip())
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:46] + "…") if len(s) > 46 else s


def news_feed(hours=6, max_pages=3):
    """东财 + 新浪 7x24 合并去重，只留最近 hours 小时，按时间倒序。"""
    cutoff = datetime.now() - timedelta(hours=hours)
    items = []

    def add(tm, title, src, url):
        try:
            dt = datetime.strptime((tm or "")[:16], "%Y-%m-%d %H:%M")
        except Exception:
            return
        if dt < cutoff:
            return
        tt = _news_title(title)
        if len(tt) < 6:
            return
        items.append({"dt": dt, "time": dt.strftime("%m-%d %H:%M"),
                      "title": tt, "src": src, "url": url})

    for page in range(1, max_pages + 1):        # 东财：翻页到窗口边界为止
        try:
            txt = plain_get(f"https://newsapi.eastmoney.com/kuaixun/v1/"
                            f"getlist_102_ajaxResult_50_{page}_.html",
                            ref="https://kuaixun.eastmoney.com/")
            lst = (json.loads(txt.split("=", 1)[1].rstrip(";\r\n ")).get("LivesList") or [])
        except Exception:
            break
        if not lst:
            break
        for x in lst:
            add(x.get("showtime") or x.get("ordertime"),
                x.get("title") or x.get("digest"), "东财",
                x.get("url_m") or x.get("url_w") or "")
        try:
            oldest = datetime.strptime((lst[-1].get("showtime") or "")[:16], "%Y-%m-%d %H:%M")
            if oldest < cutoff:
                break
        except Exception:
            break

    try:                                        # 新浪 7x24（口径略不同，作补充）
        d = json.loads(plain_get("https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=50"
                                 "&zhibo_id=152&tag_id=0&dire=f&dpc=1",
                                 ref="https://finance.sina.com.cn/7x24/"))
        for x in ((d.get("result") or {}).get("data") or {}).get("feed", {}).get("list", []):
            add(x.get("create_time"), x.get("rich_text"), "新浪", x.get("docurl") or "")
    except Exception:
        pass

    seen, uniq = set(), []
    for it in items:
        k = re.sub(r"[\s，。！？、：；【】「」（）()\"'%·]", "", it["title"])[:18]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    uniq.sort(key=lambda x: x["dt"], reverse=True)
    return uniq


def news_block(no, cfg=None, mode="noon"):
    sub = "今日要闻，便于复盘" if mode == "evening" else "近期发生的事，便于即时判断"
    print(f"\n{no}、实时快讯（{sub}）")
    hours = 14 if mode == "morning" else 12 if mode == "evening" else 6
    items = news_feed(hours=hours)
    if not items:
        print("  无数据（东财 / 新浪 7x24 均不可用，或窗口内无更新）")
        return
    # 同一关键词最多 3 条：挡掉「5 家药企同一天公告医保目录」这类同质刷屏
    hit, kwcnt = [], {}
    for x in items:
        ks = [k for k in NEWS_KW2 if k in x["title"]]
        if not ks or news_score(x["title"]) < 2:
            continue
        if kwcnt.get(ks[0], 0) >= 3:
            continue
        kwcnt[ks[0]] = kwcnt.get(ks[0], 0) + 1
        hit.append(x)
        if len(hit) >= 8:
            break
    rest = [x for x in items if x not in hit]
    print(f"  更新时间        {datetime.now():%H:%M}（近 {hours} 小时，去重后 {len(items)} 条，"
          f"相关 {len(hit)} 条）")
    for x in hit[:8]:
        print(f"  {x['time']}  相关  {x['title']}｜{x['src']}")
    for x in rest[:4]:
        print(f"  {x['time']}  快讯  {x['title']}｜{x['src']}")
    print("  快讯口径        东财 + 新浪 7x24 合并去重，只给事实不解读；"
          "与资金流/两融方向矛盾时以数据为准，消息只当情绪参考")

# ---------------------------------------------------------------- KDAS
def auto_anchors(bars, n=5, win=10, max_back=250):
    """自动挑关键日：阶段高低点，保证间隔，尽量铺开时间跨度"""
    seq = bars[-max_back:]
    cand = []
    for i in range(win, len(seq) - 1):
        seg = seq[i - win:i + win + 1]
        b = seq[i]
        if b["high"] >= max(x["high"] for x in seg):
            cand.append((i, "阶段高"))
        elif b["low"] <= min(x["low"] for x in seg):
            cand.append((i, "阶段低"))
    if len(cand) < n:
        step = max(1, len(seq) // (n + 1))
        cand = [(len(seq) - 1 - step * k, "回看") for k in range(n, 0, -1)]
    picked, last = [], None
    for i, why in sorted(cand, reverse=True):
        if last is not None and last - i < 15:      # 至少隔 15 根，避免线挤成一团
            continue
        picked.append((i, why))
        last = i
        if len(picked) == n:
            break
    picked = sorted(picked)
    return [(seq[i]["date"], why) for i, why in picked]


def kdas(bars, anchors, asof=None):
    dates = [b["date"] for b in bars]
    i1 = dates.index(asof) if asof else len(bars) - 1
    out = []
    for a in anchors:
        if a not in dates:
            out.append((a, None)); continue
        i0 = dates.index(a) + 1          # 关键：排除锚点日，与 SUM(X,BARSLAST) 一致
        if i0 > i1:
            out.append((a, None)); continue
        seg = bars[i0:i1 + 1]
        vol = sum(b["vol"] for b in seg)
        amt = sum(b["amount"] for b in seg)
        out.append((a, amt / (vol * 100) if vol else None))
    return out


def kdas_relay(bars, anchors, lines, price):
    """接力模式与强弱分界线（readme §5.3）。

    KDAS 图上永远是 5 条线，接力 = 把锚点挪到新的关键日，所以**最新锚点画出的
    那条线就是接力链尾 = 强弱分界线**（全体系唯一状态开关）。
    不用逐日模拟穿越：锚点落在当日时线值恒等于当日均价，会造出假买点。
    接力方向看相邻两条线是抬高还是降低——支撑不断抬高=向上接力。"""
    valid = [(a, v) for a, v in lines if v]
    if not valid:
        return {"chain": [], "line": None, "dir": None, "anchor": None,
                "bars_since": None, "n_touch": 0}
    a_now, v_now = valid[-1]
    v_prev = valid[-2][1] if len(valid) >= 2 else None
    dates = [b["date"] for b in bars]
    since = (len(bars) - 1 - dates.index(a_now)) if a_now in dates else None
    return {"chain": [(a, v) for a, v in valid],
            "line": v_now,
            "dir": ("up" if v_now >= v_prev else "down") if v_prev else None,
            "anchor": a_now, "bars_since": since,
            "n_touch": len(valid),
            "above": sum(1 for _, v in valid if v <= price)}


def kdas_state(lv, relay):
    """六状态（readme §4.2）。唯一状态开关是「站不站得上强弱分界线」。
    判据只用价格与 KDAS 线的关系，不掺其他指标。"""
    px, res = lv["price"], lv["res"]
    wr = relay.get("line")
    if not wr:
        return "未知", "无接力线可判"
    d_wr = (px / wr - 1) * 100
    d_res = ((res[1] / px - 1) * 100) if res else None
    if px <= wr:
        # 站不上强弱分界线 = 左半场，差一点点也是没站上（§5.3）
        if d_wr > -1.5:
            return "自然寻底", f"贴近强弱线 {wr:.3f}（{d_wr:+.1f}%），站上即转右侧买点"
        return "自然寻底", f"强弱线 {wr:.3f} 在头上 {d_wr:+.1f}%"
    if relay.get("dir") == "down" and d_wr < 3:
        return "右侧买点", f"刚站上向下接力的强弱线 {wr:.3f}（{d_wr:+.1f}%）"
    if d_res is None:
        return "右侧趋势", "上方已无 KDAS 压力位"
    if d_res < 3:
        # §4.3：右侧空间很小时两三根阳线就走完，此时是卖点不是买点
        return "右侧空间·窄", f"上方仅 {d_res:.1f}% 就撞压力 {res[1]:.3f}，此处是减仓区不是买点"
    if d_res >= 8:
        return "右侧空间", f"强弱线 {wr:.3f} 之上、压力 {res[1]:.3f} 还有 {d_res:.1f}% 空间"
    return "震荡区间", f"强弱线 {wr:.3f} 与压力 {res[1]:.3f} 之间 {d_res:.1f}% 空间"


def levels(code, px=None):
    bars, nm = daily(code)
    if not bars:
        return None
    anchors = [a for a, _ in auto_anchors(bars)]
    lines = kdas(bars, anchors)
    # px 传实时价：盘中日线末根常滞后（东财当天不更新），用昨收判状态会整整慢一拍
    price = px or bars[-1]["close"]
    below = [(a, v) for a, v in lines if v and v <= price]
    above = [(a, v) for a, v in lines if v and v > price]
    sup = max(below, key=lambda x: x[1]) if below else None
    res = min(above, key=lambda x: x[1]) if above else None
    # 没有对应线时用 20 日高低兜底；但兜底值必须在正确一侧，否则视为「创新高/新低」
    hist = bars[-21:-1] if len(bars) > 21 else bars[:-1]
    if sup is None:
        lo = min(b["low"] for b in hist)
        sup = ("20日低", lo) if lo < price else None
    if res is None:
        hi = max(b["high"] for b in hist)
        res = ("20日高", hi) if hi > price else None
    # 近 5 日平均振幅，用来判断这个标的还值不值得做T
    amps = [(b["high"] - b["low"]) / b["close"] * 100 for b in bars[-6:-1]]
    amp5 = sum(amps) / len(amps) if amps else None
    relay = kdas_relay(bars, anchors, lines, price)
    out = {"name": nm, "price": price, "date": bars[-1]["date"], "lines": lines,
           "sup": sup, "res": res, "amp5": amp5, "relay": relay,
           "all_below": len(above) == 0, "all_above": len(below) == 0}
    out["state"], out["state_why"] = kdas_state(out, relay)
    return out


# ---------------------------------------------------------------- 输出工具
def pct(x, d=2):
    return "—" if x is None else f"{x:+.{d}f}%"


def wan(x):
    if x is None:
        return "—"
    a = abs(x)
    return f"{x/1e8:+.2f}亿" if a >= 1e8 else f"{x/1e4:+.0f}万"


def bar(x, lo, hi, w=10):
    if x is None:
        return ""
    r = max(0.0, min(1.0, (x - lo) / (hi - lo) if hi > lo else 0.5))
    n = int(round(r * w))
    return "▁" * n + "█" + "▁" * (w - n)


# ---------------------------------------------------------------- 交易日检查
def day_check(cfg):
    now = datetime.now()
    if now.weekday() >= 5:
        print(f"⚠ 今天是{'周六' if now.weekday()==5 else '周日'}，A股休市，以下为上一交易日数据。\n")
        return
    try:
        bars, _ = daily(cfg["holdings"][0]["code"])
        if bars:
            d = datetime.strptime(bars[-1]["date"], "%Y-%m-%d")
            if (now - d).days > 4:
                print(f"⚠ 最新K线为 {bars[-1]['date']}，今天可能休市，先确认交易日。\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 数据源小结
XCNT = [0]


def src_label():
    if MX["data"]:
        return "腾讯自选股连接器快照（westock-mcp）"
    return {"auto": "腾讯自选股行情 + 东财交叉校验",
            "tx": "腾讯自选股行情（无校验）",
            "em": "东财公开接口（无校验）"}[SRC[0]]


def print_xcheck(cfg):
    """放在报告最后：不用 · 开头，避免被 report_gen 当成持仓重点解析"""
    n = len(cfg["holdings"])
    names = {h["code"]: h["name"] for h in cfg["holdings"]}
    print("\n附：数据源校验（腾讯自选股 vs 东财）")
    print(f"  报价取数：连接器 {SRC_STAT['mx']}/{n} 只、腾讯 {SRC_STAT['tx']}/{n} 只"
          f"、东财 {SRC_STAT['em']}/{n} 只、新浪兜底 {SRC_STAT.get('sina', 0)} 只"
          f"（模式 {SRC[0]}）；逐只比对 {XCNT[0]} 项")
    if MX["data"]:
        print(f"  连接器快照：{len(MX['data'].get('quotes') or {})} 只报价 / "
              f"{len(MX['data'].get('flows') or {})} 只资金流，{MX['age']:.0f} 分钟前写入")
    elif MX["age"] is not None:
        print(f"  ⚠ 连接器快照已过期 {MX['age']:.0f} 分钟（TTL {MX_TTL}），已改用实时接口")
    if SRC_STAT["mx"]:
        print("  （连接器快照单源，未做交叉校验）")
    elif SRC[0] == "em":
        print("  （东财单源模式，未做交叉校验）")
    elif TX_FAIL[0] >= 3:
        print("  ⚠ 腾讯接口连续失败，已整体回落东财，本次无交叉校验")
    elif XSTALE and not XCNT[0]:
        print(f"  ⚠ 东财 {len(XSTALE)} 只全部返回陈旧快照（最长滞后 "
              f"{max(s for _, s in XSTALE):.0f} 秒），差值不作数，已跳过比对")
    elif not XCNT[0]:
        print("  ⚠ 无可比价位（盘前无价且昨收缺失），本次未做交叉校验")
    elif XSTALE:
        print(f"  ⚠ 其中 {len(XSTALE)} 只东财快照滞后（最长 "
              f"{max(s for _, s in XSTALE):.0f} 秒），未参与比对")
    if XLOG:
        for code, label, a, b, d in XLOG[:8]:
            print(f"  ⚠ {names.get(code, code)} {code} {label}：{a:.3f} vs {b:.3f}"
                  f"，差 {d:.2f}% —— 取腾讯值，两源不一致，下单前用券商报价复核")
    elif XCNT[0]:
        print("  现价与昨收逐只比对，价差均 <0.2%，两源一致")


# ---------------------------------------------------------------- 股债比价 K04
def ratio_block(no="八", cfg=None, mode="morning"):
    """K04 股债比价（沪深300全收益 ÷ 二级债基）。环境/估值模块，放在报告文末。
    下沿（≤1.0）是股票便宜的区域、可当扳机；上沿（≥1.3）只能当温度计（泡沫没上限）。
    数据与口径全部来自 indicators/equity_bond_ratio.py / knowledge K04，不在这里重算。"""
    print()
    print(f"{no}、股债比价（K04 沪深300全收益 ÷ 二级债基）")
    try:
        from indicators import equity_bond_ratio as ebr
        o = ebr.build(ebr.load_anchor(), tries=2)  # 报告链路只重试 2 次，避免拖慢定时任务
        o["bond_temp"] = ebr.bond_temp()
    except Exception as e:
        print(f"  取数失败：{e}（本指标不可用，按无信号处理）")
        return
    try:
        ebr.to_csv(o)
    except Exception:
        pass

    live = o["live"]
    r = o["ratio_live"] if live else o["ratio"]
    px = o["hs300_px_live"] if live else o["hs300_px"]
    tr = o["hs300_tr_live"] if live else o["hs300_tr"]
    b = o["bond_live"] if live else o["bond"]
    p100 = o["p100_live"] if live else o["p100"]
    p097 = o["p097_live"] if live else o["p097"]
    p095 = o["p095_live"] if live else o["p095"]
    p130, p150 = o["p_upper"][1.30], o["p_upper"][1.50]
    frac = o["corridor"]["frac"]

    if r >= 1.50:
        hint = "股票贵过债，股票腿降到位、新钱进债或货币"
    elif r >= 1.30:
        hint = "偏贵，宽基不再新增、只减不加"
    elif r >= 1.05:
        hint = "两端都远，本指标不给信号，只做记录不动手"
    elif r >= 1.00:
        hint = "已到触线区上沿，第一笔不超过计划额 1/3"
    elif r >= 0.95:
        hint = "已跌破 1.00，分批推进，每批间隔 ≥10 日或再跌 3%"
    else:
        hint = "历史级区域，剩余弹药拿出来，先看 K01 份额是否同步抬升"

    print(f"  比价        {r:.3f}  档位 {o['band'][0]}")
    print(f"  现价        沪深300 {px:,.2f} ｜ 全收益 {tr:,.2f} ｜ 债腿 {b:,.1f}（代理）")
    g = lambda v: pct((v / px - 1) * 100)
    print(f"  下沿        1.00线 {p100:,.0f}（{g(p100)}）｜ "
          f"0.97线 {p097:,.0f}（{g(p097)}）｜ 0.95线 {p095:,.0f}（{g(p095)}）")
    print(f"  上沿        1.30线 {p130:,.0f}（{g(p130)}）｜ "
          f"1.50线 {p150:,.0f}（{g(p150)}）")
    print(f"  走廊        {frac * 100:.0f}%（0%=触线 {p100:,.0f}，100%=偏贵 {p150:,.0f}）")
    bt = " ｜ ".join(f"{t['code']} {t['frac'] * 100:.0f}%"
                     for t in (o.get("bond_temp") or []) if t["frac"] is not None)
    print(f"  债端        {bt or '无数据'}（国债ETF 52周分位，越高越贵）")
    print(f"  含义        {hint}")

    cfg = cfg or {}
    legs, others = [], 0
    for h in cfg.get("holdings", []):
        if h.get("role") != "宽基":
            others += 1
            continue
        try:
            q = quote(h["code"])
            e = q["price"] or q["prev"]
        except Exception:
            continue
        legs.append(f"{h['code']} {h['name']} 触线等比价 {e * (p100 / px):.3f}"
                    f"（{pct((p100 / px - 1) * 100, 1)}）现价 {e:.3f}")
    if legs:
        print(f"  持仓        {'；'.join(legs)}；其余 {others} 只非宽基，本指标不适用")
    else:
        print(f"  持仓        无宽基持仓（{others} 只均非宽基），本指标对当前组合无直接含义")

    if live:
        print(f"  口径        盘中估算，债腿当不动；收盘后以 indicators/equity_bond_ratio.py 重算为准")
    elif mode == "morning":
        print(f"  口径        上一交易日收盘口径（盘前无实时）；债腿=锚点+中证全债推进代理，误差约 ±1%")
    else:
        print(f"  口径        官网收盘口径；债腿=锚点+中证全债推进代理，误差约 ±1%")

    signal("K04", f"{r:.3f}", o["band"][0], px)   # 台账留痕（仅午盘落盘）


# ---------------------------------------------------------------- 早盘
def impulse_block(no, cfg=None, mode="morning"):
    """K05 信贷脉冲：月频指标，在日报里只当「背景板 + 否决位」，不参与日内择时。
    实际计算与口径全在 indicators/credit_impulse.py / knowledge K05，这里只做调用与失败兜底。"""
    try:
        from indicators import credit_impulse as ci
        s = ci.block(no, mode)
        if s:
            signal("K05", f"{s['p_all']:+.2f}", s["band"], s.get("px"))   # 台账留痕（仅午盘落盘）
    except Exception as e:
        print()
        print(f"{no}、信贷脉冲（K05 社融存量同比差分 · 月频）")
        print(f"  取数失败：{e}（本指标不可用，按无信号处理）")


# ---------------------------------------------------------------- 晚报（21:00 复盘）
# 与早/午盘的分工：晚报只回答「今天发生了什么」，不出做T动作、不给触发价。
# 关注顺序按用户口径：A股/港股收盘 → 全球市场 → 宽基ETF → 国家队与大单 → 两融 → 要闻。
# 行格式仍统一为「两空格 + 标签 + 两个以上空格 + 数值」，供 evening_gen.py 按标签解析。
EV_ASHARE = [("1.000001", "上证指数"), ("0.399001", "深证成指"),
             ("0.399006", "创业板指"), ("1.000688", "科创50")]
EV_ASIA = [("100.N225", "日经225"), ("100.KS11", "韩国KOSPI"), ("100.STI", "新加坡海峡")]
EV_EUROPE = [("100.FTSE", "英国富时"), ("100.GDAXI", "德国DAX"), ("100.FCHI", "法国CAC")]
EV_US = [("100.DJIA", "道琼斯"), ("100.NDX", "纳斯达克"), ("100.SPX", "标普500")]
EV_FUT = [("hf_CHA50CFD", "A50期货"), ("hf_ES", "标普期货"), ("hf_NQ", "纳指期货")]
EV_MACRO = [("100.UDI", "美元指数"), ("101.GC00Y", "COMEX黄金")]


def ev_row(name, px, p, tail="", ind=2):
    print((" " * ind) + f"{name:<10} {px:>11,.2f}  {pct(p):>8}   {tail}".rstrip())


def ev_index(sid, name, tail_amt=False, ind=2):
    """晚报指数行：取数失败只打一行提示，不抛错拖垮整段"""
    try:
        q = index_quote(sid, name)
    except Exception:
        q = {"ok": False}
    if not q.get("ok"):
        print((" " * ind) + f"{name:<10} 取数失败")
        return None
    tail = f"成交额 {q['amount']/1e8:,.0f} 亿" if (tail_amt and q.get("amount")) else ""
    ev_row(q.get("name") or name, q["price"], q.get("pct"), tail, ind)
    return q


def southbound():
    """南向资金（港股通）当日净买入，元。东财 kamt 接口，不可信就返回 None。

    口径陷阱：该接口同时承载「额度余额」，单位万元。当净买入绝对值贴近每日额度
    （沪/深各 420 亿 = 4200000 万）时，返回的其实是额度而非净买入，必须判为无效 ——
    实测盘中曾返回恒等于阈值的 4200000。宁可写「无数据」也不给错数。
    """
    try:
        d = jget("https://push2.eastmoney.com/api/qt/kamt/get"
                 "?fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56")
    except Exception:
        return None
    dd = d.get("data") or {}
    out = {}
    for k, nm in (("sh2hk", "沪港通"), ("sz2hk", "深港通")):
        x = dd.get(k) or {}
        v, th = x.get("dayNetAmtIn"), x.get("dayAmtThreshold")
        if not isinstance(v, (int, float)) or not th:
            return None
        if abs(v) >= th * 0.99:        # 触到额度上限 → 返回的是额度，不是净买入
            return None
        out[nm] = v * 1e4
    out["total"] = sum(out.values())
    out["date"] = (dd.get("sh2hk") or {}).get("date2") or ""
    return out


def ashare_block(no):
    """A股收盘：四大指数 + 两市成交额。指数口径收盘即准，比市场概览可靠。"""
    print(f"\n{no}、A股收盘（全天）")
    amts = []
    for sid, nm in EV_ASHARE:
        q = ev_index(sid, nm, tail_amt=True)
        if q and sid in ("1.000001", "0.399001") and q.get("amount"):
            amts.append(q["amount"])
    if len(amts) == 2:
        print(f"  两市成交额      {sum(amts)/1e8:,.0f} 亿"
              f"（沪 {amts[0]/1e8:,.0f} + 深 {amts[1]/1e8:,.0f}，指数口径）")
    else:
        print("  两市成交额      无数据（指数成交额缺失）")
    print("  成交额口径      创业板/科创50 是成分指数，成交额已含在深市/沪市内，不重复累加")


def hk_block(no):
    """港股收盘：三大指数 + 南向资金"""
    print(f"\n{no}、港股收盘")
    for sid, nm in [("100.HSI", "恒生指数"), ("124.HSTECH", "恒生科技"),
                    ("100.HSCEI", "国企指数")]:
        ev_index(sid, nm, tail_amt=(sid == "100.HSI"))
    sb = southbound()
    if sb:
        print(f"  南向资金        {wan(sb['total'])}（沪港通 {wan(sb['sh2hk'])} / "
              f"深港通 {wan(sb['sz2hk'])}，{sb.get('date') or '—'}）")
        print("  南向口径        港股通净买入，正=内地资金净流入港股；已剔除「额度当净买入」的错值")
    else:
        print("  南向资金        无数据（东财接口不可用，或返回值疑似额度而非净买入）")
    print("  港股口径        16:00 收盘；港股与 A 股不同步，对隔夜美股的反映常滞后一天")


def global_block(no):
    """全球市场：亚太收盘 / 欧洲收盘 / 美股昨夜 / 期货当前 / 汇率商品"""
    print(f"\n{no}、全球市场")
    print("  亚太收盘")
    for sid, nm in EV_ASIA:
        ev_index(sid, nm, ind=4)
    print("  欧洲收盘")
    for sid, nm in EV_EUROPE:
        ev_index(sid, nm, ind=4)
    print("  美股昨夜")
    for sid, nm in EV_US:
        ev_index(sid, nm, ind=4)
    print("  期货当前")
    try:
        s = sina([c for c, _ in EV_FUT])
    except Exception:
        s = {}
    for code, nm in EV_FUT:
        v = s.get(code.split("_", 1)[-1])
        if not v or len(v) < 9 or not v[0]:
            print(f"    {nm:<10} 取数失败")
            continue
        try:
            cur, prev = float(v[0]), float(v[7])
            ev_row(nm, cur, (cur / prev - 1) * 100 if prev else None, ind=4)
        except Exception:
            print(f"    {nm:<10} 取数失败")
    print("  汇率商品")
    for sid, nm in EV_MACRO:
        ev_index(sid, nm, ind=4)
    print("  时点说明        亚太/欧洲为当日收盘，美股为昨夜收盘（21:00 美股未开盘），"
          "期货为当前报价；看方向不看点位")


def ev_shares(codes):
    """从 data/etf_flow.csv 取「今天」的份额变化。没有今天的记录返回 {}，不猜不补 0。"""
    path = data("etf_flow.csv")
    today = datetime.now().strftime("%Y-%m-%d")
    out = {}
    try:
        import csv as _csv
        with open(path, encoding="utf-8-sig") as f:
            for r in _csv.DictReader(f):
                c = (r.get("code") or "").strip()
                if r.get("date") != today or not c:
                    continue
                out[c] = {"chg": float(r.get("chg_shares") or 0),
                          "flow": float(r.get("net_flow") or 0) * 1e8}
    except Exception:
        return {}
    return out


def ev_sht(v):
    """份额变化：小于 50 万份的波动显示为 0.00，避免出现「-0.00亿份」这种看着像错的数"""
    s = f"{v/1e8:+.2f}亿份"
    return "0.00亿份" if s in ("+0.00亿份", "-0.00亿份") else s


def ev_bare(c):
    """sh510300 → 510300。quote()/fflow() 的缓存键都是裸码，传带前缀的码会一路漏到腾讯拼错"""
    return c[2:] if c[:2].lower() in ("sh", "sz", "bj") else c


def broad_block(no, cfg):
    """宽基 ETF 全景：按指数族聚合 + 持仓当日表现（含 KDAS 六状态）。
    族涨跌幅按成交额加权 —— 同族 4 只 ETF 涨跌幅有差异，等权会把小品种放大。"""
    print(f"\n{no}、宽基ETF全景（国家队载具 + 持仓）")
    qs, fls = {}, {}
    for c, _f in NATIONAL + NATIONAL_EXT:
        b = ev_bare(c)
        try:
            qs[c] = quote(b)
        except Exception:
            qs[c] = None
        try:
            fls[c] = fflow(b)
        except Exception:
            fls[c] = None
    sh = ev_shares([ev_bare(c) for c, _ in NATIONAL + NATIONAL_EXT])

    print("  族别           涨跌     成交额      主力净额     份额变化")
    agg = {}
    for c, fam in NATIONAL:
        q, fl = qs.get(c), fls.get(c)
        if not q:
            continue
        a = agg.setdefault(fam, {"amt": 0.0, "w": 0.0, "main": 0.0, "sh": 0.0,
                                 "sh_n": 0, "fl_n": 0})
        amt = q.get("amount") or 0
        a["amt"] += amt
        if q.get("pct") is not None:
            a["w"] += q["pct"] * (amt or 1)
        if fl:
            a["main"] += fl.get("main") or 0
            a["fl_n"] += 1
        s = sh.get(ev_bare(c))
        if s:
            a["sh"] += s["chg"]
            a["sh_n"] += 1
    if not agg:
        print("  无数据（载具报价全部缺失）")
        return

    tot = {"amt": 0.0, "w": 0.0, "main": 0.0, "sh": 0.0, "sh_n": 0, "fl_n": 0}
    for fam in FAM_ORDER:
        a = agg.get(fam)
        if not a:
            continue
        p = a["w"] / a["amt"] if a["amt"] else None
        sht = ev_sht(a["sh"]) if a["sh_n"] else "—"
        mns = wan(a["main"]) if a["fl_n"] else "—"
        print(f"  {fam:<10} {pct(p):>8}  {a['amt']/1e8:>7,.1f}亿  "
              f"{mns:>10}  {sht:>11}")
        for k in ("amt", "w", "main", "sh"):
            tot[k] += a[k]
        tot["sh_n"] += a["sh_n"]
        tot["fl_n"] += a["fl_n"]
    p = tot["w"] / tot["amt"] if tot["amt"] else None
    tsht = ev_sht(tot["sh"]) if tot["sh_n"] else "—"
    tmns = wan(tot["main"]) if tot["fl_n"] else "—"
    print(f"  {'合计':<10} {pct(p):>8}  {tot['amt']/1e8:>7,.1f}亿  "
          f"{tmns:>10}  {tsht:>11}")
    if not tot["fl_n"]:
        print("  主力口径        资金流取数失败，主力净额整列留空（不估算、不补 0）")
    if sh:
        print(f"  份额口径        份额取自连接器 total_shares，日内变化也可能来自其他机构；"
              f"载具 {len(sh)} 只有今日记录")
    else:
        print("  份额口径        今日无份额记录（未跑 etf_flow.py --mx），份额列按「—」处理，不外推")

    print("\n  持仓当日")
    print("  名称            现价      涨跌     成交额     主力净额    状态")
    for h in cfg["holdings"]:
        try:
            q = quote(h["code"])
        except Exception:
            q = None
        if not q:
            print(f"  {h['name']:<12} 取数失败")
            continue
        try:
            fl = fflow(h["code"])
        except Exception:
            fl = None
        st = "—"
        try:
            lv = levels(h["code"], q["price"] or q["prev"])
            if lv:
                st = lv["state"]
        except Exception:
            pass
        main_s = wan(fl["main"]) if fl else "—"
        print(f"  {h['name']:<12}{q['price']:>7.3f} {pct(q.get('pct')):>8} "
              f"{(q.get('amount') or 0)/1e8:>7.2f}亿 {main_s:>9}  {st}")
    print("  状态口径        只有「震荡区间」可做T（strategy_rules §4.0）；"
          "晚报不据此出动作，只标注明日能不能动手")


# ---------------------------------------------------------------- 可插拔指标段
# 固定前段（行情/持仓/关键位/异动）直接写在 morning/noon 里；只有「指标段」进注册表。
# 加指标 = 这里加一行，序号自动顺延；report_gen 按标题关键字解析，与顺序无关。
BLOCKS = [
    # (key, 段标题关键字, 函数, 适用模式)
    # 晚报（evening）只挂客观指标段；新闻由 evening() 单独放到文末，所以 news 不列 evening。
    ("market",  "大盘资金与国家队", market_block,  ("morning", "noon", "evening")),
    ("margin",  "融资融券",         margin_block,  ("morning", "noon", "evening")),
    ("news",    "实时快讯",         news_block,    ("morning", "noon")),
    ("impulse", "信贷脉冲",         impulse_block, ("morning", "noon", "evening")),
    ("ratio",   "股债比价",         ratio_block,   ("morning", "noon", "evening")),
    # tstats_block（做T战果）未进注册表：2026-11-16 用户要求报告不显示，函数保留备用
]

_CN = "零一二三四五六七八九十"


def cn(n):
    """1→一 … 10→十 11→十一 20→二十。段号只到二十，超出直接给数字。"""
    if n <= 10:
        return _CN[n]
    if n < 20:
        return "十" + _CN[n - 10]
    if n == 20:
        return "二十"
    return str(n)


def emit_blocks(cfg, mode, start):
    """按 BLOCKS 顺序输出指标段，序号从 start 之后自动顺延（start = 固定前段数）。
    单段异常不拖垮整份报告：打段头 + 失败原因，后续段继续，报告仍可交付。"""
    n = start
    for key, title, fn, modes in BLOCKS:
        if mode not in modes:
            continue
        n += 1
        try:
            fn(cn(n), cfg, mode)
        except Exception as e:
            print(f"\n{cn(n)}、{title}")
            print(f"  取数失败：{e}（本段不可用，按无信号处理）")
    return n


# ---------------------------------------------------------------- 指标读数台账
# 每条读数记一行，半年后能回答「这个档位真的管用吗」。没有这张表，加第 12 个指标
# 和第 3 个指标一样不可信——这是「指标体系」和「指标堆」的唯一区别。
SIGNALS_MD = os.path.join(ROOT, "signals.md")
_SIG_TITLE = "## 台账"
_SIG_COLS = "| date | key | value | band | hs300 | fwd20 |\n|---|---|---|---|---|---|\n"
_SIG_HEAD = (
    "# 指标读数台账\n\n"
    "> 一行 = 一个指标在某天的一次读数。**脚本自动写入，不要手工改历史行**。\n"
    "> 用途：把 value / band 与后续 20 个交易日的沪深300 涨跌对齐，按 band 分组算平均收益，\n"
    "> 和全样本比。跑 `python brief.py --signals backfill` 回填 fwd20（完全离线，靠表内未来的点位算）。\n\n"
    "## 字段\n\n"
    "| 字段 | 含义 |\n|---|---|\n"
    "| date | 读数日期（午盘口径，当日盘中） |\n"
    "| key | 指标编号，如 K04 股债比价 / K05 信贷脉冲；新增指标沿用此格式 |\n"
    "| value | 主读数，不带单位（比价=倍数，脉冲=pp） |\n"
    "| band | 当时所处档位，**决定动作的就是它**，回溯按这一列分组 |\n"
    "| hs300 | 当时沪深300点位，用来算未来收益 |\n"
    "| fwd20 | 20 个交易日后的涨跌幅，未到期留空 |\n\n"
)

_SIG = {}          # 本次运行收集到的读数：key -> (value, band, hs300)


def signal(key, value, band="", hs300=None):
    """指标块内部登记一条读数，由 signals_flush 统一落盘。"""
    _SIG[key] = (str(value), (band or "—").replace("|", "／"),
                 f"{hs300:.2f}" if isinstance(hs300, (int, float)) else "—")


def _sig_read():
    """(说明部分, 台账行列表)。说明部分可手工补笔记，脚本只重写台账表。"""
    txt = ""
    if os.path.exists(SIGNALS_MD):
        txt = open(SIGNALS_MD, encoding="utf-8").read()
    pre = txt.split(_SIG_TITLE)[0] if txt else _SIG_HEAD
    rows = [s.strip() for s in txt.splitlines() if s.strip().startswith("| 20")]
    return pre, rows


def _sig_write(pre, rows):
    with open(SIGNALS_MD, "w", encoding="utf-8") as f:
        f.write(pre.rstrip("\n") + "\n\n" + _SIG_TITLE + "\n\n" + _SIG_COLS
                + "\n".join(rows) + "\n")


def signals_flush(mode="noon"):
    """把本次读数追加进 signals.md。同一天重跑覆盖同一 key 的旧行，不产生重复。
    只写午盘：早盘是昨日收盘口径，写进去会和当天的行重复。"""
    if mode != "noon" or not _SIG:
        return
    date = datetime.now().strftime("%Y-%m-%d")
    pre, rows = _sig_read()

    keep = []
    for s in rows:
        c = [x.strip() for x in s.strip("|").split("|")]
        # 同一 key 当天重跑才覆盖；本次没取到数的指标（如 K05 失败）保留原有行
        if len(c) > 1 and c[0] == date and c[1] in _SIG:
            continue
        keep.append(s)
    for k, (v, band, px) in _SIG.items():
        keep.append(f"| {date} | {k} | {v} | {band} | {px} | |")

    _sig_write(pre, keep)
    _SIG.clear()


def signals_backfill():
    """回填 fwd20：同一 key 的第 i 行对比第 i+20 行的沪深300点位。
    完全离线——未来那天的点位已经记在表里了。中间有缺日会让偏移 ±1~2 天，不影响分组结论。"""
    if not os.path.exists(SIGNALS_MD):
        print("signals.md 不存在，先跑一次午盘面板攒数据")
        return
    pre, rows = _sig_read()
    cells = [[c.strip() for c in s.strip("|").split("|")] for s in rows]
    idx_by_key = {}
    for i, c in enumerate(cells):
        idx_by_key.setdefault(c[1], []).append(i)

    n, skipped = 0, 0
    for key, idx in idx_by_key.items():
        for j, i in enumerate(idx):
            if j + 20 >= len(idx):
                continue
            try:
                a, b = float(cells[i][4]), float(cells[idx[j + 20]][4])
            except (ValueError, IndexError):
                skipped += 1
                continue
            if a <= 0:
                skipped += 1
                continue
            cells[i][5] = f"{(b / a - 1) * 100:+.2f}%"
            n += 1

    _sig_write(pre, ["| " + " | ".join(c) + " |" for c in cells])
    print(f"已回填 {n} 行 fwd20" + (f"，跳过 {skipped} 行（点位缺失）" if skipped else ""))


def morning(cfg):
    print(f"【KDAS 早盘面板】{datetime.now():%Y-%m-%d %H:%M}  数据：{src_label()}\n")
    day_check(cfg)

    print("一、隔夜美股（收盘）")
    for sid, nm in [("100.DJIA", "道琼斯"), ("100.NDX", "纳斯达克"), ("100.SPX", "标普500")]:
        try:
            q = index_quote(sid, nm)
            if q.get("ok"):
                print(f"  {q['name']:<8} {q['price']:>10,.2f}  {pct(q['pct'])}")
            else:
                print(f"  {nm:<8} 取数失败")
        except Exception as e:
            print(f"  {nm:<8} 取数失败")
    for sid, nm in [("100.SOX", "费城半导体"), ("100.UDI", "美元指数")]:
        try:
            q = index_quote(sid, nm)
            if q.get("ok"):
                print(f"  {q['name']:<8} {q['price']:>10,.2f}  {pct(q['pct'])}")
        except Exception:
            pass

    print("\n二、亚洲早盘（07:00-08:30 参考）")
    try:
        s = sina(["hf_NK", "hf_HSI"])
        for k, nm in [("NK", "日经期货"), ("HSI", "恒指期货")]:
            v = s.get(k)
            if v and len(v) > 8:
                cur, prev = float(v[0]), float(v[7])
                print(f"  {nm:<8} {cur:>10,.1f}  {(cur/prev-1)*100:+.2f}%")
    except Exception:
        print("  期货数据取数失败")
    for sid, nm in [("100.N225", "日经225"), ("100.KS11", "韩国KOSPI"), ("100.HSI", "恒生指数")]:
        try:
            q = index_quote(sid, nm)
            if q.get("ok"):
                print(f"  {q['name']:<8} {q['price']:>10,.2f}  {pct(q['pct'])}")
            else:
                print(f"  {nm:<8} 取数失败")
        except Exception:
            print(f"  {nm:<8} 取数失败")

    print("\n三、持仓今日关键位（KDAS 日线 + 20日高低兜底）")
    print("  名称             参考价    成本      浮盈     支撑      压力     T空间  5日均振幅  状态（KDAS 六状态）")
    tot = 0.0
    rows = []
    for h in cfg["holdings"]:
        try:
            q = quote(h["code"])
            lv = levels(h["code"], q["price"] or q["prev"])
        except Exception:
            print(f"  {h['name']:<14} 取数失败")
            continue
        if not q or not lv:
            continue
        # 盘前（<09:15 集合竞价）f43 返回 0，用昨收兜底，否则浮盈显示 -100%
        px = q["price"] or q["prev"]
        mv = (px or 0) * h["shares"]
        tot += mv
        pnl = (px / h["cost"] - 1) * 100
        sup = lv["sup"][1] if lv["sup"] else None
        res = lv["res"][1] if lv["res"] else None
        space = f"{(res-sup)/sup*100:.1f}%" if sup and res else "—"
        amp5 = f"{lv['amp5']:.2f}%" if lv["amp5"] else "—"
        rows.append((h, q, lv, sup, res, space, pnl, mv))
        close_check(h["code"], q, lv)
        print(f"  {h['name']:<14}{px:>7.3f} {h['cost']:>8.3f} {pct(pnl):>9} "
              f"{('%.3f' % sup) if sup else '—':>9} {('%.3f' % res) if res else '—':>9} "
              f"{space:>7} {amp5:>9}  {lv['state']}")

    wr_rows = [f"{h['name']} {lv['relay']['line']:.3f}({lv['relay']['dir'] or '—'},{lv['relay']['bars_since']}日)"
               for h, q, lv, *_ in rows if lv["relay"].get("line")]
    if wr_rows:
        print(f"\n  强弱分界        {'｜'.join(wr_rows)}")
        print("  状态口径        站上强弱分界线=右侧（买点/震荡/空间/趋势），站不上=自然寻底；"
              "只有震荡区间可做T，空间窄是减仓区不是买点")

    print(f"\n  持仓市值合计（不含货币ETF）：{tot:,.0f} 元")
    print("\n四、今日重点")
    for h, q, lv, sup, res, space, pnl, mv in rows:
        tags = [f"{lv['state']}（{lv['state_why']}）"]
        sp = float(space.rstrip("%")) if space.endswith("%") else 0
        if lv["state"] in ("自然寻底", "右侧空间·窄", "右侧趋势"):
            tags.append("此状态不做T")
        if h.get("t0"):
            tags.append("可T+0")
        if pnl < -8:
            tags.append("深套，只做降本正T")
        if pnl > 15:
            tags.append("浮盈厚，优先止盈T")
        if lv.get("amp5") and lv["amp5"] < 1.8 and sp < 2:
            tags.append(f"5日振幅仅{lv['amp5']:.1f}%、T空间{sp:.1f}%，不值得频繁做")
        if sup:
            tags.append(f"跌破 {sup:.3f} 转弱")
        print(f"  · {h['name']}：{'；'.join(tags) if tags else '区间震荡，按支撑压力做'}")

    emit_blocks(cfg, "morning", start=4)   # 固定前段 4 段（一~四），指标段从「五」起

    print_xcheck(cfg)


# ---------------------------------------------------------------- 午盘
def noon(cfg):
    now = datetime.now()
    print(f"【KDAS 午盘面板】{now:%Y-%m-%d %H:%M}  数据：{src_label()}\n")
    day_check(cfg)

    print("一、A股上午")
    for sid, nm in [("1.000001", "上证指数"), ("0.399001", "深证成指"),
                    ("0.399006", "创业板指"), ("1.000688", "科创50")]:
        try:
            q = index_quote(sid, nm)
            if q.get("ok"):
                amt = f"成交额 {q['amount']/1e8:,.0f} 亿" if q.get("amount") else ""
                print(f"  {q['name']:<8} {q['price']:>10,.2f}  {pct(q['pct']):>8}   {amt}")
        except Exception:
            pass

    print("\n二、持仓上午表现")
    print("  名称           现价    涨跌    均价    日内位置   换手  量比  成交额")
    pack = []
    for h in cfg["holdings"]:
        # 三路取数各自降级：行情是全行必需，分时（仅用于昨日同期量）与资金流缺了只少字段，
        # 不能因为东财分时挂了把连接器已经拿到的这一行整只丢掉（2026-11-12 事故）。
        try:
            q = quote(h["code"])
        except Exception:
            q = None
        if not q:
            print(f"  {h['name']:<12} 取数失败"); continue
        try:
            tr = trends(h["code"], 2)
        except Exception:
            tr = []
        try:
            fl = fflow(h["code"])
        except Exception:
            fl = None
        today = now.strftime("%Y-%m-%d")
        t_today = [x for x in tr if x["dt"].startswith(today)]
        y_prev = [x for x in tr if not x["dt"].startswith(today)]
        # 昨日同时点成交额（对比放量/缩量）
        y_amt = None
        if y_prev:
            yd = y_prev[0]["dt"][:10]
            same = [x for x in y_prev if x["dt"].startswith(yd) and x["dt"][-5:] <= now.strftime("%H:%M")]
            if same:
                y_amt = sum(x["amount"] for x in same)
        pos = "—"
        if q["avg"] and q["price"]:
            pos = "均价上方" if q["price"] >= q["avg"] else "均价下方"
        print(f"  {h['name']:<12}{q['price']:>7.3f} {pct(q['pct']):>7} {q['avg']:>7.3f} "
              f"{pos:>8} {q['turn']:>5.2f}% {q['vr']:>5.2f} {q['amount']/1e8:>6.2f}亿")
        pack.append((h, q, fl, y_amt, pos))

    print("\n三、资金流（上午累计，元）")
    _lags = [x for x in (fflow_lag(fl) for _, _, fl, _, _ in pack) if x is not None]
    if _lags and max(_lags) > 5:
        print(f"  ⚠ 东财资金流末端滞后 {max(_lags)} 分钟（非实时），只能看方向不能看瞬时值")
    print("  名称           主力       超大单      大单       中单       小单     解读")
    for h, q, fl, y_amt, pos in pack:
        if not fl:
            print(f"  {h['name']:<12} 无资金流数据")
            continue
        main, huge, big, mid, small = fl["main"], fl["huge"], fl["big"], fl["mid"], fl["small"]
        if huge > 0 and big < 0:
            tag = "超大单进、大单撤（机构接游资）"
        elif huge < 0 and big > 0:
            tag = "超大单撤、大单接力（游资接机构）"
        elif huge > 0 and big > 0:
            tag = "大超大单同向流入，偏强"
        elif huge < 0 and big < 0:
            tag = "大超大单同向流出，偏弱"
        elif main > 0 and small > 0:
            tag = "散户+主力同买，情绪高"
        elif main < 0 and small > 0:
            tag = "散户接盘，警惕"
        else:
            tag = "多空混杂"
        if q.get("amount"):
            p_ = abs(main)/q['amount']*100
            tag += f"（占成交额{p_:.1f}%）" if p_ < 1 else f"（占成交额{p_:.0f}%）"
        print(f"  {h['name']:<12}{wan(main):>10}{wan(huge):>11}{wan(big):>10}"
              f"{wan(mid):>10}{wan(small):>10}  {tag}")

    print("\n四、日内位置与量能")
    for h, q, fl, y_amt, pos in pack:
        if not q.get("high") or not q.get("low"):
            continue
        rng = (q["price"] - q["low"]) / (q["high"] - q["low"]) * 100 if q["high"] > q["low"] else 50
        amp = (q["high"] - q["low"]) / q["prev"] * 100 if q["prev"] else 0
        vol_cmp = ""
        if y_amt:
            r = (q["amount"] / y_amt - 1) * 100
            vol_cmp = f"，较昨日同时点 {'放量' if r > 15 else '缩量' if r < -15 else '持平'} {r:+.0f}%"
        print(f"  {h['name']:<12} 日内 {bar(rng,0,100)} {rng:>3.0f}%  振幅 {amp:.2f}%{vol_cmp}")

    print("\n五、日线关键位（KDAS 六状态）")
    print("  名称           现价     最近支撑   距离     最近压力   距离     状态")
    wr_rows = []
    for h, q, fl, y_amt, pos in pack:
        try:
            lv = levels(h["code"], q["price"])
        except Exception:
            continue
        if not lv:
            continue
        close_check(h["code"], q, lv)
        sup = lv["sup"][1] if lv["sup"] else None
        res = lv["res"][1] if lv["res"] else None
        d_sup = (q["price"] / sup - 1) * 100 if sup else None
        d_res = (q["price"] / res - 1) * 100 if res else None
        st = lv["state"]
        print(f"  {h['name']:<12}{q['price']:>7.3f} "
              f"{('%.3f' % sup) if sup else '—':>10} {pct(d_sup) if d_sup is not None else '—':>8} "
              f"{('%.3f' % res) if res else '—':>10} {pct(d_res) if d_res is not None else '—':>8}  {st}")
        r = lv["relay"]
        if r.get("line"):
            wr_rows.append(f"{h['name']} {r['line']:.3f}({r['dir'] or '—'},{r['bars_since']}日)")
    if wr_rows:
        print(f"  强弱分界        {'｜'.join(wr_rows)}")
        print("  状态口径        站上强弱分界线=右侧（买点/震荡/空间/趋势），站不上=自然寻底；"
              "只有震荡区间可做T，空间窄是减仓区不是买点")

    print("\n六、异动提示")
    alerts = []
    for h, q, fl, y_amt, pos in pack:
        if fl and abs(fl["main"]) > 3e7:
            alerts.append(f"{h['name']} 主力{wan(fl['main'])}（占成交额 "
                          f"{abs(fl['main'])/q['amount']*100:.1f}%）")
        if q.get("turn") and q["turn"] > 5:
            alerts.append(f"{h['name']} 换手 {q['turn']:.1f}%，明显放大")
        if q.get("vr") and q["vr"] > 2:
            alerts.append(f"{h['name']} 量比 {q['vr']:.1f}，异动")
    print("  " + ("；\n  ".join(alerts) if alerts else "无明显异动"))

    emit_blocks(cfg, "noon", start=6)      # 固定前段 6 段（一~六），指标段从「七」起
    try:
        signals_flush("noon")              # 指标读数写 signals.md（一天一条，重跑覆盖）
    except Exception as e:
        print(f"\n  ⚠ 台账写入失败：{e}（不影响本次报告）")

    print_xcheck(cfg)


# ---------------------------------------------------------------- 晚报
def evening(cfg):
    """21:00 复盘：只看当天发生了什么，不出做T动作、不给触发价。

    段序固定：前四段是客观行情（A股/港股/全球/宽基ETF），指标段由 BLOCKS 自动顺延，
    要闻固定放文末 —— 复盘是「先看盘面、再看资金、最后看消息」的顺序。
    """
    print(f"【KDAS 晚报面板】{datetime.now():%Y-%m-%d %H:%M}  数据：{src_label()}\n")
    day_check(cfg)

    ashare_block("一")
    hk_block("二")
    global_block("三")
    broad_block("四", cfg)

    n = emit_blocks(cfg, "evening", start=4)      # 五~：大盘资金 / 两融 / 信贷脉冲 / 股债比价
    try:
        news_block(cn(n + 1), cfg, "evening")     # 要闻固定最后
    except Exception as e:
        print(f"\n{cn(n + 1)}、实时快讯（近期发生的事，便于即时判断）")
        print(f"  取数失败：{e}（本段不可用，按无消息处理）")

    print_xcheck(cfg)


def selftest(code):
    """核对腾讯字段位：打印原始 ~ 分隔内容 + 解析结果，一次命令看清有没有错位"""
    sym = sina_code(code)
    print(f"腾讯原始返回 {sym}")
    try:
        txt = _get(f"https://qt.gtimg.cn/q={sym}", ref="https://gu.qq.com/", decode="gbk")
    except Exception as e:
        print("  取数失败：", e)
        return
    body = txt.split('="', 1)[1] if '="' in txt else ""
    body = body.rstrip().rstrip(";").rstrip('"')
    f = body.split("~")
    print(f"  字段数 {len(f)}")
    for i, v in enumerate(f):
        if v.strip():
            print(f"  [{i:>2}] {v}")
    try:
        print("解析：", json.dumps(quote_tx(code), ensure_ascii=False))
    except Exception as e:
        print("解析失败：", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="morning",
                    choices=["morning", "noon", "evening"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--src", choices=["auto", "tx", "em"],
                    default=os.environ.get("BRIEF_SRC", "auto"),
                    help="报价源：auto=腾讯为主+东财校验（默认）/ tx / em")
    ap.add_argument("--selftest", metavar="CODE", help="打印腾讯原始字段，核对字段位")
    ap.add_argument("--signals", choices=["backfill"],
                    help="维护 signals.md 台账：回填 fwd20（离线，用表内未来的点位）")
    a = ap.parse_args()
    SRC[0] = a.src
    if a.signals == "backfill":      # 台账维护不需要行情，放在 load_mx 之前
        return signals_backfill()
    load_mx()
    if a.selftest:
        return selftest(a.selftest)
    with open(os.path.join(ROOT, "portfolio.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    # 货币ETF只做现金管理，不进做T池（否则会算进市值合计、占一行 KDAS 关键位）
    cfg["holdings"] = [h for h in cfg["holdings"]
                       if h.get("role") != "现金管理" and h.get("code") != "511880"]
    (morning if a.mode == "morning" else noon if a.mode == "noon" else evening)(cfg)


if __name__ == "__main__":
    main()
