# Goal Document: 投资指标体系可靠性与复盘闭环

## Go / No-Go
- **Judgment**: Go
- **Reason**: 用户明确授权实施评审项 1–7，排除第 5 项定时交付闭环；云端由用户自行测试。实现限定在本地代码、知识/规则说明和回归验证。

## Target Outcome
本地体系能识别过期或不完整来源、避免 K01 静默切换口径，以一致且可审阅的规则保护报告动作；知识变更和指标评估有清晰证据；早午晚报职责明确；成交反馈能区分做 T、调仓、估算与确认。

## Goal Definition
- **Type**: technical / quality
- **Boundary**: 实施审核项 1–4、6–7；更新受影响的 HANDOFF、README、知识索引/指标说明、策略规则及 changelog；创建并推送本地工作分支。
- **Non-goals**:
  - 不实施第 5 项定时任务、失败重试、送达监控；不接触云端配置或真实连接器。
  - 不更改持仓、投资范围、仓位规则、指标阈值或用户数据；策略冲突只显式列明和保护，不替用户裁定。
  - 不引入数据库、RAG、通用指标平台或新依赖。
- **Deferred work**: 云端测试与定时运行；积累足够前瞻样本后评估指标有效性；用户裁定当前策略/K05 冲突后再改正式业务口径。
- **Verification rule**: 离线 fixture 覆盖缺数、旧时间、显式来源失败、非法动作、有效观测日及成交分类；部分实现为事后补测，新增边界按红绿验证。运行历史面板回归及文档/代码检查。
- **Evidence source**: Python 测试、`tools/panel_check.py`、离线合成输入、代码审阅、Git diff 与远端分支状态。
- **Pass criteria**: 新测试通过；历史报告解析/渲染兼容；不得访问真实交易/云端；用户既有工作文件未进入提交；目标分支已推送且工作分支提交仅含本任务文件。
- **Confidence note**: 离线测试可验证代码契约，不能证明腾讯连接器的实时质量或投资收益；云端由用户自行验证。
- **Judgment owner**: 用户审核规则与交付，自动测试判断代码约束。

## Current State
- 连接器快照以本机写入时间参与 TTL；资金流缺字段可折成 0；汇总允许部分市场数据。
- K01 的显式 `--mx` 仍存在备用源回退，且写前清除当日旧行。
- `fwd20` 以记录序号而非交易日计算；信号台账少量样本，指标因果/增量有效性尚无足够证据。
- 报告决策 JSON 直接渲染；知识文档和规则已有层级，但来源、生效版本、冲突裁决及撤销流程不统一。
- 早/午/晚报功能已有；成交、持仓及做T台账仍有人工维护和估算记录。
- 工作区含用户未提交的 `.gitignore`、`data/`、`signals.md` 与未跟踪 `reports/` 改动，必须完整保留且不纳入提交。

## Priority Rationale
- 先保证数据和 K01 来源边界，避免错误输入被后续流程当成有效事实。
- 再验证动作结构及指标评估口径，保护输出并避免小样本被误读为有效性证明。
- 最后固化知识生命周期、日报分工和成交归因；这些依赖可信的输入和明确的业务边界。

## Assumptions and Open Decisions
| Item | Status | Impact | Owner / Next step |
|---|---|---|---|
| K00 范围、既有策略例外和 K05 宏观否决冲突 | unresolved | 不得由实现者更改投资含义 | 保留现行文档并列出冲突/人工裁决要求；用户后续定口径 |
| 缺失/过期数据时报告能否生成 | confirmed | 报告可降级；午报动作必须有完整有限五档资金流，早报校验持仓、结构和关键位 | 仅实现基础门禁，尚未校验全部交易规则与数据依赖 |
| 第 5 项定时交付闭环 | excluded | 防止触碰运行/调度配置 | 无实现；云端由用户测试 |
| 用户在场的未提交数据 | confirmed | 避免覆盖或推送个人数据 | 不重置工作区；提交前按路径审阅暂存区 |

## Phases

