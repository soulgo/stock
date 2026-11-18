# -*- coding: utf-8 -*-
"""indicators/ —— 指标计算层（ADR-001）。

一个 knowledge/K0x.md 对应一个模块，每个模块提供：
  - `block(no, mode)`：往 brief.py 的 stdout 打印一段（段号由调用方传入）
  - `snapshot()` / `build()` 等纯计算入口，供 CLI 与调试直接调用

约定：
  - 模块**不做渲染**，只输出「两空格 + 标签 + 2 个以上空格 + 值」的文本行；
  - 路径一律 `from paths import DATA, REPORTS`，不自己拼 `data/`；
  - 新增指标：这里加文件 + `knowledge/K0x.md` 写口径 + `brief.py` 的 `BLOCKS` 加一行。

现有：
  - equity_bond_ratio.py   K04 股债比价（沪深300全收益 ÷ 二级债基）
  - credit_impulse.py      K05 信贷脉冲（社融存量同比 12 个月差分）
"""
