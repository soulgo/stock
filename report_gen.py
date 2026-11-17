#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
report_gen.py — 把 brief.py 的文本输出 + 决策 JSON 渲染成移动端 HTML 报告

设计原则：行情数字 100% 来自 brief.py 的输出（脚本解析，不手抄）；
          判断结论（定调 / 动作 / 不动 / 风险）写在 decision JSON 里；
          版式由本脚本固定，两个定时任务共用一套，不会跑偏。

用法：
  python brief.py noon    > reports/_noon_2026-11-11.txt
  python report_gen.py --mode noon --data reports/_noon_2026-11-11.txt \
                       --decision reports/_decision_noon_2026-11-11.json

输出：reports/午盘T计划_2026-11-11.html（自包含，内联 CSS，可直接分享）

decision JSON 结构（mode 通用的字段）：
{
  "date": "2026-11-11",                      // 可选，缺省取 brief 面板日期
  "subtitle": "午盘 12:30 · 东财公开接口",     // 可选
  "verdict": "一句话定调，支持 <b> 标签",      // 必填
  "actions": [                               // 最多 3 条，没有就给 []
    {
      "name": "香港医药", "code": "513700",
      "direction": "倒T 回补",
      "side": "buy",                        // buy=蓝 / sell=橙
      "trigger": "0.611",                  // 触发价，超大字号显示
      "shares": "3100 份",
      "target": "原卖 0.628 · 价差 +0.017", // 卖出/回补价
      "invalid": "0.605",                  // 失效/作废价
      "deadline": "14:30 未成交即市价补",    // 可选，截止纪律
      "why": "理由，必须引用具体数据",
      "note": "可选备注，例如定位声明/旧账提醒"
    }
  ],
  "reads": [                                 // 资金面解读卡（可选，2~4 张）
    {
      "tag": "大盘资金",                     // 卡片小标签
      "tone": "blue",                        // blue / green / 留空
      "title": "主力跑了 235.7 亿，接盘的是小单",
      "body": ["第一段…", "第二段…"],       // 每段 60~120 字，允许 <b>
      "src": "财联社 11-16"                  // 可选，来源与日期
    }
  ],
  "skip": [ {"name":"医疗ETF","code":"512170","why":"...原因..."} ],
  "risks": ["风险1", "风险2"],               // 也可写 {"t":"短标题","d":"正文"}
  "alerts": ["可选的待办/记录提醒"],          // 也可写 {"t":"短标题","d":"正文"}
  "flash_digest": [                          // 可选：快讯「要点」，2~5 条，最多 5 条
    {"m":"08:12", "t":"美财长质疑英伟达营收，纳指再跌 2.49%"}
  ]
}

2026-11-17 增补（用户反馈后）：
  1) 指标卡前统一加「一句话看懂」大白话条（PLAIN 表，不含具体数字），
     覆盖：大盘资金 / 国家队 / 两融 / 信贷脉冲 / 股债比价 / KDAS 状态 / 快讯；
  2) 全局字号放大（正文 17px，最小标签 11.5px），手机上一眼可读；
  3) 风险、待办改成「短标题 + 正文」的小卡/行式，不再是一坨长段落；
     传字符串时用 _lead() 按冒号/逗号自动切标题，传 dict 时用 t/d 字段；
  4) 快讯改为「要点 3~5 条 + 明细折叠」：要点来自 decision["flash_digest"]，
     没写就自动取「相关」条目前 4 条；余下条目折叠进 <details>。
  5) 信贷脉冲 / 股债比价口径细节折叠进 <details>，卡面只留 大字 + 四格 + 一句结论。

早盘和午盘的唯一区别：数据区（隔夜外盘 vs 上午大盘/资金流/四象限），
其余版式完全一致。

「资金面解读」区由两部分拼成：
  1) 大盘资金数据卡 + 国家队载具 —— 全部由 brief.py 的「大盘资金与国家队」段自动解析渲染
     （parse_market / render_market），决策 JSON 里<b>不要</b>再写这些数字；
  2) 解读文字卡 —— 来自 decision["reads"]，讲结构含义、国家队口径、消息面与对持仓的影响。
两者都缺时该区不渲染，后续段落编号自动收回。
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "reports")

E = lambda s: html.escape(str(s), quote=True)

