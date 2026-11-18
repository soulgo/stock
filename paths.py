#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""paths.py — 路径唯一真源（ADR-001）。

所有脚本取数据/报告路径都从这里拿，**不要在别处拼字符串**。
- 根目录的入口脚本、indicators/ 下的模块：直接 `from paths import DATA, data`。
- tools/ 下的脚本：先把自己所在目录的父目录插入 sys.path，再 `from paths import ...`。

为什么需要它：`BASE = dirname(__file__)` 在脚本搬家后会把 `data/` 解析到新目录下，
表现为"取数正常但读写的是另一个 data/"——静默错，最难查。
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
REPORTS = os.path.join(ROOT, "reports")
ARCHIVE = os.path.join(REPORTS, "archive")
DOCS = os.path.join(ROOT, "docs")
KNOWLEDGE = os.path.join(ROOT, "knowledge")
INDICATORS = os.path.join(ROOT, "indicators")


def data(name=""):
    """data("etf_flow.csv") → <root>/data/etf_flow.csv"""
    return os.path.join(DATA, name) if name else DATA


def report(name=""):
    """report("_morning_2026-11-18.txt") → <root>/reports/..."""
    return os.path.join(REPORTS, name) if name else REPORTS
