# Stage 3：全量消费、需求收敛与生产门禁

执行者是主 Agent。渠道 Agent 负责忠实采集；术语核查和单点证据任务可并行委派，但通读、候选归一、决策、与用户沟通及最终责任不外包。

## 目录

- [输入预检](#输入预检)
- [分块消费全部笔记](#分块消费全部笔记)
- [候选、术语与 claim](#候选术语与-claim)
- [第一次综合](#第一次综合)
- [与用户沟通和 decision contract](#与用户沟通和-decision-contract)
- [约束驱动的第二次研究](#约束驱动的第二次研究)
- [POC 与决策状态](#poc-与决策状态)
- [交付前门禁](#交付前门禁)
- [无交互模式](#无交互模式)

## 输入预检

1. 运行 `researchctl status`，确认 probe/deepen/audit task 的状态、真实耗时、费用和当前 revision。
2. 运行 `researchctl gate` 的 evidence/collection 检查。`missing`、`partial`、未结算预算或 stale artifact 不得被“格式校验通过”覆盖。
3. 检查领域 profile 的必需来源类别、关键 evidence grid、connector failure 和 freshness；来源数量多不等于覆盖要求已满足。
4. 把失败分为：会改变决策的 critical gap、降低置信度的 material gap、只影响附录的 minor gap。critical gap 必须补研或最终阻断。

命令参数随当前 `researchctl <command> --help`，不手写或修补数据库、coverage、manifest、ledger。

## 分块消费全部笔记

主 Agent 不直接 Read 数据库或超长 JSONL。反复调用 `researchctl project-notes`，按不超过约 8k tokens 的批次取得只读投影；每批包含 stable finding ID、revision/hash、headline、note、来源/日期/作者、capture、unknown terms 和必要 locator，不默认带全文。

对**每个 finding**给出且仅给出一种主 disposition：

- `supports:<claim-id>`：支持某原子 claim；
- `refutes:<claim-id>` 或 `qualifies:<claim-id>`：形成反证/限定；
- `candidate:<candidate-id>`：只用于候选发现，尚无可承担 claim 的证据；
- `follow-up:<gap-id>`：需要反查全文、补证或去重；
- `excluded:<reason-code>`：重复、过时、无关、仅营销、无法读取等，附具体理由。

用 `researchctl ack-notes` 提交批次 cursor、输入 hash 和逐条 disposition。只有 ack 成功才取下一批；输入 revision 改变则旧 ack 失效并重新消费受影响批次。最终 gate 必须证明 100% finding 已 ack 或显式排除，不能用“我读过了”替代机器账本。

需要核对原话时，通过 `project-notes` 的单 finding/full-content 模式按 stable ID 反查；不能回忆 note，也不能为了省 token 静默只读热门子集。材料超出剩余上下文时保存 ack/checkpoint、压缩自己的中间综合，再从 cursor 继续；这不是丢弃输入。

## 候选、术语与 claim

### 候选归一

建立 candidate registry：规范名称、别名、版本/日期、发布方、适用场景、当前状态和被替代关系。同一产品不同版本不可混成一个性能结论；同一方案跨平台出现不可当多个候选。

### 术语卡

只给以下词建卡：出现在用户问题/选项/候选名称中、会改变约束或比较、决定代际、承载关键 claim。先合并别名和误识别，再查询官方发布、论文或运营方页面。普通词、作者标签和与决策无关的 unknown term 记录为排除，不为“全量术语”无限派 Agent。

术语卡至少包含 `term/aliases/what/released/by/version/supersedes/sources/as_of`。不认识且影响选择的词没有核清前不得进入问题或推荐。

### claim registry

把结论拆为原子 claim，按 `fact/quality/performance/experience/forecast/causal/constraint` 分类，并标 `load-bearing/supporting/context`。先调用 `upsert-evidence-cluster`：输入的每个 finding fingerprint 在 `members` 中恰出现一次，member 必须有非空 `quote`、非空 `locator`，并尽力写 `author_id/upstream_id/source_class`；再把返回的 `evc_…` ID 交给 `upsert-claim`。缺精确 span 的 finding 只能留作背景，不能进入承重 cluster。

核查方法：

- 价格、许可证、接口、版本、预约等事实查当前官方原文；
- 品质/性能不把厂商 benchmark 当独立验证，找独立复现、真实问题或执行代表性 POC；
- 体验类保留场景与作者条件，不把个例写成普遍规律；
- 预测拆成已知事实、假设链、反证和可证伪时间点；
- 同源转载、同作者跨平台和共同引用一个厂商材料可以同处一个 evidence cluster，但 `independent_source_count` 只按真实独立上游计数；成员数量不能冒充独立数量。

并行核查使用有上限的 task queue 和稳定 task/claim ID；Agent 只返回一个 claim 的结构化结果，由 `researchctl` 单写入。禁止多个 Agent 共享追加同一个 JSONL，也禁止核查 Agent 递归扩成新一轮全量调研。

## 第一次综合

在询问用户前，主 Agent 必须形成以下地图：

1. 候选版图与代际时间线；
2. profile 的 evidence grid；
3. 独立交叉印证、冲突和反例；
4. 当前约束下可能的默认路径及会令其改变的条件；
5. 用户一开始通常不知道、但确实改变选择的约束，例如持续成本、处理时间、合规、维护、体力或预约风险。

此时只形成 provisional analysis，不输出最终推荐。关键 claim 没证据时写 gap，不根据模型常识补齐。

## 与用户沟通和 decision contract

无论初始需求 rough 或 refined，最终方案前都必须与用户完成沟通。所有 Agent 文本和用户回复都先经 `record-event` 保存**完整原话**；若由主 Agent 代另一个 Agent 传话，必须逐字转发原消息，不总结、补充或推断。

### 第一段：讲清再答疑

先给用户：

1. **名词/地点速览**：只覆盖后续候选、问题和选项会用到的术语；每项说明是什么、属于哪类/在哪里、为何相关。
2. **独立交叉印证**：每条包含 claim、至少两个独立 cluster 的 finding ID；同源转载不计。单一来源另列，不能混入“已印证”。
3. **争议与未知**：把可能改变选择的冲突、证据缺口和 POC 要求说清。

正文完整呈现后，用纯文本询问用户是否有要先弄清的问题。回答到用户明确表示没有疑问；“继续”只表示继续，不自动解释为“无疑问”。

### 第二段：高信息增益问题

每轮问 1–3 个会改变候选、排序或生产门禁的问题；选项用用户已看懂的概念，显示默认值及其后果。问题来源只能是研究中真实出现的分歧、新约束或证据/POC 取舍，不为了走流程问不影响结论的问题。

### decision contract

用户回答原话记录后，主 Agent另建结构化 contract：

```json
{
  "hard_constraints": [{"name":"…","operator":"<=","value":"…","derived_from_event_seq":8}],
  "preferences": [{"dimension":"…","priority":1,"derived_from_event_seq":8}],
  "success_metrics": [{"metric":"…","threshold":"…","test":"…"}],
  "risk_tolerance": "…",
  "time_horizon": "…",
  "approved_costs": [],
  "unresolved": []
}
```

把结构化 contract 序列化为 JSON，先逐字保存为 `event_type=agent.decision-contract`、`actor=main-agent` 并把完全相同的文本展示给用户；用户纠正时新建 revision，不覆盖旧展示事件。明确确认回复再逐字保存为独立的 `event_type=user.decision-confirmation`。`set-decision` 同时绑定 `contract_presentation_event_id` 与 `user_confirmation_event_id`，并核对展示文本、结构化 contract 和 hash；只有该 revision 能驱动最终筛选。该确认必须发生在全部 finding 处置、证据修订、计划修订和预算结算之后，并紧接 `set-decision`；确认后任何新的研究输入都会令 decision stale，必须重新展示更新后的 contract 并取得新的确认。Agent 不得把沉默、模糊回答或自己的默认值标成用户原话，也不得复用 scope/ASR/账号授权事件代替 decision confirmation。

`set-decision` 的核心输入形状如下；`contract_verbatim` 是上面展示事件中的同一段 JSON 字符串，不是用户确认回复：

```json
{
  "contract_verbatim": "{...完整 decision_contract JSON...}",
  "decision_contract": {"hard_constraints":[],"preferences":[],"success_metrics":[],"risk_tolerance":"…","time_horizon":"…","approved_costs":[],"unresolved":[]},
  "contract_presentation_event_id": 11,
  "confirmed": true,
  "user_confirmation_event_id": 12,
  "requested_status": "production-ready|pilot-only|blocked",
  "selected_candidate_id": "cand_…",
  "recommendation": {}
}
```

非 blocked 至少给出一个可测试的 `success_metrics`；production-ready 的 `unresolved` 必须为空。有 recommendation 时 `selected_candidate_id` 必须指向当前 candidate registry 中的 active candidate。blocked 不放 recommendation，并改给 `blockers`/`next_research`。

## 约束驱动的第二次研究

确认 contract 后重新过滤候选和 evidence grid。满足任一条件就必须回到 Stage 2，创建 reason=`user-constraint` 的 targeted deepening task：

- 新硬约束淘汰原候选或改变第一名；
- 新场景使现有 benchmark/体验不再适用；
- 用户要求的价格、地区、版本、时间点或许可没有当前一手证据；
- 约束暴露新的安全、合规、可维护性或旅行可达性缺口；
- 需要在真实输入上比较候选。

二次研究只搜关闭这些 gap 所需的来源，不重跑所有渠道。完成后重新消费新增/变更 finding、更新 claim 和 contract impact，并再次运行 gate。若结果引出新的会改变选择的用户取舍，再进行一轮短沟通；不能拿第一次问答当永久授权。

## POC 与决策状态

生产实践中的质量、性能、稳定性和集成难度通常不能靠网页证明。对最终候选定义最小代表性 POC：真实输入样本、基线、指标阈值、成本/时延记录、失败注入、通过/停止条件和回滚。把实际结果写入 `<OUT_DIR>/artifacts/` 的非空文件，用 `upsert-artifact` 核对并登记 `kind=poc-result` 与 SHA-256，再把返回的 `art_…` ID 放进 `poc.artifact_ids`；没有该真实 artifact 不能填 `result=passed`。没有权限或预算执行 POC 时不要假装验证完成。

最终状态只能是：

### `production-ready`

同时满足：confirmed decision contract；所有硬约束；每个承重 claim 达到类型阈值并可精确定位；来源独立性/时效通过；生产相关品质已经代表性 POC 或等价独立复现；成本、安全、许可、运维、回滚均闭环；无 critical gap。

### `pilot-only`

已有可行候选，但仍有仅能实测的品质/集成问题、material gap 或 contract 未完全量化。必须给出有边界的 POC、成功阈值、预算和禁止扩大生产使用的条件；不能把同一方案在摘要中称为“生产推荐”。

### `blocked`

关键需求未确认、关键事实/许可/安全证据缺失、预算/权限不足、来源严重冲突，或没有候选满足硬约束。列出最小解阻动作，不为了“必须给答案”选择次优方案。

高风险 overlay 默认至少需要适当专业复核。金融场景不输出针对个人的买卖时点/标的，医疗/法律场景不替代专业诊断或意见；即使材料丰富也不越过该边界。

## 交付前门禁

运行 `researchctl gate`，至少检查：

- 全 finding disposition coverage = 100%；
- 所有承重 claim 有精确 evidence locator，且独立性与 freshness 满足阈值；
- contract revision 已由真实 user event 确认；
- 预算已 settle，未知账单、重复媒体和超限为 0；
- report/runbook 只引用当前 decision export revision；
- 用户选择、候选、步骤、数字、open questions 在所有交付物一致；
- HTML 已转义，不可信内容不能形成脚本、属性或命令；
- delivery status 与 gap 相符。

gate 失败就修复、补研或降级；禁止让报告生成器自行“合理化”失败。

## 无交互模式

没有人回答时仍完成探测、证据地图和问题清单，但不得伪造 user event 或 confirmed contract。若输入中已有明确、可追溯且足够的 contract，可按其执行；否则最高只能 `pilot-only`，关键硬约束未知时为 `blocked`。把每个 open question 的影响和默认分支写进 decision export，但默认分支是 Agent 假设，不是用户选择。