# ------------------------------------------------------------------ CSS
CSS = r"""
:root{
  --bg:#f2f3f6; --card:#fff; --line:#e2e5eb; --soft:#eef0f4;
  --ink:#14171c; --ink2:#454b54; --ink3:#7c8490;
  --up:#cf3126; --upbg:#fdecea; --down:#0d8f66; --downbg:#e7f5f0;
  --buy:#1d4ed8; --buybg:#eaf0fe; --sell:#b45309; --sellbg:#fdf3e4;
  --mono:"SF Mono","JetBrains Mono",Consolas,Menlo,monospace;
  --sh:0 1px 2px rgba(18,23,32,.05),0 10px 26px -16px rgba(18,23,32,.22);
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--ink);font:17px/1.68 "PingFang SC","Hiragino Sans GB","Microsoft YaHei",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased;padding:14px 13px 104px}
.wrap{max-width:680px;margin:0 auto}
a{color:inherit}
header{margin-bottom:14px;padding:0 2px}
.eyebrow{font-size:12px;letter-spacing:.14em;color:var(--ink3);font-weight:700;margin-bottom:6px}
h1{font-size:clamp(23px,6.2vw,29px);font-weight:800;letter-spacing:-.02em;line-height:1.3}
h1 em{font-style:normal;color:var(--ink3);font-weight:400;margin:0 4px}
.meta{margin-top:9px;display:flex;flex-wrap:wrap;gap:6px;font-size:12.5px;color:var(--ink3)}
.meta i{font-style:normal;background:#fff;border:1px solid var(--line);border-radius:999px;padding:3px 10px;color:var(--ink2);font-variant-numeric:tabular-nums}
.verdict{background:#fff;border:1px solid var(--line);border-left:4px solid var(--ink);border-radius:12px;padding:14px 15px;font-size:15.5px;color:var(--ink2);line-height:1.7;box-shadow:var(--sh);margin-bottom:6px}
.verdict b{color:var(--ink)}
.verdict .lab{display:block;font-size:11.5px;letter-spacing:.12em;font-weight:700;color:var(--ink3);margin-bottom:4px}
section{margin-top:26px;scroll-margin-top:12px}
.sec-head{display:flex;align-items:center;gap:9px;margin-bottom:11px;padding:0 2px;flex-wrap:wrap}
.sec-head h2{font-size:17.5px;font-weight:800;letter-spacing:-.01em}
.sec-head .n{font-size:12px;font-weight:800;color:#fff;background:var(--ink);width:22px;height:22px;border-radius:7px;display:grid;place-items:center;flex:0 0 auto}
.sec-head .hint{margin-left:auto;font-size:12.5px;color:var(--ink3);font-weight:600;text-align:right;line-height:1.4;max-width:100%}
.empty{background:#fff;border:1px dashed var(--line);border-radius:12px;padding:22px 18px;text-align:center;color:var(--ink3);font-size:15px;line-height:1.75}
.empty b{display:block;font-size:18px;color:var(--ink);font-weight:800;margin-bottom:4px}
/* ---- 大白话解释条 ---- */
.plain{background:#f1f5ff;border:1px solid #dbe5fb;border-left:4px solid #1d4ed8;border-radius:11px;padding:12px 14px;margin:0 0 10px;font-size:15px;line-height:1.8;color:#20293a}
.plain .pl{display:block;font-size:11.5px;font-weight:800;letter-spacing:.1em;color:#1d4ed8;margin-bottom:4px}
.plain b{color:#12203c;font-weight:800}
.plain.warm{background:#fffaf0;border-color:#f2e5c8;border-left-color:#b45309}
.plain.warm .pl{color:#b45309}
/* ---- 动作卡 ---- */
.ticket{background:#fff;border:1px solid var(--line);border-radius:16px;overflow:hidden;box-shadow:0 2px 4px rgba(18,23,32,.05),0 18px 40px -26px rgba(18,23,32,.4);margin-bottom:14px;scroll-margin-top:10px}
.t-bar{height:5px;background:linear-gradient(90deg,var(--buy),#6f96f5)}
.ticket.sell .t-bar{background:linear-gradient(90deg,var(--sell),#e6a94f)}
.t-head{display:flex;align-items:center;gap:9px;padding:14px 15px 6px;flex-wrap:wrap}
.t-step{width:25px;height:25px;border-radius:7px;background:var(--ink);color:#fff;font-size:13px;font-weight:800;display:grid;place-items:center;flex:0 0 auto}
.t-name{display:flex;flex-direction:column;gap:2px;margin-right:auto;min-width:0}
.t-name .n{font-size:17px;font-weight:800;letter-spacing:-.01em}
.t-name .n span{font-family:var(--mono);font-size:12.5px;font-weight:600;color:var(--ink3);margin-left:5px}
.t-name .d{font-size:12px;color:var(--ink3)}
.t-badge{font-size:13px;font-weight:800;padding:4px 11px;border-radius:7px;white-space:nowrap;background:var(--buy);color:#fff}
.ticket.sell .t-badge{background:var(--sell)}
.t-hero{padding:6px 15px 13px}
.h-lab{font-size:12.5px;font-weight:700;color:var(--ink3);letter-spacing:.04em;display:flex;align-items:center;gap:6px}
.h-lab .dot{width:17px;height:17px;border-radius:50%;background:var(--buybg);color:var(--buy);font-size:10px;font-weight:800;display:grid;place-items:center}
.ticket.sell .h-lab .dot{background:var(--sellbg);color:var(--sell)}
.h-px{font-family:var(--mono);font-size:clamp(42px,13.5vw,58px);font-weight:800;line-height:1.06;letter-spacing:-.035em;color:var(--buy);user-select:all;display:inline-block}
.ticket.sell .h-px{color:var(--sell)}
.h-qty{font-size:15px;font-weight:700;color:var(--ink2);margin-top:2px;font-variant-numeric:tabular-nums}
.h-qty span{font-size:12.5px;font-weight:600;color:var(--ink3)}
.t-sub{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--soft);border-top:1px solid var(--soft)}
.s-cell{background:#fbfcfd;padding:11px 15px}
.s-lab{font-size:11.5px;font-weight:700;color:var(--ink3);letter-spacing:.03em;margin-bottom:2px}
.s-px{font-family:var(--mono);font-size:clamp(19px,5.4vw,24px);font-weight:800;letter-spacing:-.02em;line-height:1.25;user-select:all}
.s-px.tgt{color:var(--up)} .s-px.stp{color:var(--ink2)}
.s-txt{font-size:13px;font-weight:700;color:var(--ink2);line-height:1.5}
.t-dead{margin:0;padding:10px 15px;background:var(--buybg);font-size:13px;color:#1a3fa8;font-weight:700;border-top:1px solid #dbe4fb}
.ticket.sell .t-dead{background:var(--sellbg);color:#8a4406;border-top-color:#f2e0c4}
.t-why{padding:12px 15px;font-size:14px;color:var(--ink2);line-height:1.75}
.t-why b{color:var(--ink)}
.t-note{margin:0 15px 14px;padding:9px 12px;background:#f5f6f9;border-radius:8px;font-size:12.5px;color:var(--ink2);line-height:1.65}
/* ---- 指数网格 ---- */
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.idx{background:#fff;border:1px solid var(--line);border-radius:11px;padding:11px 12px}
.idx .nm{font-size:12.5px;color:var(--ink3)}
.idx .px{font-family:var(--mono);font-size:18px;font-weight:800;font-variant-numeric:tabular-nums}
.idx .ch{font-size:13.5px;font-weight:800;font-variant-numeric:tabular-nums;margin-left:5px}
.idx .am{font-size:12px;color:var(--ink3);margin-top:3px;font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--ink3)}
/* ---- 映射矩阵 / 四象限 ---- */
.quad{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.q{background:#fff;border:1px solid var(--line);border-radius:11px;padding:11px 12px}
.q.hot{border-color:#f0c9c5;background:#fffafa}
.q.cold{border-color:#c8e8dc;background:#fafffd}
.q .qt{font-size:12px;font-weight:800;margin-bottom:7px;line-height:1.5}
.q .qt .k{font-size:10.5px;font-weight:800;padding:1px 5px;border-radius:3px;background:#f2f4f7;color:var(--ink3);margin-right:4px}
.q .chips{display:flex;flex-wrap:wrap;gap:4px}
.chip{font-size:11.5px;padding:2px 8px;border-radius:5px;background:#f5f6f9;border:1px solid var(--soft);color:var(--ink2);font-weight:700}
.q .qd{font-size:12px;color:var(--ink3);margin-top:7px;line-height:1.6}
.signal{background:#fff;border:1px solid var(--line);border-radius:11px;padding:2px 14px;margin-top:8px}
.signal div{padding:11px 0;font-size:14px;color:var(--ink2);line-height:1.68;border-bottom:1px solid var(--soft)}
.signal div:last-child{border:0}
.signal b{color:var(--ink)}
.signal .k{display:inline-block;font-size:11px;font-weight:800;padding:1px 6px;border-radius:4px;background:var(--upbg);color:var(--up);margin-right:6px}
.signal .k.g{background:var(--downbg);color:var(--down)}
.signal .k.n{background:#f2f4f7;color:var(--ink3)}
/* ---- 持仓折叠 ---- */
details.h{background:#fff;border:1px solid var(--line);border-radius:13px;margin-bottom:8px;overflow:hidden}
details.h[open]{box-shadow:var(--sh)}
details.h > summary{list-style:none;cursor:pointer;padding:12px 28px 12px 13px;display:flex;align-items:center;gap:5px;flex-wrap:wrap;position:relative}
details.h > summary::-webkit-details-marker{display:none}
.sm-name{font-size:15.5px;font-weight:800}
.sm-code{font-family:var(--mono);font-size:12px;color:var(--ink3);font-weight:600}
.sm-px{font-family:var(--mono);font-size:15px;font-weight:800;font-variant-numeric:tabular-nums}
.sm-ch{font-size:13.5px;font-weight:800;font-variant-numeric:tabular-nums}
.sm-main{margin-left:auto;font-size:11.5px;font-weight:800;white-space:nowrap;font-variant-numeric:tabular-nums;padding:2px 6px;border-radius:6px;background:#f5f6f9;color:var(--ink3)}
.sm-main.in{background:var(--upbg);color:var(--up)}
.sm-main.out{background:var(--downbg);color:var(--down)}
.sm-pos{font-size:11px;font-weight:700;white-space:nowrap;padding:2px 6px;border-radius:5px;background:#f2f4f7;color:var(--ink3)}
.sm-pos.a{background:var(--upbg);color:var(--up)}
.sm-pos.b{background:var(--downbg);color:var(--down)}
.sm-pos.c{background:#fdf3e4;color:#b45309}
.sm-pos.d{background:var(--upbg);color:var(--up)}
.sm-arrow{position:absolute;right:10px;top:50%;margin-top:-8px;font-size:12px;color:var(--ink3);transition:transform .18s;display:inline-block}
details.h[open] .sm-arrow{transform:rotate(180deg)}
details.h[open] > summary .sm-arrow{margin-top:-22px}
.h-body{padding:2px 13px 13px;border-top:1px solid var(--soft)}
.kv{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:var(--soft);margin-top:10px;border-radius:8px;overflow:hidden}
.kv div{background:#fbfcfd;padding:8px 10px;font-size:12px;color:var(--ink3)}
.kv b{display:block;font-family:var(--mono);font-size:14.5px;color:var(--ink);font-variant-numeric:tabular-nums;font-weight:800}
.track{position:relative;height:8px;border-radius:99px;background:#edeff3;overflow:hidden;margin-top:6px}
.track i{position:absolute;left:0;top:0;bottom:0;border-radius:99px;background:linear-gradient(90deg,#5f9df5,#1d4ed8)}
.track i.hot{background:linear-gradient(90deg,#eab308,#d97706)}
.pos-lab{display:flex;justify-content:space-between;font-size:12px;color:var(--ink3);margin:10px 0 0}
.pos-lab b{color:var(--ink2);font-variant-numeric:tabular-nums}
.fl{margin-top:10px;padding-top:9px;border-top:1px dashed var(--soft)}
.fl-t{font-size:12px;color:var(--ink3);margin-bottom:6px}
.fl-t b{color:var(--ink2)}
.fl-r{display:grid;grid-template-columns:48px 1fr 72px;gap:7px;align-items:center;margin-bottom:4px}
.fl-r .lb{font-size:12px;color:var(--ink3)}
.fl-r .vl{font-family:var(--mono);font-size:12px;text-align:right;font-weight:700;font-variant-numeric:tabular-nums}
.dual{position:relative;display:grid;grid-template-columns:1fr 1fr;height:11px}
.dual::after{content:"";position:absolute;left:50%;top:-1px;bottom:-1px;width:1px;background:#dde1e8}
.half{display:flex;align-items:center}.half.l{justify-content:flex-end}.half.r{justify-content:flex-start}
.bar{height:9px;border-radius:2px}.bar.p{background:var(--up)}.bar.n{background:var(--down)}
.fl-n{font-size:12px;color:var(--ink3);margin-top:6px;line-height:1.65}
/* ---- 不动 ---- */
.skip{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.sr{padding:11px 14px;border-bottom:1px solid var(--soft)}
.sr:last-child{border:0}
.sr .sn{font-size:15px;font-weight:800;display:flex;align-items:baseline;gap:6px}
.sr .sn code{font-family:var(--mono);font-size:12px;color:var(--ink3);font-weight:600}
.sr .st{font-size:13px;color:var(--ink2);line-height:1.68;margin-top:2px}
/* ---- 风险（卡片化） ---- */
.risk{background:#fff;border:1px solid var(--line);border-radius:13px;overflow:hidden}
.risk .ri{display:grid;grid-template-columns:24px 1fr;gap:10px;padding:12px 14px;border-bottom:1px solid var(--soft)}
.risk .ri:last-child{border:0}
.risk .nn{font-family:var(--mono);font-size:12px;font-weight:800;color:#b4811a;background:#fdf5e4;width:22px;height:22px;border-radius:6px;display:grid;place-items:center;margin-top:1px}
.risk .rt{font-size:15px;font-weight:800;color:var(--ink);line-height:1.5}
.risk .rd{font-size:13.5px;color:var(--ink2);line-height:1.75;margin-top:3px}
.risk .rd b{color:var(--ink)}
.risk .ri.one{grid-template-columns:22px 1fr}
.risk .ri.one .rd{font-size:14px;color:var(--ink2);margin-top:0}
/* ---- 待办（行式） ---- */
.todo{background:#fffdf6;border:1px solid #f0e4c4;border-radius:13px;overflow:hidden}
.todo .ti{display:grid;grid-template-columns:18px 1fr;gap:9px;padding:11px 14px;border-bottom:1px dashed #f0e4c4;font-size:14px;color:var(--ink2);line-height:1.72}
.todo .ti:last-child{border:0}
.todo .ti .b{color:#b4811a;font-weight:800;font-size:13px;padding-top:1px}
.todo .ti b{color:var(--ink)}
footer{margin-top:24px;padding:12px 2px 0;border-top:1px solid var(--line);font-size:12px;color:var(--ink3);line-height:1.85}
footer b{color:var(--ink2)}
/* ---- 大盘资金 / 国家队 ---- */
.mbox{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.mb-hd{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px;padding:12px 14px 8px;font-size:12.5px;color:var(--ink3)}
.mb-hd b{font-size:14px;color:var(--ink2);white-space:nowrap}
.mb-hd span{white-space:nowrap}
.mb-main{display:flex;align-items:flex-end;gap:8px;padding:0 14px 10px}
.mb-main .v{font-family:var(--mono);font-size:31px;font-weight:800;letter-spacing:-.5px;line-height:1}
.mb-main .u{font-size:12.5px;color:var(--ink3);padding-bottom:3px}
.mb-pills{display:flex;flex-wrap:wrap;gap:5px;padding:0 14px 12px}
.pill{font-size:12px;padding:3px 9px;border-radius:6px;background:var(--soft);color:var(--ink2);font-weight:700}
.pill b{font-family:var(--mono);font-weight:800}
.pill.up{background:var(--upbg);color:var(--up)}
.pill.down{background:var(--downbg);color:var(--down)}
.rbar{position:relative;height:54px;margin:14px 14px 0}
.rbar .tk{position:absolute;left:0;right:0;top:22px;height:8px;border-radius:4px;
  background:linear-gradient(90deg,#1d4ed8 0%,#93aae8 18%,#e2e5eb 50%,#e8b48c 82%,#cf3126 100%)}
.rbar .pt{position:absolute;top:0;transform:translateX(-50%);text-align:center}
.rbar .pt i{display:block;width:0;height:0;border-left:5px solid transparent;
  border-right:5px solid transparent;border-top:7px solid var(--ink);margin:0 auto}
.rbar .pt b{display:block;font-family:var(--mono);font-size:12.5px;font-weight:800;margin-top:2px}
.rbar .en{position:absolute;top:36px;font-family:var(--mono);font-size:11.5px;font-weight:700;
  color:var(--ink3);line-height:1.35}
.rbar .en.l{left:0}
.rbar .en.r{right:0;text-align:right}
.rgrid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:14px}
.rcell{background:var(--soft);border-radius:9px;padding:9px 11px}
.rcell .t{display:block;font-size:12px;color:var(--ink3);font-weight:700}
.rcell .v{display:block;font-family:var(--mono);font-size:17.5px;font-weight:800;margin-top:2px}
.rcell .d{display:block;font-family:var(--mono);font-size:12px;font-weight:700;margin-top:2px}
.mb-sep{height:1px;background:var(--soft);margin:0 14px}
.nat{display:flex;flex-wrap:wrap;gap:4px;padding:10px 14px 12px}
.nat i{font-style:normal;font-family:var(--mono);font-size:11.5px;font-weight:700;padding:2px 7px;border-radius:5px;background:#f5f6f9;border:1px solid var(--soft);color:var(--ink2)}
.nat i.up{background:var(--upbg);border-color:#f6d5d1;color:var(--up)}
.nat i.down{background:var(--downbg);border-color:#cfe9df;color:var(--down)}
.cut{display:flex;align-items:center;gap:10px;margin:10px 14px 2px;padding:10px 11px;border-radius:9px;background:#f7f8fb;border:1px solid var(--soft)}
.cut .cx{font-family:var(--mono);font-size:22px;font-weight:800;line-height:1;color:var(--ink2);white-space:nowrap}
.cut .cx.up{color:var(--up)}
.cut .cx.down{color:var(--down)}
.cut .ct{font-size:12.5px;line-height:1.6;color:var(--ink3)}
.cut .ct b{color:var(--ink2)}
.mb-sub{padding:6px 14px 10px;font-size:12.5px;line-height:1.7;color:var(--ink3)}
.mb-sub b{color:var(--ink2)}
details.natd{border-top:1px dashed var(--soft);margin:0 14px}
details.natd summary{padding:9px 0;font-size:12.5px;color:var(--ink3);cursor:pointer;list-style:none}
details.natd summary::-webkit-details-marker{display:none}
details.natd summary::after{content:" ›";color:var(--ink3)}
details.natd[open] summary::after{content:" ⌄"}
details.natd .nat{padding:2px 0 10px}
/* ---- 两融 / 做T 战果表（战果段暂不显示，样式保留） ---- */
.trow{display:grid;grid-template-columns:1fr 96px 50px 66px;gap:6px;align-items:center;padding:7px 14px;font-size:12.5px;color:var(--ink2);border-top:1px dashed var(--soft)}
.trow .nm2{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:700}
.trow .nm2 code{font-family:var(--mono);font-size:11.5px;color:var(--ink3);font-weight:600;margin-left:4px}
.trow .cst{font-family:var(--mono);font-size:12.5px;text-align:right;font-weight:700;font-variant-numeric:tabular-nums}
.trow .bk{font-family:var(--mono);font-size:12px;text-align:right;color:var(--ink3);font-variant-numeric:tabular-nums}
.trow .mk2{font-size:10.5px;color:#9a6a08;background:#fdf3e0;border-radius:4px;padding:0 4px;margin-left:4px;font-weight:800}
/* ---- 实时快讯：要点 + 折叠明细 ---- */
.nw{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.dg{background:#f7faff;border:1px solid #e2eaf8;border-radius:11px;padding:2px 14px 8px;margin-bottom:9px}
.dg .dgh{font-size:12.5px;font-weight:800;letter-spacing:.1em;color:#1d4ed8;padding:9px 0 2px}
.dg .di{display:grid;grid-template-columns:52px 1fr;gap:9px;padding:8px 0;border-top:1px dashed #e4ecfa}
.dg .di:first-of-type{border-top:0}
.dg .dm{font-family:var(--mono);font-size:12px;color:var(--ink3);font-weight:700;padding-top:1px;white-space:nowrap}
.dg .dt{font-size:14.5px;color:var(--ink2);line-height:1.65}
.dg .dt b{color:var(--ink);font-weight:800}
.nwr{display:grid;grid-template-columns:52px 1fr;gap:10px;padding:9px 14px;border-top:1px dashed var(--soft);font-size:14px;line-height:1.65;color:var(--ink2)}
.nwr:first-child{border-top:0}
.nwr .nwt{font-family:var(--mono);font-size:12px;color:var(--ink3);line-height:1.45;padding-top:2px;font-variant-numeric:tabular-nums}
.nwr.hot{background:#fffdf6}
.nwr .tag2{font-size:10.5px;color:#9a6a08;background:#fdf3e0;border-radius:4px;padding:1px 5px;margin-right:5px;font-weight:800}
.nwr .nws{font-style:normal;color:var(--ink3);font-size:11.5px;margin-left:5px}
details.nwd{border-top:1px solid var(--soft)}
details.nwd summary{padding:11px 14px;font-size:13px;font-weight:700;color:var(--ink2);cursor:pointer;list-style:none}
details.nwd summary::-webkit-details-marker{display:none}
details.nwd summary::after{content:" ⌄";color:var(--ink3)}
details.nwd[open] summary::after{content:" ⌃"}
/* ---- 解读卡 ---- */
.reads{display:grid;gap:9px}
.read{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.read .tg{display:inline-block;font-size:11px;font-weight:800;letter-spacing:.4px;color:#7c5209;background:#fdf3e0;border-radius:4px;padding:2px 7px;margin-bottom:7px}
.read .tg.blue{color:#1d4ed8;background:#eaf0fe}
.read .tg.green{color:#0d8f66;background:var(--downbg)}
.read h3{font-size:15.5px;font-weight:800;line-height:1.5;margin-bottom:5px}
.read p{font-size:14px;color:var(--ink2);line-height:1.8}
.read p+p{margin-top:6px}
.read b{color:var(--ink)}
.read .src{font-size:11.5px;color:var(--ink3);margin-top:7px}
/* ---- 底部吸底条 ---- */
.dock{position:fixed;left:0;right:0;bottom:0;z-index:60;background:rgba(255,255,255,.97);backdrop-filter:blur(10px);border-top:1px solid var(--line);padding:10px 14px calc(10px + env(safe-area-inset-bottom))}
.dock .in{max-width:680px;margin:0 auto;display:flex;align-items:center;gap:10px}
.dock .l{font-size:12px;color:var(--ink3);line-height:1.4}
.dock .l b{display:block;font-size:14.5px;color:var(--ink);font-weight:800}
.dock a{margin-left:auto;text-decoration:none;background:var(--ink);color:#fff;font-size:13.5px;font-weight:800;padding:9px 16px;border-radius:9px;white-space:nowrap}
@media(max-width:380px){.grid,.quad{grid-template-columns:1fr}.h-px{font-size:40px}.dg .di{grid-template-columns:1fr}.dg .dm{padding-top:0}}
"""