### Phase 1: 可靠数据与可验证信号
- **Purpose**: 把时间、缺失、来源和交易日语义显式化。
- **Entry condition**: 用户授权已明确；本地分支已从当前主分支创建。
- **Phase rules**:
  - 不改金融指标定义和阈值；失效快照只能报告为不可用，不能以写入时间冒充新鲜。
  - 显式指定 K01 快照时禁止悄悄改用其他数据源；完整校验成功前不覆盖现有日记录。
  - 文档同步说明源时间、统计日期、采集时间及待积累样本限制。
- **Todos**:
  - [x] 为源时间校验和缺值处理添加测试并修正快照/汇总路径。
    - **Surface**: `mx_snapshot.py`, `brief.py`, `tests/`
    - **Proof**: 固定旧源时间 fixture 被拒绝；缺字段保留未知状态；部分覆盖标注范围。
    - **Depends on**: none
  - [x] 为 K01 显式来源和分文件安全替换添加测试并修复；两个文件不是联合事务。
    - **Surface**: `etf_flow.py`, `tests/`
    - **Proof**: 缺/坏快照不触发备用源、不删旧记录；有效来源可写。
    - **Depends on**: source schema inspection
  - [x] 用 K04 后续第 20 个有效观测日计算 fwd20；缺日延长周期，不混入 K05 月频数据。
    - **Surface**: `brief.py`, `signals.md` contract, `tests/`
    - **Proof**: 按日期去重排序；未成熟行留空；非有限点位不参与计算。
    - **Depends on**: signals schema inspection
- **Exit proof**: phase tests and existing report parsing checks pass.
- **Stop condition**: 如果正确拒绝数据会改变现存报告口径，先提供清晰降级状态，不猜测替代值。

### Phase 2: 规则/知识生命周期与指标评估
- **Purpose**: 让规则、知识采纳和指标验证可追溯，且不代替用户决定投资口径。
- **Entry condition**: Phase 1 exit proof passes。
- **Phase rules**:
  - 不自动将参考资料升级为交易规则，不静默解决现存规则冲突。
  - 指标评估只区分样本内发现与样本外验证，不声称产生盈利证明。
- **Todos**:
  - [x] 校验报告决策 JSON 的标的/结构/数据依赖约束并增加拒绝测试；不声称覆盖全部交易规则。
    - **Surface**: `report_gen.py`, `evening_gen.py`, `strategy_rules.md`, `tests/`
    - **Proof**: 非法标的/状态/方向/缺少关键位被拦截；合法旧决策渲染兼容。
    - **Depends on**: 明确现有 JSON schema 和文档硬约束
  - [x] 建立知识来源、状态、版本、生效/复核/撤销条件模板及冲突标记。
    - **Surface**: `knowledge/INDEX.md`, `knowledge/K00–K05`, `strategy_rules.md`
    - **Proof**: 现有条目可识别正式规则、观察观点、证据与失效条件；不改阈值。
    - **Depends on**: none
  - [x] 定义信号前瞻评估口径和最小可复核记录，不对小样本下结论。
    - **Surface**: `knowledge/INDEX.md`
    - **Proof**: 文档要求记录样本内外、版本、可用样本和基准；未实现自动回测。
    - **Depends on**: Phase 1 signal dates
- **Exit proof**: validator and signal tests pass; docs explicitly retain unresolved business decisions.
- **Stop condition**: JSON schema cannot表达/校验规则且需要改变交易规则时，停在提示层并记录待裁定项。

### Phase 3: 报告职责、成交闭环与交付
- **Purpose**: 使三报互补、成交反馈可归因并完成文档交接。
- **Entry condition**: Phase 2 exit proof passes。
- **Phase rules**:
  - 报告只重排已有事实，不增加新的买卖建议或未授权的风险阈值。
  - 成交未确认时保留为估算/待核；做T和调仓收益分开统计。
  - 排除第 5 项；不运行生产日报、不修改云端任务。
