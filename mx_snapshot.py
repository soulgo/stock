#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mx_snapshot.py — 把腾讯自选股连接器（westock-mcp）的原始返回转成 brief.py 能读的快照

为什么需要这一步：MCP 工具只能由上层 agent 调用，brief.py 这类脚本无法直连。
所以流程是「agent 调连接器 → 原样存两份 JSON → 本脚本归一化 → data/_mx_<date>.json
→ brief.py 优先读它」。

  python mx_snapshot.py --quotes data/_raw_quote.json --flows data/_raw_flow.json

字段来源：
  data_quote      price / prev_close / open / high / low / volume(股) / amount(元)
                  / change / change_percent / turnover_rate / volume_ratio / avg_price
  data_fund_flow  MainNetFlow(主力) / JumboNetFlow(超大单) / BlockNetFlow(大单)
                  / MidNetFlow(中单) / SmallNetFlow(小单)

written 是本机写入时间，brief.py 要求当日且写入后 0~3 分钟；报价另查源 time，
资金流另保留 EndDate。源日期缺失不以本机时钟补造；缺字段保留 None，真实零保留 0。
连接器目前仅提供源日期，无法证明分钟级新鲜；重写旧数据不能使它成为当日数据。
"""

import argparse
import json
import os
from datetime import datetime

from paths import DATA                  # 路径唯一真源（ADR-001）


def bare(code):
    """sh512170 / sz159928 → 512170 / 159928，与 portfolio.json 对齐"""
    c = str(code or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return c[len(p):]
    return c.split(".")[0]


def pick_market(obj):
    """data_market_overview(type=all) → 只留做T用得上的几项"""
    rows = {}
    for it in (obj.get("data") or []):
        if isinstance(it, dict) and it.get("type"):
            rows[it["type"]] = it.get("row") or {}
    trade, updown = rows.get("trade") or {}, rows.get("updown") or {}
    d = ""
    for it in (obj.get("data") or []):
        if isinstance(it, dict) and it.get("date"):
            d = it["date"]
            break
    return {
        "date": d,
        "amount": trade.get("MONEY"),
        "amount_ratio20": trade.get("MONEY_20DAVG_RATIO"),
        "up": updown.get("CNT_RED"), "down": updown.get("CNT_GREEN"),
        "limit_up": updown.get("CNT_REACH_UPLIMIT"),
        "limit_dn": updown.get("CNT_REACH_DNLIMIT"),
    }


def pick_etf(obj):
    """data_etf(aspect=overview) → 载具规模，用来观察国家队进出"""
    out = {}
    for k, v in (obj.get("data") or {}).items():
        if not v:
            continue
        out[str(k).strip().lower()] = {
            "name": v.get("name"), "size": v.get("etfSize"),
            "chg": v.get("chgPct"), "turn": v.get("turnoverRate"),
            "date": v.get("date"),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quotes", required=True,
                    help="data_quote 的原始返回文件；多个用英文逗号分隔")
    ap.add_argument("--flows", help="data_fund_flow 的原始返回文件（可选；多个用英文逗号分隔）")
    ap.add_argument("--market", help="data_market_overview 的原始返回文件（可选）")
    ap.add_argument("--etf", help="data_etf(overview) 的原始返回文件（可选，国家队载具）")
    ap.add_argument("--out", help="输出路径，缺省 data/_mx_YYYY-MM-DD.json")
    a = ap.parse_args()

    quotes = {}
    quotes_full = {}      # 保留带市场前缀的 key（sh000001），供指数查表
    for path in [p.strip() for p in a.quotes.split(",") if p.strip()]:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as source_file:
            q = json.load(source_file)
        for k, v in (q.get("data") or {}).items():
            if not v:
                continue
            e = {
                "code": bare(k), "name": v.get("name"),
                "price": v.get("price"), "prev_close": v.get("prev_close"),
                "open": v.get("open"), "high": v.get("high"), "low": v.get("low"),
                "volume": v.get("volume"), "amount": v.get("amount"),
                "change": v.get("change"), "change_percent": v.get("change_percent"),
                "turnover_rate": v.get("turnover_rate"),
                "volume_ratio": v.get("volume_ratio"),
                "avg_price": v.get("avg_price"), "time": v.get("time"),
                # 总份额：K01 用它算申赎（东财 f84 已断，这条是当前唯一可靠来源）
                "total_shares": v.get("total_shares"),
                "total_market_cap": v.get("total_market_cap"),
            }
            quotes[bare(k)] = e
            quotes_full[str(k).strip().lower()] = e

    flows, flows_full = {}, {}
    for path in [p.strip() for p in (a.flows or "").split(",") if p.strip()]:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as source_file:
            f = json.load(source_file)
        for k, v in (f.get("data") or {}).items():
            arr = (v or {}).get("data") or []
            if not arr:
                continue
            r = arr[-1]
            num = lambda key: float(r[key]) if r.get(key) not in (None, "") else None
            e = {
                "date": r.get("EndDate"), "time": r.get("EndDate"), "main": num("MainNetFlow"), "huge": num("JumboNetFlow"),
                "big": num("BlockNetFlow"), "mid": num("MidNetFlow"),
                "small": num("SmallNetFlow"),
                # 多日累计：判断国家队是托底还是退潮，单一交易日不够
                "main5": num("MainNetFlow5D"), "main10": num("MainNetFlow10D"),
                "main20": num("MainNetFlow20D"),
            }
            flows[bare(k)] = e
            flows_full[str(k).strip().lower()] = e

    market, etf = {}, {}
    if a.market and os.path.exists(a.market):
        with open(a.market, encoding="utf-8") as source_file:
            market = pick_market(json.load(source_file))
    if a.etf and os.path.exists(a.etf):
        with open(a.etf, encoding="utf-8") as source_file:
            etf = pick_etf(json.load(source_file))

    out = a.out or os.path.join(DATA, f"_mx_{datetime.now():%Y-%m-%d}.json")
    payload = {"date": datetime.now().strftime("%Y-%m-%d"),
               "written": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "src": "westock-mcp（腾讯自选股连接器）",
               "quotes": quotes, "quotes_full": quotes_full, "flows": flows,
               "flows_full": flows_full, "market": market, "etf": etf}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=1)
    print(f"OK {out}")
    print(f"报价 {len(quotes)} 只 / 资金流 {len(flows)} 只 / "
          f"市场概览 {'有' if market else '无'} / 国家队载具 {len(etf)} 只，"
          f"写入时间 {payload['written']}")


if __name__ == "__main__":
    main()
