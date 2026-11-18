# CHANGELOG · 变更登记

> **用途**：登记每次 Agent 或人工对本目录的修改——改了哪些文件、改了什么、为什么改。方便后续快速查阅与回溯。
> **分工**：本文件记**变更事实**；`HANDOFF.md` 记**排查遇到的 Bug 与避坑经验**；`勘误与返工记录.md` 记**业务口径与数据结论的错误修正**（尚未建立，见文末）。
> **写法**：新条目在**最上面**（倒序）。一条 = 一次改动。只写事实，不写感悟；"为什么"一句话说清。
> **粒度**：同一主题改了多个文件算一条；`data/` 下的日更数据（csv/json）不登记；`reports/` 产物不登记（已 gitignore）。

**类型**：`架构` 结构性改造 / `指标` 新增或修改指标 / `数据源` 取数与口径 / `交付` 报告与推送 / `修复` bug / `配置` 依赖与调度 / `文档`。

---

## 2026-11-18

### 交付 · 新增 21:00 晚报（当日复盘，不做T）

- **改了什么**
  - `brief.py`：新增 `brief.py evening` 模式与 `evening(cfg)`；新增四个前段函数
    `ashare_block`（A股四大指数收盘 + 两市成交额，指数口径）、`hk_block`（恒指/恒生科技/国企指数 + 南向资金）、
    `global_block`（亚太收盘 / 欧洲收盘 / 美股昨夜 / 期货当前 / 汇率商品）、
    `broad_block`（国家队载具按指数族聚合，成交额加权涨跌幅 + 主力净额 + 份额变化，附持仓当日含 KDAS 六状态）。
  - `brief.py`：`BLOCKS` 的 market / margin / impulse / ratio 增加 `evening` 模式（news 不列，由 `evening()` 放到文末）；
    `market_block` 的成交量倍数在 evening 下按全天（进度 100%）计算并改用「全天已走完」措辞；
    `news_block` evening 窗口 12 小时；`main()` 增加 `evening` 分支。
  - 新建 `evening_gen.py`：晚报专用渲染器（复用 `report_gen` 的 CSS、解析器与 `render_market/render_margin/render_impulse/render_ratio/render_news`），
    新增 `.tb` 族别表 / `.gp` 全球分组卡 / `.wt` 明日关注卡三类样式。
  - `report_gen.py`：`parse_common` 认「晚报」；`render_market(mk, full=False)` 支持全天口径标签；
    `parse_market` 的「载具同时点」正则兼容「历史同时点已走完 / 全天已走完」；
    `PLAIN` 增加 `broad` / `hk` / `global` 三条大白话。
  - 新增定时任务「持仓复盘·晚报 21:00」（工作日 21:00，已绑定 westock-mcp）。
- **为什么**：用户需要收盘后的一份复盘——当天 A 股/港股、国家队与大单方向、融资额变化、全球股市、宽基 ETF 走势与重要新闻；
  做T动作留给次日 08:30，晚报只做「今天发生了什么」，避免与早午盘的计划职责重叠。
- **验证**：以 11-18 快照实跑 `brief.py evening` 得 9 段（A股/港股/全球/宽基/大盘资金/两融/K05/K04/要闻），
  `evening_gen.py` 回执「A股 4 条指数 / 港股 3 条 / 全球 5 组 / 宽基 11 行族别 + 7 只持仓 / 资金面四项有 / 要闻 12 条」；
  午盘 `report_gen.py --mode noon` 在 11-17 真实数据上回归，解析字段与改动前一致。

### 架构 · 段落 key 化：序号不再当主键

- **改了什么**
  - `brief.py`：新增 `BLOCKS` 注册表（key / 标题 / 函数 / 适用模式）+ `cn()` + `emit_blocks(cfg, mode, start)`；`morning()`、`noon()` 里手写的 `market_block("五","morning")` 等五行调用，换成 `emit_blocks(cfg,"morning",start=4)` / `start=6`。
  - `report_gen.py`：删除 `section(text,"一","二")`（按中文序号切段），新增 `sec_title(text, title, next_title)` 按**标题关键字**切段；`parse_morning`、`parse_noon` 共 9 处调用全部替换。
  - `market_block`/`news_block`/`impulse_block` 签名统一为 `(no, cfg, mode)`，供注册表统一调用。
- **为什么**：序号同时是版面标签和解析主键——在中间插入一个指标就要重排后面所有段号，而没重排的那几段会静默串位。K04/K05 因此只能被迫追加到文末（K05 是宏观否决位，逻辑上该在最前）。改造后段号由顺序自动生成，解析与顺序完全解耦。
- **验证**：① 早盘 HTML 与改造前逐字节一致（0 字节差异）；② 新旧解析逻辑在 11-17 午盘 / 11-18 早盘两份真实数据上全字段 JSON 等价；③ 实跑 morning 9 段、noon 11 段序号正确；④ 序号标签互换、整段移位重编号后再解析仍全部正确。

