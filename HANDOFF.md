# HANDOFF · 交接与避坑

> **用途**：让接手的人（或新的 agent）在五分钟内知道这套系统怎么跑、哪里最容易坏、坏了怎么查。
> **分工**：本文件记**排查遇到的 Bug 与避坑经验**；`changelog.md` 记**变更事实**；`勘误与返工记录.md` 记**业务口径与数据结论的错误修正**（未建立）。
> **不重复**：数据源口径的完整结论在 `strategy_rules.md` §八；指标体系在 `knowledge/INDEX.md`；做T 决策规则在 `strategy_rules.md`。本文件只给**入口和坑**。

---

## 一、这是什么

一套 A 股 ETF 持仓的日频做T 报告系统。每个交易日产出两份手机端 HTML：**早盘 8:30**（隔夜外盘 + 今日关键位 + 资金面）与**午盘 12:30**（上午表现 + 资金流 + 六状态 + 指标段）。

**它只给数据与事实，不做买卖决策**；决策由人（或上层策略）按 `strategy_rules.md` 做。

### 谁在跑

| 位置 | ID | 角色 |
|---|---|---|
| 云端 | `cloud:15878801`（早 08:30）/ `cloud:15878805`（午 12:30） | **生产** |
| 本地 | `732b5775`（早 08:30）/ `d5309087`（午 12:30）/ `e633e39b`（晚 21:00，仅本地，云端未建） | **迭代测试** |

**规则**：本地验证通过后才同步到云端；**对云端只读不改**。两边的 prompt 各存一份，改流程要同步两处（这是目前最大的维护负担，见文末）。

---

## 二、五分钟跑通

关键前提：**腾讯自选股连接器是 agent 工具，Python 脚本调不到**。所以取数必须由 agent 先做，脚本只处理落地文件。

```bash
D=2026-11-18

# 0) agent 侧：调连接器 7 组，原始返回原样存盘（保留 {"ok":true,"data":{...}} 结构）
#    data_quote ×2（7 持仓+4 指数 / 24 载具）
#    data_fund_flow ×3（持仓+指数 / 载具前 12 / 载具后 12）
#    data_etf(overview) ×1（24 载具）
#    data_market_overview(type=all) ×1
#    → data/_raw_{quote_hold,quote_pool,flow_hold,flow_pool1,flow_pool2,etf,market}_$D.json

# 1) 归一化（快照 TTL 3 分钟，必须紧跟第 0 步）
python mx_snapshot.py \
  --quotes "data/_raw_quote_hold_$D.json,data/_raw_quote_pool_$D.json" \
  --flows "data/_raw_flow_hold_$D.json,data/_raw_flow_pool1_$D.json,data/_raw_flow_pool2_$D.json" \
  --market "data/_raw_market_$D.json" --etf "data/_raw_etf_$D.json"

# 2) 份额快照：午盘与晚报都要跑，早盘**绝不能跑**（8:30 的 total_shares 还是昨收值）
#    必须带 --mx，否则会静默回落东财 f84（已断供，会拿到错值）
#    **晚报必须放在 brief.py 之前**：宽基表的「份额变化」列读的就是这一步写进 data/etf_flow.csv 的当日记录
python etf_flow.py --mx "data/_mx_$D.json"

# 3) 面板（唯一数据源，后面所有数字都出自这里）
python brief.py morning > reports/_morning_$D.txt
python brief.py noon    > reports/_noon_$D.txt      # 午盘会顺手写 signals.md
python brief.py evening > reports/_evening_$D.txt   # 21:00 复盘：A股/港股/全球 + 宽基ETF + 国家队 + 两融 + 要闻

# 4) 渲染（需要人工/agent 先写决策 JSON）
python report_gen.py --mode morning \
  --data "reports/_morning_$D.txt" --decision "reports/_decision_morning_$D.json"
python evening_gen.py --data "reports/_evening_$D.txt" \
  --decision "reports/_decision_evening_$D.json"     # → reports/晚报_$D.html
```

跑完自检四件事：① 首行含「连接器快照」；② 文末「报价取数：连接器 X/7」；③ 指标段不为「无数据」；④ 出现「快照已过期」就重取。

---

## 三、目录职责