# ------------------------------------------------------------------ 工具
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


def cls(v):
    return "up" if v > 0 else "down" if v < 0 else "flat"


def sign(v, d=2):
    return f"{v:+.{d}f}%"


# KDAS 六状态 → 该状态下的动作口径（readme §4.2）
STATE_ADVICE = {
    "自然寻底": "0–1 成，不买不接刀",
    "右侧买点": "建仓信号，不做T",
    "震荡区间": "可做T：区间上下沿高抛低吸",
    "右侧空间": "可加仓，不做T",
    "右侧空间·窄": "减仓区，不追不买",
    "右侧趋势": "持有不加，破位即减",
    "右侧追涨": "0 成，不参与",
}


# 六状态配色：寻底=绿(弱) 买点/空间窄=橙(过渡/减仓) 空间/趋势=红(强)
ST_CLS = {"自然寻底": "b", "右侧买点": "c", "右侧空间": "d",
          "右侧空间·窄": "c", "右侧趋势": "d", "右侧追涨": "b"}


def short_name(s):
    return s.replace("(待确认)", "").replace("汇添富", "").strip()


def read_text(path):
    """Windows 下 brief.py 重定向输出可能是 GBK，逐编码兜底"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def section(text, start, end):
    """截取「一、xxx」到「二、xxx」之间的内容"""
    m = re.search(r"^\s*" + re.escape(start) + r"[、\s].*?$", text, re.M)
    if not m:
        return ""
    tail = text[m.end():]
    n = re.search(r"^\s*" + re.escape(end) + r"[、\s]", tail, re.M)
    return tail[:n.start()] if n else tail


# ------------------------------------------------------------------ 解析
def parse_common(text):
    m = re.search(r"【KDAS\s*(早盘|午盘)面板】(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", text)
    return {"date": m.group(2) if m else datetime.now().strftime("%Y-%m-%d"),
            "time": m.group(3) if m else "",
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
    d["indices"] = parse_indices(section(text, "一", "二"))
    d["quotes"] = parse_noon_quote(section(text, "二", "三"))
    d["flows"] = parse_flow(section(text, "三", "四"))
    d["intraday"] = parse_intraday(section(text, "四", "五"))
    d["levels"] = parse_levels(section(text, "五", "六"))
    m = re.search(r"六、异动提示\s*\n(.*?)(?=^\s*(?:[一二三四五六七八九十]+、|附：)|\Z)", text, re.S | re.M)
    d["alerts_raw"] = " ".join(x.strip() for x in (m.group(1).split("；") if m else []) if x.strip())
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
    d["us"] = parse_indices(section(text, "一", "二"))
    d["asia"] = parse_indices(section(text, "二", "三"))
    d["levels"] = parse_morning_levels(section(text, "三", "四"))
    d["focus"] = parse_focus(section(text, "四", "五"))
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
def render_indices(items, show_amount=True):
    if not items:
        return '<div class="empty">指数数据未取到</div>'
    s = ['<div class="grid">']
    for it in items:
        c = cls(it["pct"])
        am = f'<div class="am">{E(it["amount"])}</div>' if show_amount and it.get("amount") else ""
        s.append(f'<div class="idx"><div class="nm">{E(it["name"])}</div>'
                 f'<div><span class="px">{E(it["price"])}</span>'
                 f'<span class="ch {c}">{sign(it["pct"])}</span></div>{am}</div>')
    s.append("</div>")
    return "".join(s)


def render_actions(actions):
    if not actions:
        return ('<div class="empty"><b>今日不做 T</b>'
                '没有同时通过 KDAS 状态与过滤条件的标的，空手也是一种操作。</div>')
    s = []
    for i, a in enumerate(actions[:3], 1):
        side = "sell" if a.get("side") == "sell" else "buy"
        badge = E(a.get("direction", "做T"))
        tgt = a.get("target", "—")
        inv = a.get("invalid", "—")
        dead = f'<div class="t-dead">⏱ {E(a["deadline"])}</div>' if a.get("deadline") else ""
        note = f'<div class="t-note">📌 {E(a["note"])}</div>' if a.get("note") else ""
        s.append(f'''
<div class="ticket {side}" id="a{i}">
  <div class="t-bar"></div>
  <div class="t-head">
    <div class="t-step">{i}</div>
    <div class="t-name"><div class="n">{E(a.get("name",""))}<span>{E(a.get("code",""))}</span></div>
      <div class="d">定位：{badge}</div></div>
    <div class="t-badge">{badge}</div>
  </div>
  <div class="t-hero">
    <div class="h-lab"><span class="dot">{'买' if side=='buy' else '卖'}</span>触发价</div>
    <div class="h-px">{E(a.get("trigger","—"))}</div>
    <div class="h-qty">× {E(a.get("shares",""))} <span>挂单等它来，不追价</span></div>
  </div>
  <div class="t-sub">
    <div class="s-cell"><div class="s-lab">卖出 / 回补价</div><div class="s-px tgt">{E(tgt)}</div></div>
    <div class="s-cell"><div class="s-lab">失效 / 作废价</div><div class="s-px stp">{E(inv)}</div></div>
  </div>
  {dead}
  <div class="t-why">{a.get("why","")}</div>
  {note}
</div>''')
    return "".join(s)


def render_flow_bar(fl):
    if not fl:
        return "<div class='fl-n'>无资金流数据</div>"
    vals = [fl["main"], fl["huge"], fl["big"], fl["mid"], fl["small"]]
    mx = max(abs(v) for v in vals) or 1
    labels = [("主力", "main"), ("超大单", "huge"), ("大单", "big"), ("中单", "mid"), ("小单", "small")]
    s = ['<div class="fl"><div class="fl-t">资金流 · <b>' + E(fl["tag"]) + '</b></div>']
    for lb, k in labels:
        v = fl[k]
        c = "p" if v >= 0 else "n"
        w = abs(v) / mx * 50
        bar = (f'<span class="half l"><i class="bar {c}" style="width:{w:.1f}%"></i></span>'
               f'<span class="half r"></span>' if v < 0 else
               f'<span class="half l"></span>'
               f'<span class="half r"><i class="bar {c}" style="width:{w:.1f}%"></i></span>')
        color = "up" if v >= 0 else "down"
        disp = f"{v/1e8:+.2f}亿" if abs(v) >= 1e8 else f"{v/1e4:+.0f}万"
        s.append(f'<div class="fl-r"><span class="lb">{lb}</span>'
                 f'<span class="dual">{bar}</span>'
                 f'<span class="vl {color}">{disp}</span></div>')
    s.append('<div class="fl-n">红=净流入，绿=净流出；左半轴为流出，右半轴为流入</div></div>')
    return "".join(s)


def render_holdings_noon(d, decision):
    fmap = {x["name"]: x for x in d["flows"]}
    action_names = {a.get("name", "") for a in decision.get("actions", [])}
    s = []
    for q in d["quotes"]:
        nm = q["name"]
        fl = fmap.get(nm)
        intra = d["intraday"].get(nm, {})
        lv = d["levels"].get(nm, {})
        main = fl["main"] if fl else 0
        mc = ("in" if main > 0 else "out" if main < 0 else "") if fl else ""
        mt = (f"{main/1e8:+.2f}亿" if abs(main) >= 1e8 else f"{main/1e4:+.0f}万") if fl else "—"
        pc = cls(q["pct"])
        pos_cls = "a" if q["pos"] == "均价上方" else "b" if q["pos"] == "均价下方" else ""
        pos_short = "均上" if q["pos"] == "均价上方" else "均下" if q["pos"] == "均价下方" else "—"
        pos = intra.get("pos", 50)
        track = "hot" if pos > 80 or pos < 20 else ""
        openit = " open" if nm in action_names else ""
        code = next((a.get("code", "") for a in decision.get("actions", []) if a.get("name") == nm), "")
        if not code:
            code = next((x.get("code", "") for x in decision.get("skip", []) if x.get("name") == nm), "")
        s.append(f'''
<details class="h"{openit}>
  <summary>
    <span class="sm-name">{E(nm)}</span>{f'<span class="sm-code">{E(code)}</span>' if code else ''}
    <span class="sm-px">{E(q["price"])}</span>
    <span class="sm-ch {pc}">{sign(q["pct"])}</span>
    <span class="sm-pos {pos_cls}">{pos_short}{pos}%</span>
    <span class="sm-main {mc}">主力 {mt}</span><span class="sm-arrow">▼</span>
  </summary>
  <div class="h-body">
    <div class="kv">
      <div>当日均价<b>{E(q["avg"])}</b></div>
      <div>均价位置<b>{E(q["pos"])}</b></div>
      <div>换手 / 量比<b>{E(q["turn"])} / {E(q["vr"])}</b></div>
      <div>成交额<b>{E(q["amount"])}</b></div>
      <div>支撑 / 压力<b>{E(lv.get("sup","—"))} / {E(lv.get("res","—"))}</b></div>
      <div>KDAS 状态<b>{E(lv.get("state","—"))}</b></div>
      <div>强弱分界<b>{E(lv.get("weak","—"))}{" ↑" if lv.get("weak_dir")=="up" else (" ↓" if lv.get("weak_dir")=="down" else "")}</b></div>
      <div>状态口径<b style="font-size:12.5px">{E(STATE_ADVICE.get(lv.get("state",""),"—"))}</b></div>
      <div>日内振幅<b>{E(intra.get("amp","—"))}</b></div>
      <div>量能对比<b style="font-size:11px">{E(intra.get("vol","—"))}</b></div>
    </div>
    <div class="pos-lab"><span>日内位置</span><b>{pos}% · {E(intra.get("vol","—"))}</b></div>
    <div class="track"><i class="{track}" style="width:{pos}%"></i></div>
    {render_flow_bar(fl)}
  </div>
</details>''')
    return "".join(s) or '<div class="empty">持仓数据未取到</div>'


def render_holdings_morning(d, decision):
    action_names = {a.get("name", "") for a in decision.get("actions", [])}
    s = []
    for lv in d["levels"]:
        nm = lv["name"]
        code = next((a.get("code", "") for a in decision.get("actions", []) if a.get("name") == nm), "")
        if not code:
            code = next((x.get("code", "") for x in decision.get("skip", []) if x.get("name") == nm), "")
        openit = " open" if nm in action_names else ""
        pnl_c = cls(lv["pnl"])
        focus = d["focus"].get(nm, "")
        try:
            sup, res = float(lv["sup"]), float(lv["res"])
        except ValueError:      # 「—」= 创新高无压力 / 全线上方无支撑
            sup, res = 0.0, 0.0
        space_pct = (res - sup) / sup * 100 if sup else 0
        ok = space_pct >= 1.5 and num(lv["amp5"]) >= 1.8
        st = lv.get("state", "")
        # 六状态决定能不能做T：只有震荡区间可做（readme §9.4）
        if st == "震荡区间" and ok:
            badge = '<span class="sm-pos a">震荡区间 · 可做T</span>'
        elif st == "震荡区间":
            badge = '<span class="sm-pos b">震荡区间 · 空间不足</span>'
        else:
            badge = f'<span class="sm-pos {ST_CLS.get(st, "b")}">{E(st or "—")} · 不做T</span>'
        s.append(f'''
<details class="h"{openit}>
  <summary>
    <span class="sm-name">{E(nm)}</span>{f'<span class="sm-code">{E(code)}</span>' if code else ''}
    <span class="sm-px">{E(lv["price"])}</span>
    <span class="sm-ch {pnl_c}">{sign(lv["pnl"])}</span>
    {badge}<span class="sm-arrow">▼</span>
  </summary>
  <div class="h-body">
    <div class="kv">
      <div>成本 / 浮盈<b>{E(lv["cost"])} · {sign(lv["pnl"])}</b></div>
      <div>参考价<b>{E(lv["price"])}</b></div>
      <div>支撑<b>{E(lv["sup"])}</b></div>
      <div>压力<b>{E(lv["res"])}</b></div>
      <div>T 空间<b>{E(lv["space"])}</b></div>
      <div>5 日均振幅<b>{E(lv["amp5"])}</b></div>
      <div>KDAS 状态<b>{E(st or "—")}</b></div>
      <div>强弱分界<b>{E(lv.get("weak","—"))}{" ↑" if lv.get("weak_dir")=="up" else (" ↓" if lv.get("weak_dir")=="down" else "")}</b></div>
      <div>状态口径<b style="font-size:11px">{E(STATE_ADVICE.get(st,"—"))}</b></div>
    </div>
    {f'<div class="fl-n" style="margin-top:9px">📌 {E(focus)}</div>' if focus else ''}
  </div>
</details>''')
    return "".join(s) or '<div class="empty">持仓数据未取到</div>'


def render_quadrant(d):
    """按 价格×主力 自动归四象限，完全由脚本算，不需要人工判断"""
    fmap = {x["name"]: x for x in d["flows"]}
    groups = {"强": [], "出货": [], "洗盘": [], "真弱": []}
    missing = []
    for q in d["quotes"]:
        fl = fmap.get(q["name"])
        # 资金流缺失不能当 0 处理，否则会被误判成「真弱」或「拉高出货」
        if not fl:
            missing.append(q["name"])
            continue
        above = q["pos"] == "均价上方"
        main = fl["main"]
        k = ("强" if main > 0 else "出货") if above else ("洗盘" if main > 0 else "真弱")
        groups[k].append(q["name"])
    cfg = [("强", "均价上方 · 主力流入", "回踩均价做正T，不追高", "hot"),
           ("出货", "均价上方 · 主力流出", "拉高出货嫌疑，冲高倒T不补", "hot"),
           ("洗盘", "均价下方 · 主力流入", "等站回均价再正T，不抢", "cold"),
           ("真弱", "均价下方 · 主力流出", "反弹减仓，不补；硬禁正T", "cold")]
    s = ['<div class="quad">']
    for key, title, desc, tone in cfg:
        chips = "".join(f'<span class="chip">{E(n)}</span>' for n in groups[key]) or '<span class="chip">—</span>'
        s.append(f'<div class="q {tone}"><div class="qt"><span class="k">{E(key)}</span>{E(title)}</div>'
                 f'<div class="chips">{chips}</div><div class="qd">{E(desc)}</div></div>')
    s.append("</div>")
    if missing:
        s.append('<div class="fl-n" style="margin-top:8px">资金流缺失，不参与象限归类（'
                 + E("、".join(missing)) + '）</div>')
    return "".join(s)


def _avail(*xs):
    """过滤取数失败的 None，避免把缺失当成 0% 参与判断"""
    return [x for x in xs if x is not None]


def render_signals(d):
    """早盘映射矩阵：按 strategy_rules §3.2 自动标注命中项"""
    us = {x["name"]: x["pct"] for x in d["us"]}
    asia = {x["name"]: x["pct"] for x in d["asia"]}
    rows = []
    nasdaq, spx = us.get("纳斯达克"), us.get("标普500")
    hsi_f = asia.get("恒指期货")
    dxy = us.get("美元指数")
    nikkei, kospi = asia.get("日经225"), asia.get("韩国KOSPI")

    def add(kind, title, body):
        rows.append(f'<div><span class="k {kind}">{E(title)}</span>{body}</div>')

    if nasdaq is not None and spx is not None:
        if nasdaq >= 1 or spx >= 1:
            add("", "美股大涨", f"纳指 {sign(nasdaq)}、标普 {sign(spx)} → <b>513500 高开概率大</b>，冲压力位做倒T，不追高。")
        elif nasdaq <= -1 or spx <= -1:
            add("g", "美股大跌", f"纳指 {sign(nasdaq)}、标普 {sign(spx)} → <b>513500 低开</b>，等站回均价再考虑正T。")
        else:
            add("n", "美股平淡", f"纳指 {sign(nasdaq)}、标普 {sign(spx)}，未触发 ±1% 阈值，<b>513500 当日不专门做</b>。")
    if hsi_f is not None:
        if hsi_f >= 1:
            add("", "恒指期货强", f"恒指期货 {sign(hsi_f)} → 513700 高开，浮盈厚，冲压力位先减（倒T）。")
        elif hsi_f <= -1:
            add("g", "恒指期货弱", f"恒指期货 {sign(hsi_f)} → <b>513700 只观察支撑，破位不接、不低吸</b>。")
        else:
            add("n", "恒指期货中性", f"恒指期货 {sign(hsi_f)}，未触发 ±1% 阈值。")
    if nikkei is not None and kospi is not None and (nikkei <= -2 or kospi <= -2):
        add("g", "亚洲避险", f"日经 {sign(nikkei)}、KOSPI {sign(kospi)} → <b>医疗 / 消费 / A500 当日不做正T</b>。")
    elif nikkei is None or kospi is None:
        sub = _avail(asia.get("日经期货"), hsi_f, asia.get("恒生指数"))
        txt = "、".join(f"{k} {sign(v)}" for k, v in
                       [("日经期货", asia.get("日经期货")), ("恒指期货", hsi_f),
                        ("恒生指数", asia.get("恒生指数"))] if v is not None)
        if sub and min(sub) <= -2:
            add("g", "亚洲避险", f"日经 / KOSPI 取数失败，改用 {txt} → <b>医疗 / 消费 / A500 当日不做正T</b>。")
        else:
            add("n", "亚洲情绪", f"日经 / KOSPI 取数失败，改用 {txt or '—'}，未触发 −2% 避险线。")
    else:
        add("n", "亚洲情绪", f"日经 {sign(nikkei)}、KOSPI {sign(kospi)}，未触发 −2% 避险线。")
    if dxy is not None and dxy >= 0.5:
        add("g", "美元走强", f"美元指数 {sign(dxy)} → 压制港股与新兴市场，<b>513700 只减不加</b>。")
    elif dxy is None:
        add("n", "美元", "美元指数取数失败，今日无 +0.5% 走强的证据。")
    else:
        add("n", "美元", f"美元指数 {sign(dxy)}，未触发 +0.5% 压制线。")
    # 方向分歧：美股与亚洲一涨一跌，规则 §3.2 要求 A 股标的全部观望
    us_dir = ((nasdaq or 0) + (spx or 0)) / 2
    asia_vals = _avail(nikkei, kospi)
    note = ""
    if not asia_vals:      # 日经/KOSPI 缺失时用期货与恒指替代，不能把缺失当 0
        asia_vals = _avail(asia.get("日经期货"), hsi_f, asia.get("恒生指数"))
        note = "（日经/KOSPI 取数失败，用日经期货与恒指代）"
    asia_dir = sum(asia_vals) / len(asia_vals) if asia_vals else 0
    if (us_dir < -0.2 and asia_dir > 0.2) or (us_dir > 0.2 and asia_dir < -0.2):
        add("g", "方向分歧", f"美股均值 {sign(us_dir)}、亚洲均值 {sign(asia_dir)}{note} 方向相反 → "
            "<b>只做港股/美股标的，医疗 / 消费 / A500 等 A 股标的当日观望</b>。")
    else:
        add("n", "方向一致", f"美股均值 {sign(us_dir)}、亚洲均值 {sign(asia_dir)}{note}，未构成方向分歧。")
    return '<div class="signal">' + "".join(rows) + "</div>"


def render_skip(skip):
    if not skip:
        return ""
    s = ['<div class="skip">']
    for x in skip:
        s.append(f'<div class="sr"><div class="sn">{E(x.get("name",""))}'
                 f'<code>{E(x.get("code",""))}</code></div>'
                 f'<div class="st">{x.get("why","")}</div></div>')
    s.append("</div>")
    return "".join(s)


def _lead(s):
    """把一条长文本切成「短标题 + 正文」：优先按冒号，其次按第一个逗号。
    切不出来就返回 (None, 原文)，渲染时按整段处理。"""
    t = str(s or "").strip()
    for sep in ("：", ":"):
        if sep in t:
            a, b = t.split(sep, 1)
            if 4 <= len(a) <= 30 and b.strip():
                return a.strip(), b.strip()
    if "，" in t:
        a, b = t.split("，", 1)
        if 8 <= len(a) <= 24 and b.strip():
            return a.strip(), b.strip()
    return None, t


def _item(x):
    """决策 JSON 里的一条，支持 {"t":标题,"d":正文} 或纯字符串"""
    if isinstance(x, dict):
        return (x.get("t") or "").strip() or None, (x.get("d") or "").strip()
    return _lead(x)


def render_risks(risks):
    """风险：一条一张小卡，短标题加粗，细节在下面，扫一眼就能读完。"""
    if not risks:
        return ""
    s = ['<div class="risk">']
    for i, r in enumerate(risks, 1):
        t, d = _item(r)
        if t:
            s.append(f'<div class="ri"><span class="nn">{i}</span><div>'
                     f'<div class="rt">{rich(t)}</div><div class="rd">{rich(d)}</div></div></div>')
        else:
            s.append(f'<div class="ri one"><span class="nn">{i}</span>'
                     f'<div class="rd">{rich(d)}</div></div>')
    s.append("</div>")
    return "".join(s)


def render_alerts(alerts):
    """待办：一行一条，冒号前当小标题，避免一坨长句。"""
    if not alerts:
        return ""
    s = ['<div class="todo">']
    for a in alerts:
        t, d = _item(a)
        body = (f'<b>{rich(t)}</b>　{rich(d)}' if t and d else rich(t or d))
        s.append(f'<div class="ti"><span class="b">▸</span><span>{body}</span></div>')
    s.append("</div>")
    return "".join(s)


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
    cut = None
    mt = re.search(r"([\d.]+)×（今日 ([\d,.]+)亿 / 基准 ([\d,.]+)亿，"
                   r"历史同时点已走完 ([\d.]+)%）→ (.+?)\s*$", rows.get("载具同时点", ""))
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


def _yi(v, d=2):
    if v is None:
        return "—"
    return f"{v/1e8:+.{d}f}亿" if abs(v) >= 1e8 else f"{v/1e4:+.0f}万"


def render_market(mk):
    if not mk:
        return ""
    fl = {"main": mk["main"] or 0, "huge": mk["huge"] or 0, "big": mk["big"] or 0,
          "mid": mk["mid"] or 0, "small": mk["small"] or 0,
          "tag": "两市（沪指 + 深成指口径）"}
    c = cls(mk["main"] or 0)
    pills = []
    for lb, k in (("5日", "main5"), ("10日", "main10"), ("20日", "main20")):
        if mk.get(k) is not None:
            pills.append(f'<span class="pill {cls(mk[k])}">{lb}主力 <b>{_yi(mk[k],0)}</b></span>')
    if mk.get("up") is not None:
        pills.append(f'<span class="pill">涨跌 <b>{mk["up"]}</b>/<b>{mk["down"]}</b></span>')
        pills.append(f'<span class="pill">涨停 <b>{mk["limit_up"]}</b> 跌停 <b>{mk["limit_dn"]}</b></span>')
    if mk.get("amount"):
        r = mk.get("amount_ratio")
        ad = mk.get("amount_date") or "上一交易日"
        pills.append(f'<span class="pill">{ad} 收盘 <b>%.0f</b>亿' % (mk["amount"] / 1e8)
                     + (f' = 20日均量 <b>{r:.0f}%</b>' if r else "") + "</span>")

    # 同时点成交量倍数：判断「国家队今天上没上班」的第一信号，分母按历史同时点进度缩放
    cut_html = ""
    cut = mk.get("cut")
    if cut:
        ct = "down" if cut["x"] < 0.85 else ("up" if cut["x"] >= 1.2 else "")
        cut_html = (f'<div class="cut"><span class="cx {ct}">{cut["x"]:.2f}×</span>'
                    f'<span class="ct">载具同时点成交量'
                    f'<br>今日 {cut["today"]/1e8:,.1f}亿 / 基准 {cut["bench"]/1e8:,.1f}亿'
                    f'（历史同时点已走完 {cut["prog"]:.1f}%）'
                    f'<br><b>{E(cut["tip"])}</b></span></div>')

    def _short(v):
        return f"{v/1e8:+.2f}亿" if abs(v) >= 1e8 else f"{v/1e4:+.0f}万"

    nat = ""
    if mk.get("fam"):
        chips = "".join(f'<i class="{cls(v)}">{E(f)} {_short(v)}</i>' for f, v in mk["fam"])
        nat = ('<div class="mb-sep"></div>' + plain("nat", tight=True) +
               f'<div class="mb-hd"><b>国家队载具</b>'
               f'<span>今日 <b>{_yi(mk["nat_main"])}</b></span>'
               f'<span>{mk["nat_n"]}只/{mk["nat_in"]}只流入</span>'
               f'<span>5日 {_yi(mk["nat5"],1)}</span>'
               f'<span>规模 {mk["nat_size"]/1e8:,.0f}亿</span></div>'
               f'<div class="nat">{chips}</div>')
        ex = mk.get("ext")
        if ex:
            nat += (f'<div class="mb-sub">观察池 {ex["n"]} 只 '
                    f'<b class="{cls(ex["main"])}">{_short(ex["main"])}</b>'
                    f'（{ex["in"]} 只净流入；2025 年报未见汇金持仓，只跟趋势）</div>')
        if mk.get("nat"):
            detail = "".join(f'<i class="{cls(v)}">{code} {_short(v)}</i>' for code, v in mk["nat"])
            nat += (f'<details class="natd"><summary>逐只明细 {len(mk["nat"])} 只</summary>'
                    f'<div class="nat">{detail}</div></details>')
    k01 = mk.get("k01")
    if k01:
        nat += (f'<div class="mb-sub">份额 {k01["date"]} 主池 {k01["pool"]} 只合计 '
                f'<b>{k01["shares"]/1e8:,.1f}亿份</b>，{E(k01["chg"])}'
                f'｜加仓指数 <b>{k01["index"]:.1f}</b></div>')

    return (plain("market") +
            f'<div class="mbox"><div class="mb-hd"><b>两市主力净流入</b>'
            f'<span>大单分档口径</span></div>'
            f'<div class="mb-main"><span class="v {c}">{_yi(mk["main"])}</span>'
            f'<span class="u">元</span></div>'
            f'<div class="mb-pills">{"".join(pills)}</div>'
            f'{cut_html}'
            f'<div class="mb-sep"></div>{render_flow_bar(fl)}{nat}</div>')


def render_margin(mg):
    """两融卡：两融余额大字 + 日/5日/10日净买入 + 杠杆方向 + 持仓两融融券。"""
    if not mg:
        return ""

    def _lv(v, d=0):            # 余额是存量，不能带 + 号（_yi 是给「变动」用的）
        return "—" if v is None else f"{v/1e8:,.{d}f}亿"

    c = cls(mg.get("rzjme") or 0)
    pills = []
    if mg.get("rzye"):
        pills.append(f'<span class="pill">融资余额 <b>{mg["rzye"]/1e8:,.0f}</b>亿</span>')
    if mg.get("zb"):
        pills.append(f'<span class="pill">占流通市值 <b>{mg["zb"]:.2f}%</b></span>')
    for lb, k in (("日净买", "rzjme"), ("5日", "rzjme5"), ("10日", "rzjme10")):
        if mg.get(k) is not None:
            pills.append(f'<span class="pill {cls(mg[k])}">{lb} <b>{_yi(mg[k], 0)}</b></span>')
    if mg.get("rqye"):
        pills.append(f'<span class="pill">融券 <b>{mg["rqye"]/1e8:,.0f}</b>亿</span>')
    idx = ""
    if mg.get("idx"):
        idx = (f'上证 {mg["idx"]:,.2f} '
               f'<b class="{cls(mg.get("zdf") or 0)}">{sign(mg.get("zdf") or 0)}</b>'
               f'｜5日 {sign(mg.get("zdf5") or 0)}｜10日 {sign(mg.get("zdf10") or 0)}')
    cut = (f'<div class="cut"><span class="cx {c}">{_yi(mg.get("rzjme"), 0)}</span>'
           f'<span class="ct">融资净买入（日）<br>{E(mg.get("dir") or "—")}'
           + (f'<br><b>{idx}</b>' if idx else "") + "</span></div>")
    pos = ""
    if mg.get("pos"):
        chips = "".join(
            f'<i class="{cls(x.get("rzjme") or 0)}">{x["code"]} {x["rzye"]/1e8:.2f}亿 '
            f'{(x.get("rzjme") or 0)/1e4:+.0f}万</i>' for x in mg["pos"])
        pos = ('<div class="mb-sub"><b>持仓两融</b> 融资余额 / 当日净买入'
               + (f'（{mg["pos_date"]}）' if mg.get("pos_date") else "")
               + f'</div><div class="nat" style="padding-top:0">{chips}</div>')
    note = f'<div class="mb-sub">{E(mg.get("note"))}</div>' if mg.get("note") else ""
    return (plain("margin") +
            f'<div class="mbox" style="margin-top:8px"><div class="mb-hd">'
            f'<b>融资融券（杠杆资金）</b>'
            f'<span>{mg.get("date") or "—"} 数据 · T+1 披露</span></div>'
            f'<div class="mb-main"><span class="v">{_lv(mg.get("rzrqye"))}</span>'
            f'<span class="u">两融余额</span></div>'
            f'<div class="mb-pills">{"".join(pills)}</div>'
            f'{cut}{pos}{note}</div>')


def render_tstats(ts):
    """做T 战果卡：已实现收益大字 + 往返统计 + 逐只降本 + 一次 T 的降本效率。"""
    if not ts:
        return ""
    c = cls(ts.get("realized") or 0)
    pills = []
    if ts.get("pct") is not None:
        pills.append(f'<span class="pill">占总资产 <b>{ts["pct"]:.2f}%</b></span>')
    pills.append(f'<span class="pill">完成往返 <b>{ts.get("rounds", 0)}</b> 次</span>')
    if ts.get("open_n"):
        pills.append(f'<span class="pill down">未结 <b>{ts["open_n"]}</b> 笔</span>')

    parts = ""
    if ts.get("parts"):
        chips = "".join(f'<i class="{cls(p["v"])}">{p["code"]} {p["v"]:+,.0f}</i>'
                        for p in ts["parts"])
        parts = (f'<div class="mb-sub"><b>已实现构成</b> 卖出价高于该笔成本的差价（未扣佣金）</div>'
                 f'<div class="nat" style="padding-top:0">{chips}</div>')
    if ts.get("open_note"):
        parts += (f'<div class="mb-sub" style="color:#9a6a08"><b>未结</b> '
                  f'{E(ts["open_note"])}</div>')

    head = ('<div class="trow" style="border-top:1px solid var(--soft);color:var(--ink3);'
            'font-size:10px;padding-top:8px"><div>标的</div><div class="cst">成本</div>'
            '<div class="bk">变动</div><div class="bk">距成本线</div></div>')
    body = []
    for r in ts.get("rows") or []:
        mk = '<span class="mk2">加仓摊薄</span>' if r.get("diluted") else ""
        mt = re.search(r"([+-][\d.]+%)", r.get("tail") or "")
        tc = cls(num(mt.group(1))) if mt else "flat"
        body.append(f'<div class="trow"><div class="nm2">{E(r["name"])}'
                    f'<code>{E(r["code"])}</code>{mk}</div>'
                    f'<div class="cst">{r["from"]}→{r["to"]}</div>'
                    f'<div class="bk">{r["chg"]:+.1f}%</div>'
                    f'<div class="bk {tc}">{E(mt.group(1)) if mt else "—"}</div></div>')

    eff = ""
    e = ts.get("eff")
    if e:
        eff = (f'<div class="mb-sub"><b>降本效率</b> {e["code"]} 一次 1.5% 倒T'
               f'（上限 {e["cap"]:,} 份）≈ {e["gain"]:+.0f} 元 ≈ 成本降 {e["drop"]:.4f}'
               f'｜回本涨幅每降 1 个百分点需 <b>{e["need"]:.1f}</b> 次成功 T</div>')
    note = f'<div class="mb-sub">{E(ts.get("note"))}</div>' if ts.get("note") else ""

    return (f'<div class="mbox"><div class="mb-hd"><b>做T 已实现收益</b>'
            f'<span>同一标的买卖差价口径</span></div>'
            f'<div class="mb-main"><span class="v {c}">{ts.get("realized") or 0:+,.2f}</span>'
            f'<span class="u">元</span></div>'
            f'<div class="mb-pills">{"".join(pills)}</div>'
            f'{parts}<div class="mb-sep"></div>{head}{"".join(body)}{eff}{note}</div>')


def render_news(nw, digest=None):
    """快讯卡：先给「要点」（决策 JSON 的 flash_digest，缺省自动取相关条目），
    明细整段折叠，避免一屏十几条标题把重点淹掉。"""
    if not nw:
        return ""
    rows = nw.get("rows") or []
    if not rows:
        return '<div class="empty">窗口内无快讯</div>'

    def hm(t):
        return (t or "").partition(" ")[2] or (t or "")

    digs = [{"m": str(d.get("m") or "").strip(),
             "t": d.get("t") or ""} for d in (digest or []) if d.get("t")]
    if not digs:                                   # 没写要点就自动收相关条目顶上
        digs = [{"m": hm(x["time"]), "t": x["title"]} for x in rows if x["hot"]][:4]

    head = ""
    if digs:
        items = "".join(
            f'<div class="di"><span class="dm">{E(d["m"])}</span>'
            f'<span class="dt">{rich(d["t"])}</span></div>' for d in digs)
        head = f'<div class="dg"><div class="dgh">要点 · 先看这 {len(digs)} 条</div>{items}</div>'

    show = [] if digs else [x for x in rows if x["hot"]][:4] or rows[:4]
    body = []
    for x in show:
        dd, _, tt = x["time"].partition(" ")
        tag = '<span class="tag2">相关</span>' if x["hot"] else ""
        body.append(f'<div class="nwr{" hot" if x["hot"] else ""}">'
                    f'<span class="nwt">{E(dd)}<br>{E(tt)}</span>'
                    f'<span>{tag}{E(x["title"])}<i class="nws">{E(x["src"])}</i></span></div>')

    rest = rows[len(show):]
    more = ""
    if rest:
        ri = []
        for x in rest:
            dd, _, tt = x["time"].partition(" ")
            tag = '<span class="tag2">相关</span>' if x["hot"] else ""
            ri.append(f'<div class="nwr{" hot" if x["hot"] else ""}">'
                      f'<span class="nwt">{E(dd)}<br>{E(tt)}</span>'
                      f'<span>{tag}{E(x["title"])}<i class="nws">{E(x["src"])}</i></span></div>')
        lab = (f'展开全部 {len(rest)} 条原始快讯' if not show
               else f'展开其余 {len(rest)} 条 · 共 {len(rows)} 条')
        more = f'<details class="nwd"><summary>{lab}</summary>{"".join(ri)}</details>'

    note = f'<div class="fl-n" style="margin:10px 14px 12px">{E(nw["note"])}</div>' if nw.get("note") else ""
    return head + '<div class="nw">' + "".join(body) + more + note + "</div>"


def rich(s):
    """只放行 <b> 与 <br>，其余转义"""
    t = html.escape(str(s), quote=False)
    for tag in ("b", "/b", "br", "br/"):
        t = t.replace(f"&lt;{tag}&gt;", f"<{tag}>")
    return t


# ------------------------------------------------------------------ 大白话
# 只解释「这个指标在说什么、怎么读」，不写任何具体数字（数字一律由脚本从
# brief.py 解析），避免两处口径打架。2026-11-17 用户要求：看不懂技术指标的
# 情况下也要一眼明白。
PLAIN = {
    "market": ("主力资金 = 大钱在买还是在卖",
               "「主力」指单笔金额大的机构委托（超大单 + 大单）。<b>净流入</b>＝大钱在买，"
               "跌下去有人接；<b>净流出</b>＝大钱在卖，反弹容易被砸回去。"
               "如果超大单在卖、小单（散户）在买，就是「机构出货、散户接盘」，这种时候别抄底。"),
    "nat": ("国家队 = 汇金这类资金买的宽基 ETF",
            "看的是这些「载具」的<b>场内成交</b>方向：净流入说明有资金在托，净流出说明在退。"
            "注意这<b>不等于申购份额</b>——份额要单独看份额指数，两个背离时以份额为准。"),
    "margin": ("两融 = 借钱炒股的钱",
               "融资余额<b>涨</b>＝有人加杠杆进场，行情有推力；<b>跌</b>＝在还钱离场，跌起来没人接。"
               "它比行情慢一天（T+1 才披露），所以只看方向，不当日内信号用。"),
    "impulse": ("信贷脉冲 = 全国借钱的速度，比一年前快了还是慢了",
                "经济里「借钱」变快，半年后股市往往走强；变慢，就要弱一阵。"
                "它是<b>月度</b>数据、只管方向不管点位：负值＝还在收缩，这种阶段<b>只减不加</b>，"
                "做 T 也只做卖的那一边。"),
    "ratio": ("股债比价 = 同样一笔钱，买股票比买债券贵多少",
              "这个数<b>越低</b>说明股票相对便宜、越该动手；<b>越高</b>说明股票贵、该减。"
              "1.00 附近是「便宜到值得买」的扳机，1.50 附近是「贵到该减」。"
              "它跟债券利率一起动，所以只当温度计，不当买卖信号。"),
    "kdas": ("状态 = 这只票现在处在哪个阶段，决定「能不能做 T」",
             "<b>自然寻底</b>：还在跌，别接刀；<b>右侧买点</b>：刚转强，是建仓信号不是做 T 信号；"
             "<b>震荡区间</b>：上下有边界，<b>唯一能做 T 的</b>；<b>右侧空间</b>：还能涨，可加仓；"
             "<b>右侧空间·窄</b>：快到压力了，是卖点；<b>右侧趋势</b>：一路涨，拿着别动。"),
    "quad": ("四象限 = 价格在均价上还是下 × 大钱在买还是卖",
             "四个格子四种打法：<b>价上 + 资金进</b>＝强势，回踩均价做正T；"
             "<b>价上 + 资金出</b>＝拉高出货，冲高做倒T；<b>价下 + 资金进</b>＝洗盘，等站回均价；"
             "<b>价下 + 资金出</b>＝真弱，反弹减仓不补。"),
    "news": ("快讯 = 刚刚发生了什么，先看要点就够了",
             "要点是帮你抓重点，原始条目折叠在下面，想核随时点开。"
             "真正决定动不动手的是上面的价格、资金、两融和状态，消息只当情绪参考。"),
}


def plain(key, tight=False):
    """渲染一条大白话解释条；key 不在表里就返回空串。"""
    it = PLAIN.get(key)
    if not it:
        return ""
    lab, txt = it
    st = ' style="margin:10px 14px 8px"' if tight else ""
    return (f'<div class="plain"{st}><span class="pl">一句话看懂 · {E(lab)}</span>'
            f'{rich(txt)}</div>')


def render_impulse(im):
    """K05 信贷脉冲卡：白话条 + 脉冲大字 + 四格 + 一句结论，口径细节折叠。"""
    if not im:
        return ""
    p = im.get("p") or 0
    c = "down" if p < 0 else "up"

    def cell(label, v, sub, cls=""):
        return ('<div class="rcell"><span class="t">' + E(label) +
                f'</span><span class="v {cls}">{E(v)}</span>'
                f'<span class="d">{E(sub)}</span></div>')

    pe = f"{im['p_ex']:+.2f}" if im.get("p_ex") is not None else "—"
    pe_c = "down" if (im.get("p_ex") or 0) < 0 else "up"
    yoy = f"{im['yoy']:+.1f}%" if im.get("yoy") is not None else "—"
    yoy_c = "down" if (im.get("yoy") or 0) < 0 else "up"
    k = f"{im['k']:.0f}" if im.get("k") is not None else "—"
    kr = f"月 · r={im['r']:+.2f}" if im.get("r") is not None else ""
    cells = (cell("含政府债", f"{p:+.2f}", "pp · 总量", c) +
             cell("剔除政府债", pe, "pp · 私人部门", pe_c) +
             cell("沪深300同比", yoy, im.get("px_tag") or "", yoy_c) +
             cell("领先期", k, kr))

    src = im.get("src") or ""
    mm = re.match(r"(\d{4}-\d{2})", src)
    detail = (f'<details class="natd" style="margin-top:2px"><summary>口径与拐点</summary>'
              f'<div class="mb-sub"><b>拐点</b> {E(im.get("turn") or "")}'
              f' · {E(im.get("turn_why") or "")}</div>'
              f'<div class="mb-sub">{E(src)}</div></details>')
    return (plain("impulse") +
            '<div class="mbox"><div class="mb-hd"><b>信贷脉冲</b>'
            '<span>社融存量同比差分 · 月频</span>'
            f'<span>{E(mm.group(1) if mm else "")}</span></div>'
            f'<div class="mb-main"><span class="v {c}">{p:+.2f}</span>'
            f'<span class="u">pp · {E(im.get("band") or "")}</span></div>'
            f'<div class="rgrid">{cells}</div>'
            f'<div class="mb-sub" style="color:var(--ink);font-weight:800;font-size:14.5px;padding-top:11px">'
            f'→ {E(im.get("act") or "")}</div>'
            f'{detail}</div>')


def render_ratio(r):
    """K04 股债比价卡：走廊进度条 + 两端点位 + 对持仓的含义。"""
    if not r:
        return ""
    lo = {x["k"]: x for x in r["lower"]}
    up = {x["k"]: x for x in r["upper"]}
    p_lo = (lo.get("1.00") or {}).get("p")
    p_hi = (up.get("1.50") or {}).get("p")
    px = r.get("px") or 0
    frac = r.get("corr") or 0
    if p_lo and p_hi and p_hi > p_lo and px:
        frac = max(0.0, min(100.0, (px - p_lo) / (p_hi - p_lo) * 100))

    def cell(label, item):
        if not item:
            return ('<div class="rcell"><span class="t">' + label +
                    '</span><span class="v">—</span><span class="d">未取到</span></div>')
        c = "down" if item["d"] < 0 else "up"
        return ('<div class="rcell"><span class="t">' + label +
                f'</span><span class="v">{item["p"]:,.0f}</span>'
                f'<span class="d {c}">{item["d"]:+.1f}%</span></div>')

    cells = (cell("1.00 触线 · 买", lo.get("1.00")) +
             cell("1.30 偏贵", up.get("1.30")) +
             cell("1.50 贵 · 减股", up.get("1.50")) +
             '<div class="rcell"><span class="t">股债走廊</span>'
             f'<span class="v">{frac:.0f}%</span>'
             '<span class="d">0% 买 · 100% 减</span></div>')

    ratio = r.get("ratio") or 0
    band = E(r.get("band") or "")
    bondv = r.get("bond") or 0
    c0 = "down" if ratio < 1.05 else ("up" if ratio >= 1.30 else "")
    bt = " ｜ ".join(f"{c} {v}%" for c, v in (r.get("bond_t") or []))
    deep = []
    if lo.get("0.97"):
        deep.append(f'0.97 线 {lo["0.97"]["p"]:,.0f}（{lo["0.97"]["d"]:+.1f}%）')
    if lo.get("0.95"):
        deep.append(f'0.95 线 {lo["0.95"]["p"]:,.0f}（{lo["0.95"]["d"]:+.1f}%）')
    detail = ""
    if bt or deep or r.get("hold") or r.get("src"):
        inner = ""
        if bt:
            inner += (f'<div class="mb-sub"><b>债端温度</b> {E(bt)}'
                      '（国债ETF 52周分位，越高越贵；这条线答不了「债本身贵不贵」）</div>')
        if deep:
            inner += f'<div class="mb-sub"><b>更深的线</b> {" ｜ ".join(E(x) for x in deep)}</div>'
        if r.get("hold"):
            inner += f'<div class="mb-sub"><b>对持仓</b> {E(r.get("hold"))}</div>'
        if r.get("src"):
            inner += f'<div class="mb-sub">{E(r["src"])}</div>'
        detail = (f'<details class="natd" style="margin-top:2px"><summary>更多口径</summary>'
                  f'{inner}</details>')
    return (plain("ratio") +
            '<div class="mbox"><div class="mb-hd"><b>股债比价</b>'
            '<span>沪深300全收益 ÷ 二级债基</span>'
            f'<span>债腿 {bondv:,.0f}</span></div>'
            f'<div class="mb-main"><span class="v {c0}">{ratio:.3f}</span>'
            f'<span class="u">倍 · {band}</span></div>'
            '<div class="rbar"><div class="tk"></div>'
            f'<div class="pt" style="left:{frac:.1f}%"><i></i><b>{px:,.0f}</b></div>'
            f'<div class="en l"><b>{p_lo or 0:,.0f}</b>触线 · 买</div>'
            f'<div class="en r"><b>{p_hi or 0:,.0f}</b>偏贵 · 减</div></div>'
            f'<div class="rgrid">{cells}</div>'
            f'<div class="mb-sub" style="color:var(--ink);font-weight:800;font-size:14.5px;padding-top:11px">'
            f'→ {E(r.get("hint") or "")}</div>'
            f'{detail}</div>')


def render_reads(reads):
    if not reads:
        return ""
    out = ['<div class="reads" style="margin-top:8px">']
    for r in reads:
        tone = r.get("tone", "")
        src = f'<div class="src">{E(r["src"])}</div>' if r.get("src") else ""
        body = "".join(f"<p>{rich(p)}</p>" for p in (r.get("body") or []))
        out.append(f'<div class="read"><span class="tg {E(tone)}">{E(r.get("tag", ""))}</span>'
                   f'<h3>{rich(r.get("title", ""))}</h3>{body}{src}</div>')
    out.append("</div>")
    return "".join(out)


# ------------------------------------------------------------------ 主渲染
def render(mode, d, decision):
    is_noon = mode == "noon"
    date = decision.get("date") or d["date"]
    title = "午盘做T计划" if is_noon else "早盘做T计划"
    subtitle = decision.get("subtitle") or (
        f'{d["time"]} · 东财公开接口' if is_noon else '08:30 · 东财 / 新浪公开接口')
    actions = decision.get("actions", [])
    verdict = decision.get("verdict", "—")
    skip = decision.get("skip", [])

    # 数据区
    if is_noon:
        idx_html = render_indices(d["indices"])
        # 两市 = 沪市 + 深市；创业板 / 科创50 是成分指数，成交额已被包含，不能重复累加
        two_mkt = sum(num(x["amount"].replace("亿", ""))
                      for x in d["indices"][:2] if x.get("amount"))
        data_sections = f'''
<section id="market"><div class="sec-head"><span class="n">2</span><h2>上午大盘</h2>
  <span class="hint">两市 {two_mkt:,.0f} 亿</span></div>
{idx_html}</section>
<section id="quad"><div class="sec-head"><span class="n">3</span><h2>四象限</h2>
  <span class="hint">价格 × 主力资金</span></div>{plain("quad")}{render_quadrant(d)}</section>
<section id="hold"><div class="sec-head"><span class="n">4</span><h2>持仓明细</h2>
  <span class="hint">点开看资金结构</span></div>{render_holdings_noon(d, decision)}</section>'''
    else:
        idx_html = ("<div class='sec-head'><span class='n'>2</span><h2>隔夜美股</h2></div>" +
                    render_indices(d["us"], False) +
                    "<div class='sec-head' style='margin-top:16px'><span class='n'>3</span><h2>亚洲早盘</h2>"
                    "<span class='hint'>07:00–08:30 参考</span></div>" +
                    render_indices(d["asia"], False))
        data_sections = f'''
<section id="market">{idx_html}</section>
<section id="sig"><div class="sec-head"><span class="n">4</span><h2>映射矩阵</h2>
  <span class="hint">规则自动命中</span></div>{render_signals(d)}</section>
<section id="hold"><div class="sec-head"><span class="n">5</span><h2>持仓关键位</h2>
  <span class="hint">持仓市值 {E(d.get("total") or "—")} 元</span></div>
{plain("kdas")}{render_holdings_morning(d, decision)}</section>'''

    n_skip = len(skip)
    alerts = decision.get("alerts", [])

    # 资金面解读：数据卡由 brief.py 自动渲染（大盘资金 + 两融），文字解读由决策 JSON 的 reads 提供
    reads = decision.get("reads", [])
    mk = d.get("market")
    mg = d.get("margin")
    seq = 5 if is_noon else 6          # 编号起点：K05 信贷脉冲 → K04 股债比价
    imp_html = ""
    im = d.get("impulse")
    if im:
        imp_html = (f'<section id="imp"><div class="sec-head"><span class="n">{seq}</span>'
                    '<h2>信贷脉冲</h2><span class="hint">K05 · 社融存量同比差分</span>'
                    f'</div>{render_impulse(im)}</section>')
        seq += 1
    ratio_html = ""
    rt = d.get("ratio")
    if rt:
        ratio_html = (f'<section id="ratio"><div class="sec-head"><span class="n">{seq}</span>'
                      '<h2>股债比价</h2><span class="hint">K04 · 全收益 ÷ 二级债基</span>'
                      f'</div>{render_ratio(rt)}</section>')
        seq += 1
    blocks = [x for x in (render_market(mk), render_margin(mg), render_reads(reads)) if x]
    insight_html = ""
    if blocks:
        insight_html = f'''
<section id="fund"><div class="sec-head"><span class="n">{seq}</span>
  <h2>资金面解读</h2><span class="hint">大盘资金 · 两融 · 国家队 · 消息面</span></div>
{"".join(blocks)}</section>'''
    # 编号顺序推进，缺段自动收回，避免出现 4→5→7 的空号
    # 做T 战果段 2026-11-16 起不显示（用户要求只保留两融），parse_tstats / render_tstats 保留备用
    skip_no = seq + (1 if insight_html else 0)
    risk_no = skip_no + 1
    alerts_no = risk_no + 1
    alerts_html = (f'<section id="todo"><div class="sec-head"><span class="n">{alerts_no}</span>'
                   '<h2>待办与记录</h2></div>'
                   f'{render_alerts(alerts)}</section>') if alerts else ""

    # 实时快讯放最底部：最近几小时发生的事，决定「要不要现在动手」
    nw = d.get("news")
    news_html = ""
    if nw:
        news_no = alerts_no + (1 if alerts_html else 0)
        news_html = f'''
<section id="news"><div class="sec-head"><span class="n">{news_no}</span>
  <h2>实时快讯</h2><span class="hint">近 {nw["hours"]} 小时 · {E(nw["time"])} 更新</span></div>
{plain("news")}{render_news(nw, decision.get("flash_digest"))}</section>'''

    dock_px = actions[0].get("trigger", "—") if actions else "—"
    dock_txt = (f'<b>{len(actions)} 个动作 · {E(actions[0].get("name",""))} {E(dock_px)}</b>'
                f'其余 {n_skip} 只不动' if actions else "<b>今日不做 T</b>空手也是一种操作")
    dock_href = "#a1" if actions else "#skip"

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#f2f3f6"><title>{E(title)} · {E(date)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header>
  <div class="eyebrow">KDAS · 持仓做T · {'午间 12:30' if is_noon else '盘前 08:30'}</div>
  <h1>{E(title)}<em>/</em>{E(date)}</h1>
  <div class="meta"><i>{E(subtitle)}</i><i>数据来自 brief.py 脚本</i>
    <i>{len(actions)} 个动作</i><i>{n_skip} 只不动</i></div>
</header>

<div class="verdict"><span class="lab">一句话定调</span>{verdict}</div>

<section id="act"><div class="sec-head"><span class="n">1</span><h2>今日可执行</h2>
  <span class="hint">最多 3 条 · 按确定性排序</span></div>{render_actions(actions)}</section>

{data_sections}
{imp_html}
{ratio_html}
{insight_html}
<section id="skip"><div class="sec-head"><span class="n">{skip_no}</span>
  <h2>今日不动</h2><span class="hint">{n_skip} 只</span></div>
{render_skip(skip) or '<div class="empty">全部标的均有动作</div>'}</section>

<section id="risk"><div class="sec-head"><span class="n">{risk_no}</span>
  <h2>风险</h2></div>{render_risks(decision.get("risks", []))}</section>

{alerts_html}
{news_html}

<footer>
  <b>数据来源：</b>brief.py（报价首选腾讯自选股连接器快照 westock-mcp，其次腾讯行情 / 东方财富 / 新浪，失败自动降级；
  两融取东财数据中心 T+1 披露口径；底部快讯取东财 / 新浪 7x24 直播流），
  所有价格、资金、杠杆与快讯数据由脚本解析输出，未人工估算。<br>
  <b>规则依据：</b>strategy_rules.md §{'四' if is_noon else '三'}（{'四象限 / 资金结构 / 量能' if is_noon else '映射矩阵 / 过滤条件'}）、§2.3 目标配置≠调仓指令、knowledge K05 信贷脉冲（宏观否决位）、K04 股债比价、§六 输出格式、§七 纪律与熔断。<br>
  本报告为策略执行记录，<b>不构成投资建议</b>。市场有风险，决策请自行复核。
</footer>
</div>
<div class="dock"><div class="in"><div class="l">{dock_txt}</div>
  <a href="{dock_href}">{'看动作' if actions else '看不动清单'}</a></div></div>
</body></html>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["morning", "noon"], required=True)
    ap.add_argument("--data", required=True, help="brief.py 的文本输出文件")
    ap.add_argument("--decision", required=True, help="决策 JSON 文件")
    ap.add_argument("--out", help="输出 HTML 路径，缺省按日期生成")
    a = ap.parse_args()

    text = read_text(a.data)
    decision = json.loads(read_text(a.decision))

    d = parse_noon(text) if a.mode == "noon" else parse_morning(text)
    os.makedirs(OUT_DIR, exist_ok=True)
    date = decision.get("date") or d["date"]
    name = ("午盘" if a.mode == "noon" else "早盘") + f"T计划_{date}.html"
    out = a.out or os.path.join(OUT_DIR, name)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(a.mode, d, decision))

    # 给定时任务的简短回执
    print(f"OK {out}")
    print(f"解析：{len(d.get('indices', d.get('us', [])))} 条指数 / "
          f"{len(d.get('quotes', d.get('levels', [])))} 只持仓 / "
          f"{len(d.get('flows', []))} 条资金流")
    print(f"决策：{len(decision.get('actions', []))} 个动作 / {len(decision.get('skip', []))} 只不动")
    mk = d.get("market")
    print(f"资金面：大盘资金{'已解析' if mk else '缺失（brief 无该段或解析失败）'} / "
          f"解读 {len(decision.get('reads', []))} 条")
    mg = d.get("margin")
    print(f"两融：{'已解析' if mg else '缺失'} / 持仓 {len((mg or {}).get('pos') or [])} 只")
    nw = d.get("news")
    print(f"快讯：{'已解析' if nw else '缺失'} / {len((nw or {}).get('rows') or [])} 条 / "
          f"{((nw or {}).get('hours') or 0)} 小时窗口")
    imp = d.get("impulse")
    print(f"信贷脉冲：{'已解析' if imp else '缺失'} / " +
          (f"{imp['p']:+.2f}pp {imp['band']} / 拐点{imp['turn']}" if imp else "brief 无该段"))
    if mk:
        print(f"  国家队：同时点 {'%.2f×' % mk['cut']['x'] if mk.get('cut') else '缺失'} / "
              f"分族 {len(mk.get('fam') or [])} 族 / 逐只 {len(mk.get('nat') or [])} 只 / "
              f"观察池 {'有' if mk.get('ext') else '缺'} / "
              f"份额指数 {'%.1f' % mk['k01']['index'] if mk.get('k01') else '缺失'}")


if __name__ == "__main__":
    main()