### 指标 · 新增 `signals.md` 指标读数台账

- **改了什么**
  - 新建 `signals.md`（根目录）：追加式表格 `date / key / value / band / hs300 / fwd20`。
  - `brief.py`：新增 `signal()`、`signals_flush(mode)`、`signals_backfill()`、`_sig_read/_sig_write`；`noon()` 在指标段输出后调用 `signals_flush("noon")`（带 try，写失败不拖垮报告）；`main()` 新增 `--signals backfill` 入口。
  - 接入两个指标：`ratio_block` 末尾 `signal("K04", 比价, 档位, hs300)`；`credit_impulse.block()` 末尾改为 `return s`，`impulse_block` 据此 `signal("K05", 脉冲, 档位, hs300)`。
- **为什么**：指标体系与指标堆的唯一区别是**能不能回溯**。没有"这个档位后来涨没涨"的记录，加第 12 个指标和第 3 个一样不可信。`fwd20` 用表内未来第 20 行的 hs300 点位离线计算，不额外占接口。
- **口径**：只午盘写（早盘是上一交易日收盘口径，写入会与当天重复）；同日同 key 覆盖、不同 key 保留（K05 取数失败不抹掉当天历史行）。

### 交付 · 删除 4 个一次性 HTML

- **改了什么**：`git rm` `demo_battery.html`、`etf_radar_2026-11-05.html`、`noon_T_report_2026-11-05.html`、`grid_plan_2026-11-05.html`（共 107KB）。
- **为什么**：11-05 那天的历史快照，无任何引用；根目录 HTML 与 `reports/` 产物职责混淆。
- **影响**：均已跟踪，`git checkout <commit> -- <file>` 可恢复。

### 文档 · 新建 CHANGELOG.md 与 HANDOFF.md

- **改了什么**：新增本文件与 `HANDOFF.md`；从 `.workbuddy/memory/` 的 11-16~11-18 工作日志回填历史条目。
- **为什么**：变更事实此前只存在 agent 记忆里，人查不到；新接手者（人或 agent）需要一份"改动过什么、哪里有坑"的入口。

---

## 2026-11-17

### 指标 · K05 信贷脉冲上线并接入日报

- **改了什么**：新增 `knowledge/K05_信贷脉冲领先沪深300同比.md`、`credit_impulse.py`、`data/social_financing.csv`（2023-01 起）、`data/hs300_month.csv`；`brief.py` 加 `impulse_block()`；`report_gen.py` 加 `parse_impulse`/`render_impulse`；`knowledge/INDEX.md` 加 K05 行并把层级关系前置 K05（有否决权）。
- **为什么**：用户提供的宏观图（信贷脉冲 vs 沪深300同比）需要落地成可每日读数、且能进入决策链的指标。
- **口径决定**：统一用「社融存量同比的 12 个月差分」（存量同比动量），实测领先沪深300同比 **4 个月**（r=0.66，n=31）；不混用 BIS 增量/GDP 口径（差 1.5~2 倍）。拐点判据「连升 3 月且剔除政府债版同步」，当前未确认。
- **踩坑**：`parse_ratio` 的段头正则曾只写 `[九十]+`，早盘「八、」匹配不到导致整卡静默消失——统一改 `[一二三四五六七八九十]+`。

### 交付 · 报告 HTML 改版（用户 4 点反馈）

- **改了什么**（全在 `report_gen.py`）：新增 `PLAIN` 字典 + `plain()`（8 条大白话解释条，不含具体数字）；字号整体放大（正文 15→17px）；`risks`/`alerts` 支持 `{"t","d"}` 卡片化；快讯新增 `flash_digest` 要点 + 原始条目折叠；信贷脉冲/股债比价压缩成「大字+4格+一句结论」，细节进 `<details>`；新增 `ST_CLS` 状态配色。
- **为什么**：用户反馈技术指标看不懂、字号太小、10/11 两段一堆字、快讯要更多总结。
- **同步**：`strategy_rules.md` §八、`report_gen.py` docstring、两条 automation prompt。

### 配置 · 首次推送 GitHub 私有仓

- **改了什么**：远端 `https://github.com/soulgo/stock.git`（私有），本地 `main` 初始提交 `dc89044`（51 文件 / 20366 行）已 push 并建立跟踪。
- **注意**：`.gitignore` 排除 `.workbuddy/`、`reports/`、`__pycache__/`，所以报告产物与 agent 记忆不在仓库内，换机不会自动带走。

---