| 文件 | 干什么 | 别拿它做什么 |
|---|---|---|
| `brief.py` | 取数 + 面板输出（唯一数据源），`morning` / `noon` / `evening` 三模式 | 别在别处重算指标 |
| `panel.py` | **面板契约**：21 个解析器 + 快照读写 + 字段校验 | 别在这里渲染 HTML；别在别处写正则解析面板 |
| `report_gen.py` | 早/午盘：面板文本 + 决策 JSON → HTML | 别手写 HTML |
| `evening_gen.py` | 晚报：面板文本 + 决策 JSON → HTML（复用 report_gen 的 CSS 与解析器） | 别手写 HTML；别在这里重算数字 |
| `strategy_rules.md` | 做T 规则（四象限、六状态闸门、输出格式） | 不是知识库 |
| `readme.md` | **项目导航**：三条命令 + 目录分工 + 文档地图 | 不是手册，别再往里塞理论 |
| `docs/KDAS手册.md` | KDAS 体系手册（微观、分时、资金验证） | 不参与自动决策 |
| `tools/*.py` | 离线工具（KDAS 画线 / 支撑扫描 / 网格 / 宽基位置） | 不在日链上，随便重跑 |
| `indicators/*.py` | 指标计算模块（K04 股债比价 / K05 信贷脉冲） | 别在这里重算别的指标 |
| `knowledge/K0x.md` | 单条指标的口径 / 结论 / 失效条件 | 不写代码 |
| `portfolio.json` | 持仓 | 做T池 = holdings 去掉货币ETF |
| `trades.md` / `signals.md` | 成交记录 / 指标读数台账 | 前者手写，后者脚本写 |
| `changelog.md` / `HANDOFF.md` | 变更事实 / 避坑入口 | — |

---

## 四、改代码前五条铁律

1. **序号不是主键**。段号由 `brief.py` 的 `BLOCKS` 自动生成，`report_gen` 用 `sec_title()` 按标题关键字切段。**不要再手写「八、」，也不要再按中文数字切段**（2026-11-18 前的做法，插入指标必串位）。
2. **加指标只走两步**：`BLOCKS` 加一行 `(key, 标题关键字, 函数, 模式)`；函数里 `signal(key, value, band, hs300)` 进台账。不需要碰 `report_gen`。
   **指标段内部一律三段式**（2026-11-18 起，`market`/`margin`/`news` 已改）：`xxx_data()` 只取数并返回 dict（缺值用 `None`，不写 `—`、不补 0）→ `xxx_render(d, no)` 只做格式化并返回行列表 → `xxx_block()` 逐行 print。格式（单位、`—`、条件后缀）只允许写在 render 里，否则又回到「两处定义格式、对不上就静默丢行」。
   > **面板格式只在两处定义**：`brief.py` 的 `print` 与 `panel.py` 的对应解析器。改打印格式必须同批改解析器，并跑 `python tools/panel_check.py`。渲染层不许再出现面板正则。
3. **段内行格式固定**：`两空格 + 标签 + 2 个以上空格 + 值`。`panel.py` 按标签正则解析，缩进和标签里的空格都会导致**整行静默丢失**（渲染器已不含正则）。
4. **数字只能来自 `brief.py` 输出**。不估算、不用另一路接口补算。当天 `levels()` 连续调用会有千分之几漂移，定稿一律以 `_<mode>_<date>.txt` 表格数字为准。
5. **报告里不许出现空号**。缺段就整段跳过，`render()` 会自动收回编号。

---