- **Todos**:
  - [x] 明确早盘计划、午盘变更/作废、晚间复盘/待核职责；计划关联为记录约定，未自动校验。
    - **Surface**: `strategy_rules.md`, `HANDOFF.md`, report prompts/contracts if present locally
    - **Proof**: report templates and handoff steps agree; existing HTML regression passes.
    - **Depends on**: Phase 2 rules boundary
  - [x] 增加成交最小记录规范，并按确认状态和类型分开汇总。
    - **Surface**: `trades.md`, `data/t_ledger.json` schema if tracked, reporting code, `tests/`
    - **Proof**: 未确认记录不计入已确认收益；做T与调仓分开。程序不自动对账双腿及费用。
    - **Depends on**: inspect current trade schema
  - [x] 同步项目文档、变更记录，复核提交范围并推送分支。
    - **Surface**: `readme.md`, `HANDOFF.md`, knowledge docs, `changelog.md`, branch
    - **Proof**: tests and diff pass; commit excludes pre-existing user data/reports; remote branch exists.
    - **Depends on**: all implementation phases
- **Exit proof**: local automated checks pass; branch pushed; cloud/live-data validation is explicitly left to user.
- **Stop condition**: if no authenticated GitHub push is available, preserve local commits and report the remote error.

## Dry-Run Findings
- Source timestamps differ by connector data kind; freshness policy must use available source fields and otherwise reject/mark unknown, not invent a universal TTL.
- Early/noon/evening reports have distinct input cadence, so no shared scheduler change is needed for report-role improvements.
- Current strategy rules contain unresolved scope and indicator-authority contradictions. Implementation may expose and guard them, but cannot select a new business interpretation.
- Signal history is too small for performance claims; the first deliverable is a leakage-safe evaluation method and explicit maturity status.

## Final Validation
- `python -m unittest discover -s tests -v`：26 项通过；`python -m py_compile brief.py etf_flow.py evening_gen.py mx_snapshot.py panel.py report_gen.py` 通过。
- `python tools/panel_check.py --ref 36ac9d9`：扫描 17 份历史面板，实际比较 16 份早午盘（6 早、10 午），解析完全等价。4 份历史早报缺关键位、1 份午盘错误日志缺核心段，均为既有缺项；晚报另测。
- 用 2026-11-18 历史三报在临时目录执行 CLI，均通过基础校验与渲染；与修改前渲染器使用相同输入的 HTML 逐字节一致，未覆盖 reports。
- 面板读取与直接文本解析渲染一致；午报走匹配的 JSON 快照，早报/晚报因已有指纹不符回落文本，不能视为三种 JSON 快照路径都已验证。
- `python tools/ab_blocks.py`：退出 1，共 7 处预期差异（3 个正常 market 模式、4 个退化输入），对应缺源日期/缺数据改为未知及新增覆盖提示。margin、news 及其余退化输入保持一致，详见 HANDOFF 验证约定。
- `git diff --check` 通过。提交仅包含本任务显式路径；用户原有数据、signals、.gitignore 与 reports 排除在外。目标分支为 `codex/investment-system-hardening`，提交与远端一致性由推送后 Git 核对。
- 未运行真实连接器、云端或定时任务；云端由用户测试。计划关联、费用记录和前瞻评估为文档规范，尚未实现自动对账或自动回测。

## First Execution Step
Inspect source timestamp fields, K01 write order, signal schema, decision JSON schema and trade ledger; then add failing tests for Phase 1.

## 测试证据

- **目标**：缺源日期不得汇总两市、动作字段为 null 时拒绝；非有限信号点位不得回填；跨日不完整主池不得拼成 K01 基准。
- **RED**：`python -m unittest discover -s tests -v` 分别观察到缺日期仍输出总额、null 动作未拒绝；随后观察到无限点位算出 -100%、跨日拼接基准输出 100。失败均对应缺失的目标行为。
- **GREEN**：增加源日期与空值检查、有限数值检查，并让摘要与 K01 主池同日基准一致；同一命令 26 项通过。
- **REFACTOR**：未进行独立重构；上述边界采用最小修正。其余已有实现部分为事后补测，不称为先红后绿。