## 2026-11-16（改动最密集的一天，按主题合并）

### 数据源 · 报价链路重构：连接器 → 腾讯 → 东财 → 新浪

- **改了什么**：`brief.py` 报价四级降级；腾讯解析加三重自检（14 位时间戳 / 涨跌幅与现价-昨收自洽 / 高低价包络）；`quote_em` 用 f86 算 lag，>120s 判陈旧；新增 `mx_snapshot.py`（连接器原始 JSON → 归一化 `data/_mx_<date>.json`，TTL 3 分钟）；新增「附：数据源校验」段。
- **为什么**：实测同一秒请求东财 push2 四个镜像，f86 时间戳在「实时（滞后 1s）」与「10:38 旧快照（滞后 899s）」间随机跳；下跌市里滞后表现为「一律偏高 0.22%~0.51%」。腾讯与新浪逐分一致，且与自选股 App 同源。
- **收益**：资金流从滞后 15 分钟变实时；均价/换手/量比直接用连接器字段，不再反算。

### 数据源 · 午盘也改走连接器

- **改了什么**：`mx_snapshot.py` 报价同时写 `quotes`（裸码）与 `quotes_full`（带前缀）；`brief.py` 新增 `SID_MX`、`index_quote_mx()`，A 股 4 个指数也走快照；`noon()` 把行情/分时/资金流拆成三个独立 try。
- **为什么**：此前提示词里没有「先调连接器」这一步，每天在 TTL 过期后静默回落东财。`noon()` 三个 try 是 11-12 事故的直接修复（分时接口挂了曾导致整行「取数失败」）。

### 指标 · 报告新增「资金面解读」区

- **改了什么**：`brief.py` 新增 `market_block()`（早盘「五」/ 午盘「七」）；`report_gen.py` 新增 `parse_market`/`render_market`/`render_reads`/`rich`；决策 JSON 新增 `reads` 字段（大盘资金→国家队→消息面→对持仓含义 4 张卡）。
- **数据打通**：`data_fund_flow` 传指数码可拿指数级资金流（含 `MainNetFlow5D/10D/20D`）；`data_market_overview` 拿两市成交额/涨跌家数；`data_etf(overview)` 只拿到规模（份额字段恒 0）。
- **修掉的 bug**：`_yi` 精度丢失（−4948 万被压成 −0.5 亿）；成交额比例把「20 日均量」的 20 当成比例；载具明细生成器元组/字典索引混淆 KeyError。

### 指标 · KDAS 六状态真正落地

- **改了什么**：新增 `kdas_relay()`、`kdas_state()`；`levels(code, px)` 支持传实时价；brief 状态列改真六状态 + 「强弱分界」行；`report_gen.py` 新增 `STATE_ADVICE`；`strategy_rules.md` §4.0 / §3.5 写入「六状态是能不能做的第一道闸」。
- **为什么**：用户质问报告有没有用 KDAS，核查发现只用了支撑/压力，原「状态」列是临时标签且判据自相矛盾（「所有线在下方」就判右侧趋势，无视强弱分界线）。
- **口径**：强弱分界线 = 最新锚点画出的那条 KDAS 线；**只有震荡区间可做T**。
- **废弃方案**：曾试「逐日模拟穿越做接力」，锚点被噪声推到最近、且锚点=当日时线值恒等于均价，必然造假买点。

### 数据源 · 国家队四项升级（池 9 → 24 只）

- **改了什么**：① 成交额一律标注「X 亿（YYYY-MM-DD 收盘，20 日均量 YY%）」；② 新增「同时点成交量倍数」`pool_baseline()` + `data/pool_baseline.json`（只在午盘算）；③ `etf_flow.py` 整文件重写，K01 份额改读 `data_quote` 的 `total_shares`；④ 载具池 9 → 主池 20 + 观察池 4，`FAM_ORDER` 10 族，报告加分族 chips / 观察池 / 逐只折叠。
- **为什么**：`data_market_overview` 盘中永不更新，此前把昨日成交额写成今日实时；东财 f84（份额）已断供且 `push2delay2` 的值差 1.9 倍不能兜底。
- **修正**：科创50 的汇金持仓在 **588080（易方达）**，原用 588000（华夏）作主池是错的，已降为观察池。
- **踩坑**：`build_index` 只有主池 20 只全齐的日期才作基准，否则指数被污染；`last_rec` 跨期 >4 天要标「跨 N 日」且不计入当日合计。

### 指标 · 新增「两融」段