## 五、避坑清单（现象 → 根因 → 处置）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 报价一律**偏高 0.2~0.5%** | 东财多个镜像各自混用缓存，同一秒请求 f86 时间戳在「实时」与「滞后 899s」之间随机跳；下跌市里滞后就表现为偏高 | **东财不能单独当报价源**。链路固定为「连接器快照 → 腾讯 qt.gtimg.cn → 东财 → 新浪」。存疑时看 f86 算 lag |
| 2 | 一整天数据全空，首行不是「连接器快照」 | 连接器是 agent 工具，脚本调不到；或快照超过 TTL 3 分钟 | 必须 agent 先存 raw → `mx_snapshot.py` → `brief.py` 三步，**不能跳步、不能隔太久** |
| 3 | 某段解析空白 / 数字串到别的段 | 旧 `section(text,"一","二")` 按中文序号切段，序号一重排整链错位 | 2026-11-18 已改 `sec_title()`。若还见到，说明有人新写了按序号切段的代码 |
| 4 | 「日内位置」全是 50%、振幅显示「—」 | `parse_intraday` 正则曾把「，较昨日同时点…」写成必选；分时接口不可用时 brief 不带该后缀 → 整行不匹配退化成默认值 | 后半段必须可选。**凡「四、日内位置」没有「较昨日同时点」的交易日，先怀疑这条** |
| 5 | 逐只降本明细整块 7 行消失、不报错 | 行首缩进用 `^\s{2}`（正好两个），续行是 18 个空格，`\s{2}` 吃掉两个后 `\S+` 撞上空格 | 段内解析一律用 `^\s{2,}`；标签里不能带空格（「T 往返」→「T往返」） |
| 6 | 早盘浮盈显示 **−100%** | 09:15 集合竞价前 f43 返回 0 | 用昨收兜底（`q["price"] or q["prev"]`） |
| 7 | 成交额被写成「今天的」 | `data_market_overview` **盘中永不更新**，恒返回上一交易日收盘口径 | 报告必须写成「X 亿（YYYY-MM-DD 收盘，20 日均量 YY%）」，`amount_date` 字段带日期 |
| 8 | 国家队份额拿不到、恒为 0 | `data_etf(overview)` 的 `etfInFlow`/`etfSizeWChg` 全返回 0 | 份额改读 `data_quote` 的 `total_shares`（K01）；东财 f84 已断供且镜像值差 1.9 倍，**不能当兜底** |
| 9 | 早盘把昨天的份额记成今天 | 8:30 的 `total_shares` 还是昨收值 | **早盘不跑 `etf_flow.py`**，份额由 12:30 维护 |
| 10 | 份额数值离谱（差一两倍） | `etf_flow.py` 不带 `--mx` 时会逐只回落东财 f84，而 f84 已断供、镜像值差 1.9 倍 | 必须 `python etf_flow.py --mx "data/_mx_<date>.json"`；**不带参数不是「自动找」，是「静默走坏源」** |
| 11 | 「载具规模」明显偏小 | `data_etf(overview)` 一次只回 17~18 只，7 只空壳（510230/512050/515800/588000/159338/159339/159901） | 按缺失码补调一次再合并 |
| 12 | 日线全部取数失败 | 东财 `push2his` 返回 000；腾讯 `ifzq.gtimg.cn` 高频后返回 501 + JS 跳转页 | `daily_tx()` 已改 `web.ifzq.gtimg.cn` 优先 + 双域名轮换 + 每域名重试 2 次 |
| 13 | 中证官网取数偶尔滞后一天 / 月份边界重复值 | API 有坏窗口，同一窗口重复请求会返回错位数据 | 多次取数留候选 → 用 `k = 全收益÷价格 = 1.5060±0.3%` 配对筛掉错位 → 锚点日以人工公布值为准。**单次取数不可信** |
| 14 | 两融 / 快讯取数 000 | 走了 `_get()` 的东财镜像轮换（datacenter-web 被换成 push2 必失败） | 两融用独立的 `dcget()`，快讯用 `plain_get()`，都不参与行情降级计数器 |
| 15 | 南向资金显示 ±420 亿、正好等于每日额度 | 东财 `kamt` 接口同时承载「额度余额」，单位万元；额度用尽或数据未更新时返回的就是 420 亿 | `southbound()` 已加校验：绝对值 ≥ 阈值 99% 判为无效，输出「无数据」。**不要为了让表格好看去掉这个判断** |
| 16 | 晚报宽基表「份额变化」整列是「—」 | 顺序反了：`brief.py evening` 读的是 `data/etf_flow.csv` 当日行，`etf_flow.py` 必须在它之前跑 | 固定顺序 `mx_snapshot → etf_flow --mx → brief.py evening` |
| 17 | 晚报宽基表整段「无数据（载具报价全部缺失）」 | 把 `sh510300` 这类**带前缀码**喂给了 `quote()`：缓存键和东财 secid 都要裸码，会一路错到腾讯拼成 `shsh510300` | 统一走 `ev_bare()` 剥前缀再取数；带前缀码只用于查 `quotes_full` |
| 18 | QDII 溢价没被拦 | 溢价率数据源未接通 | 跨境品种（513500/513650）做T 前**人工看 `data_etf` 的 `etfDisc`**（折溢价率 %）。11-16 实测 513500 = 10.43%、513650 = 8.58%，都在一年均值上方 4pp |
| 20 | 渲染报错 `KeyError: 'ashare'` / 报告缺段但 txt 看着正常 | `read_panel` 按 mode 分派解析器，晚报必须走 `panel.parse_evening`；早期实现按「非 noon 即 morning」二分，晚报会被当成早盘解析 | 2026-11-18 已改为字典分派。见到这个错，先确认 `read_panel` 的分派表里有 evening |
| 19 | 「大盘资金与国家队」整段只剩段头 + 「取数失败：'sh510300'」 | 旧版 `market_block` 里 `ff[c]` 没做兜底：载具规模（`etf`）有值但载具资金流（`flows_full`）缺失时抛 `KeyError`，被 `emit_blocks` 的 except 吞掉 | 2026-11-18 已改 `ff.get(c) or {}`。见到这个段只剩段头，先看 `data/_mx_<date>.json` 的 `flows_full` 是否为空 |

---

## 六、改完怎么验证

**必做三件**（缺一件都算没改完）：

