# ETF 持仓做T 报告系统

每个交易日产出三份手机端 HTML 报告：**早盘 08:30**、**午盘 12:30**、**晚报 21:00**。
系统只给数据与事实，不做买卖决策；决策由人按 `strategy_rules.md` 完成。

> 本文件是**项目导航**。KDAS 体系手册（原 readme 的内容，43KB）已移至 `docs/KDAS手册.md`。

---

## 一、五分钟跑通

前提：**腾讯自选股连接器是 agent 工具，Python 脚本调不到**。取数必须由 agent 先做，
脚本只处理已落地的 JSON。缺了第 0 步，整份报告会静默退回东财（数据滞后十几分钟）。

```bash
D=2026-11-18

# 0) agent 侧：连接器取数，原始返回原样存盘 → data/_raw_*.json
# 1) 归一化（快照 TTL 3 分钟，必须紧跟第 0 步）
python mx_snapshot.py --quotes "data/_raw_quote_hold_$D.json,data/_raw_quote_pool_$D.json" \
  --flows "data/_raw_flow_hold_$D.json,data/_raw_flow_pool1_$D.json,data/_raw_flow_pool2_$D.json" \
  --market "data/_raw_market_$D.json" --etf "data/_raw_etf_$D.json"

# 2) K01 份额（午盘/晚报前跑；早盘绝不能跑）
python etf_flow.py --mx "data/_mx_$D.json"

# 3) 面板：唯一数据源
python brief.py morning > reports/_morning_$D.txt
python brief.py noon    > reports/_noon_$D.txt
python brief.py evening > reports/_evening_$D.txt

# 4) 渲染（需先写决策 JSON）
python report_gen.py  --mode morning --data reports/_morning_$D.txt --decision reports/_decision_morning_$D.json
python report_gen.py  --mode noon    --data reports/_noon_$D.txt    --decision reports/_decision_noon_$D.json
python evening_gen.py --data reports/_evening_$D.txt --decision reports/_decision_evening_$D.json
```

完整版本（含连接器 7 组调用的具体 codes）见 `HANDOFF.md` §二。

---

## 二、目录分工

| 位置 | 放什么 | 谁能改 |
|---|---|---|
| `*.py`（根） | **5 个入口脚本**，名字被定时任务引用，不要改名 | 谨慎 |
| `panel.py` | **面板契约**：把面板文本读成结构、落 JSON 快照、做字段校验 | 改格式要同批改 |
| `indicators/` | 指标计算模块，一个 K0x 一个（K04 股债比价 / K05 信贷脉冲） | 可扩展 |
| `tools/` | 离线工具（KDAS 画线、支撑压力扫描、网格、宽基位置、指标段 A/B 对拍），不在日链上 | 随时重跑 |
| `data/` | 脚本读写的机读数据：连接器原始返回、快照、缓存、长期序列、状态账本 | 脚本写 |
| `reports/` | 当日产物；`archive/` 放三天前的历史与临时预览 | 脚本写 |
| `docs/` | 只读资料：KDAS 手册、外部研究存档、ADR | 只读为主 |
| `knowledge/` | 指标口径（K00~K05），入口 `knowledge/INDEX.md` | 人工维护 |

三份**人工台账**留在根目录：`portfolio.json`（持仓）、`trades.md`（成交）、`signals.md`（指标读数）。

---

## 三、文档去哪看

| 我想知道… | 读这份 |
|---|---|
| 这套系统怎么跑、坏了怎么查 | `HANDOFF.md` ← **接手先读这个** |
| 为什么可以买、为什么不能买（决策规则） | `strategy_rules.md` |
| KDAS 六状态、锚点、分时与资金验证的原理 | `docs/KDAS手册.md` |
| 某条指标的定义与失效条件 | `knowledge/INDEX.md` |
| 改了什么、为什么改 | `changelog.md` |
| 目录为什么这样分层 | `docs/adr/ADR-001-目录分层.md` |
| 交易体系的理论来源（外部资料整理） | `docs/research/策略体系梳理_Tony.md` |

---

## 四、改代码前必读

`HANDOFF.md` §四「改代码前五条铁律」。最常踩的一条：**段号不是主键**，
指标段靠 `brief.py` 的 `BLOCKS` 注册表自动编号，`report_gen.py` 按标题关键字切段 —
不要手写「八、」，也不要按中文数字切段。