- **改了什么**：新增 `dcget()`（独立于 `_get()`）、`margin_market()`、`margin_etf()`、`margin_block()`；缓存 `data/margin_market.json`、`data/margin_etf.json`；`report_gen.py` 加 `parse_margin`/`render_margin`。
- **为什么**：连接器 `data_fund_margin` 对 ETF 一律返回空数组（只支持个股），两融得走东财数据中心 `RPTA_RZRQ_LSHJ` + `RPTA_WEB_RZRQ_GGMX`。
- **口径**：T+1 披露，最新一条恒为上一交易日，报告必须标日期。

### 指标 · 新增「实时快讯」段

- **改了什么**：新增 `plain_get()`（不走 `_get()`）、`news_feed()`、`news_score()`、`news_block()`；`report_gen.py` 加 `parse_news`/`render_news`。窗口午盘 6h / 早盘 14h，相关度 ≥2 分进「相关」车道，同一关键词最多 3 条。
- **为什么**：用户要求报告底部显示临近时间新闻便于即时判断；`_get()` 会对任何 eastmoney 域名做镜像轮换，newsapi 被换必失败。

### 指标 · 做T 战果段（建了但停显示）

- **改了什么**：新建 `data/t_ledger.json`（8 条）；`tstats_block()`、`parse_tstats`/`render_tstats` 保留。
- **现状**：2026-11-16 用户要求报告不显示，`brief.py` 不再调用它（省 7 次 quote），代码与账本照常维护。**2026-11-18 起它也没进 `BLOCKS` 注册表**。

### 指标 · K04 股债比价（三层落地）

- **改了什么**：新增 `equity_bond_ratio.py`、`knowledge/K04_*.md`；锚点 `data/equity_bond_anchor.json`，序列 `data/equity_bond_ratio.csv`；`--html` 出手机看板。次日扩成双向（下沿 1.0 当扳机 / 上沿 1.30、1.50 只当温度计）+「股债走廊」+「债端温度」；再接进日报（`ratio_block` + `parse_ratio`/`render_ratio`）。
- **为什么**：用户从博主截图提取指标并要求复算。逐项复算全部命中（1.181 = 6048.32/5121.91 等）。
- **口径**：`P* = 比价 × 债腿 ÷ k`，`k = 全收益÷价格 = 1.50595`；885007 无公开接口，用「锚点 + 中证全债推进 + β=0.12」代理。
- **踩坑**：中证官网 API 单次取数会返回滞后一天/月份边界重复值，必须多次取数 + k 系数配对筛选。

### 配置 · 513700 清仓并移出做T池

- **改了什么**：`portfolio.json` 删除 513700；两个 automation 的 codes 改为 7 只（此前一直漏 513650、却还留着 513700）。
- **之后**：做T池 = 512170 / 563800 / 159928 / 513500 / 513650 / 515180 / 515450。

---

## 2026-11-12

### 修复 · 午盘整行「取数失败」

- **改了什么**：`noon()` 把行情、分时、资金流拆成三个独立 try。
- **为什么**：东财分时接口不可用时，整只持仓行被丢弃（虽然连接器已经拿到报价）。改成「分时挂了只丢『较昨日同时点』一项」。

---

## 2026-11-11

### 交付 · 早/午盘改为 HTML 报告交付

- **改了什么**：两个定时任务一律交付 `reports/<早盘|午盘>T计划_<date>.html`（手机端自包含 CSS），必须通过附件交付；正文只留 8 行以内摘要。
- **为什么**：用户明确嫌纯文本「乱」——数据重复、等权平铺、一行塞太多括号信息。版式由 `report_gen.py` 固定，禁止每次手写 HTML。

---

## 2026-11-05

### 架构 · 建立 `knowledge/` 知识库

- **改了什么**：新建 `knowledge/INDEX.md` + `K00_关注范围_只做宽基`、`K01_汇金ETF累计净流入`、`K02_跨市场形态对齐`、`K03_资金流与形态四状态`；`readme.md`（KDAS 手册）、`strategy_rules.md`（做T规则）成文。
- **为什么**：把「看懂的图 / 想明白的规律」沉淀成可检索、可执行的条目，每条必须带「口径 / 结论 / 边界」三件套。
- **K00 硬约束**：只做宽基 ETF，核心是跟国家队在宽基的进出；行业/主题/策略/跨境/商品/债券一律不做。

---

## 尚未登记的历史

- 2026-11-05 之前的探索性脚本（`broad.py`、`sweep.py`、`grid.py`、`kdas.py` 等）无逐日记录，功能以代码 docstring 为准。

---

## 待处理的文档缺口

- `勘误与返工记录.md` **尚未建立**。目前业务口径的修正（如 K05 领先期 3 个月 vs 实测 4 个月、科创50 载具选错标的、K04 债腿代理误差）散落在各 K 文档的「什么时候失效」一节与 `.workbuddy/memory/` 日志里。若需要独立成册，说一声即可补建骨架。