1. **早盘回归**：用昨天的 `_morning_<date>.txt` + 决策 JSON 重新渲染，与改动前的 HTML 对比。**纯解析改动应该 0 字节差异**。
2. **等价性验证**：新旧解析逻辑在同一份真实数据上比 `parse_morning`/`parse_noon` 的完整 JSON，全字段相等才算等价。
3. **实跑一次** `brief.py morning` 与 `noon`，确认段数与序号正确（早盘 9 段、午盘 11 段），且指标段没变「无数据」。
4. **晚报单独验**：`brief.py evening` 应出 9 段（一 A股 / 二 港股 / 三 全球 / 四 宽基 / 五 大盘资金 / 六 两融 / 七 K05 / 八 K04 / 九 要闻）；
   `evening_gen.py` 回执应为「A股 4 条指数 / 港股 3 条 / 全球 5 组 / 宽基 11 行族别 + 7 只持仓 / 资金面四项有 / 要闻 N 条」。
   宽基 0 行族别 = 解析正则与 `broad_block` 的列格式脱节，两张表要一起改。

5. **改了指标段（`xxx_data`/`xxx_render`）必须跑 A/B 对拍**：`python tools/ab_blocks.py`。
   它把 `HEAD` 的 `brief.py` 取出来，与工作区版本吃**同一份冻结输入**（快照 + 两融缓存 + 快讯 fixture，
   `intraday_progress`/`pool_baseline` 钉成常量），逐字节比较 market/margin/news 在三个模式下的输出；
   另有一组退化输入专打「缺数」分支——正常数据跑不到那里，而重构最容易在那里搬错条件。
   判据：正常 7 例全一致；退化 6 例允许「旧版抛异常、新版正常输出」（说明顺带修了崩溃）。

6. **动了面板格式或解析器，跑 `python tools/panel_check.py`**：把 21 个解析器与搬迁前实现
   在 15 份历史面板上逐份比 dict，缺一项就算改坏。改动涉及渲染层时再验「快照路径 vs 文本路径」
   渲染结果字节级一致（同一份数据各渲染一次 `cmp` 即可）。
7. **`reports/_panel_<mode>_<date>.json` 与 txt 必须同批产生**：它带文本指纹，文本被改过会自动
   回落解析文本（stderr 会提示「快照与文本不匹配」）。见到这句不必慌，说明保护生效了。

**看版式**（本机没有 playwright）：

```bash
chrome --headless=new --screenshot=out.png --force-device-scale-factor=1 --window-size=500 ...
```

坑：headless 的 layout viewport 是 **497px** 而非 `--window-size` 给的值，宽度给大了会以为右侧被裁切。要分段看图，先把 `<style>` + 目标 `<section>` 拼成临时 HTML 再截。

---

## 七、出问题先看哪里

| 症状 | 先看 |
|---|---|
| 报告数字不对 | `reports/_<mode>_<date>.txt`（面板原文）→ 再看 `data/_mx_<date>.json` 的 `written` 时间戳 |
| 某段空白 | `report_gen.py` 对应 `parse_xxx` 的正则 vs 面板原文的实际行格式（多半是缩进/可选字段） |
| 图表样式错乱 | `render_xxx` 的 HTML 字符串 + `.css` 里的类名是否对上 |
| 定时任务没产出 | automation 的 prompt 自检项 → 本地先手动跑一遍命令链 |
| 决策方向可疑 | `strategy_rules.md` §4.0（六状态第一道闸）+ §3.5（过滤条件）— 顺序不能颠倒 |

---

## 八、已知缺陷与待办（截至 2026-11-18）

**缺陷**

- 「附：数据源校验」打印「连接器 8/7 只」，分子恒多 1，不影响数据。
- `levels()` 里「距离」为负时仍显示「贴支撑」（如现价低于支撑 0.31%），语义可疑，未打磨。
- `data/flows` 的载具明细在 `data_etf` 空壳时只归一化出 18~21 只，低于预期 24 只。
- 做T 账本 `data/t_ledger.json` 有 1 笔未结（512170，11-12 正T 买 0.344×15,000、卖 0.356），已连续多日写进 alerts。

**待办**

- `signals.md` 的 `fwd20` 要攒满 20 个交易日（约 **2026-12-16**）才有第一行回填值，之后才能按 `band` 分组看「哪个档位管用」。
- K01 份额指数首日 2026-11-16，**攒够 8 周（约 2027-01 中）才有判断价值**。
- 目录整理已完成（2026-11-18，见 `docs/adr/ADR-001-目录分层.md`）：4 个产物 JSON 已收进 `data/`；`grid.py` + `grid_plan.py` **未合并**（职责仍分"算数据"与"排档位"两步，合并属行为改动，另立项）；外部资料已归档 `docs/research/`。
- **最大的维护负担**：两条 automation 的 prompt 各约 3000 字、内容高度重复，改一次流程要同步 4 份（本地 2 + 云端 2）。后续应抽成 `RUNBOOK.md`，prompt 里只留「按 RUNBOOK 执行」。
- 医疗 11,500 份 @0.344 的错仓（11-12 产生）仍未处置，0.346 挂单未成交。
