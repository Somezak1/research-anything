# Stage 1：领域化搜索计划与自适应深挖

本文件定义“搜什么、搜到何时停止”。字段与写入协议见 [log-format.md](log-format.md)，总结与用户确认见 [summarize.md](summarize.md)。

## 目录

- [先选领域 profile](#先选领域-profile)
- [广探测](#广探测)
- [自适应深挖](#自适应深挖)
- [证据格与停止条件](#证据格与停止条件)
- [预算和授权](#预算和授权)
- [呈现给用户](#呈现给用户)
- [计划对象](#计划对象)
- [v2 兼容](#v2-兼容)

## 先选领域 profile

主 Agent 根据 idea 选择一个基础 profile；拿不准时用 `generic`，不要为了分类提前追问用户。`finance`、`medical`、`legal`、人身安全或重大合规后果再叠加 `high-risk` overlay。

| profile | 必需来源类别 | 默认决策维度 |
|---|---|---|
| `technical` | 官方文档/价格/模型卡；论文或原始 benchmark；代码、release、issue；独立复现或代表性 POC；许可证与安全文档 | 任务质量、时延、吞吐、总成本、集成、运维、安全、许可、版本寿命 |
| `travel` | 运营方/政府当前页面；交通与预约；天气/季节；近期当地体验；地图距离与营业状态 | 可达性、时段、同行人限制、预约、天气、体力、费用、替代路线 |
| `policy-forecast` | 政策/会议/公司一手材料；历史基准率；利益相关方观点；反方证据；结果可观测时间点 | 已知事实、假设链、情景概率、催化剂、反证、时效 |
| `generic` | 至少一类一手事实源、一类独立实践源、一类失败/反例源 | 用户成功标准、成本、时间、难度、风险、可逆性 |

`high-risk` overlay 追加以下硬规则：

- 优先监管机构、官方记录、同行评审或有资质机构；社交内容只用于发现线索和体验，不承担最终结论。
- 不把事件预测直接转换为个性化交易、诊断、用药或法律行动。输出情景分析、缺失证据和专业复核点。
- 记录利益冲突、适用司法辖区/人群、数据日期；关键缺口存在时状态只能是 `pilot-only` 或 `blocked`。

## 广探测

广探测的目的不是凑条数，而是发现候选、术语、争议和证据缺口。

1. 对当前八个发现入口（douyin、xiaohongshu、zhihu、bilibili、youtube、github、twitter、web）各做一次**低成本探测**。连接器不可用时记录 capability failure，不要求用户为了形式覆盖去登录或承担封号风险。
2. 每个查询最多保留 3 个有效样本；探测阶段只取标题、正文/摘要、作者、日期、指标、URL 和判断相关性所需的最小内容，不做付费 ASR、全量 OCR 或全评论抓取。
3. 查询至少覆盖：领域/方案、近期或当前版本、失败/踩坑、替代/对标。领域 profile 规定的一手来源类别不应只靠社交平台命中。
4. 热度只是候选信号，不是质量排序。结果至少同时包含“近期”“负面/失败”“官方/原始”“独立实践”四种 strata；没有命中要显式记缺口。
5. 建立规范化候选表：产品/项目/地点的别名、版本、发布方和上游来源归一，避免同一方案跨平台重复占满名额。

## 自适应深挖

广探测完成后，**主 Agent**读取探测指针和证据格，制定下一批 deepening plan；不得把全局取舍外包给渠道 Agent。每批默认新增不超过 5 个 finding，仅针对以下理由之一执行：

- `critical-gap`：生产结论需要的一手事实、许可证、价格、安全或 POC 证据缺失；
- `contradiction`：独立来源对关键断言冲突；
- `new-candidate`：出现可能支配当前候选的新方案或新一代版本；
- `independence`：现有“多来源”实际同作者、同转载链或同一厂商材料；
- `freshness`：承重事实已超出该类事实的有效期；
- `user-constraint`：研究后用户补充的硬约束改变候选或排序。

每批必须写 `reason`、要关闭的 `decision_gap`、目标候选/claim、查询和上限。渠道 Agent 只执行这批任务，不自行无限扩面。发现新的关键缺口时返回给主 Agent，由主 Agent决定下一批。

## 证据格与停止条件

每个候选按决策维度建立 evidence grid，单元格必须为以下状态之一：

- `supported`：证据类型达到该 claim 的阈值；
- `contested`：存在未裁决的独立冲突；
- `missing`：尚无证据；
- `not-applicable`：附理由；
- `to-test`：只能通过代表性 POC 验证。

来源阈值：

| claim 类型 | 最低要求 |
|---|---|
| 当前价格、接口、版本、营业/预约、许可证 | 对应官方当前页面或原始文件；记录抓取时间与适用范围 |
| 历史事实 | 原始记录或两个独立可靠来源；无法取得原始记录时说明降级 |
| 质量、性能、体验 | 一手规格只能证明“发布方如此声称”；生产推荐还需独立复现、真实用户证据或代表性 POC |
| 趋势、预测、因果链 | 明确拆分事实与假设；至少一个反方信号和可证伪条件，不给伪精确确定性 |
| 高风险行动 | 满足 overlay 的权威性与适用性要求，并由合适专业人员复核 |

仅在以下条件同时满足时停止深挖：

1. 所有硬约束对应的关键 evidence grid 单元格已 `supported`，或被诚实标成会阻止生产推荐的 `missing/contested/to-test`；
2. 连续两批没有新增候选、没有新增会改变结论的 claim、没有改变候选排序；
3. 独立性与新鲜度检查通过；
4. 未触及用户批准的时间、现金、账号或 ASR 硬预算。

预算先耗尽不是“调研完成”。此时停止调用，状态降为 `pilot-only` 或 `blocked`，列出最小补证动作。

## 预算和授权

计划分别展示并记录：人类等待时间、Agent/模型预算、连接器调用、付费 ASR 时长和人民币上限、登录/验证码、账号封禁风险、磁盘和媒体下载量。probe 前的 P50/P90 只能依据本机 `doctor` 的能力可用性、计划查询/条数上限和明确写出的历史假设，标注低置信度；不得声称来自尚未执行的 probe。probe 后用真实任务耗时修订估计；修订不能突破已批准硬上限，不使用固定的“30–60 分钟”承诺。

- 批准计划不等于批准付费服务、登录、验证码或高风险账号操作；每类授权单独记录。
- `account_actions=true` 必须同时传 `accountAuthorizationEventId`，指向用户明确同意具体账号/验证码/封号风险的原话事件；否则 connector 只能无账号探测或申报 blocked。
- 付费 ASR 采用硬上限。未明确授权时上限为 0；预算管理器无法原子预留时不得发起任务。
- 实施顺序固定为：run 以零上限 `init` → 用户授权原话以 `event_type=user.asr-authorization` 调 `record-event` → 用该 event seq 调 `authorize-budget`。workflow 的非零 `budgets` 同时必须带 `budgetAuthorizationEventId`；其它 user event 类型不能替代。
- 搜索范围批准也必须先以 `event_type=user.search-scope-approval` 调 `record-event`，再把 event seq 作为 `scope_approval_event_id` 调 `set-plan`。同一批准事件只能复用于 scope hash 相同、仅估时/deepening 改变的 revision；基础查询、维度、入口启停或硬预算变化必须重新批准。
- 连接器许可证和服务条款属于计划约束。MediaCrawler 的公开许可证仅允许非商业学习/研究；商业或工作研究不得默认启用，除非取得另行授权并记录依据。

## 呈现给用户

```markdown
# 搜索计划：<idea 原话>

- profile: <technical|travel|policy-forecast|generic>；overlay: <none|high-risk>
- 已知约束：<原话引用，不补写>
- 决策维度：<维度>
- 必需来源：<profile 要求及当前缺口>

| 入口 | 探测查询 | 寻找的信号 | 探测上限 | 深挖条件 |
|---|---|---|---:|---|
| web | 当前版本 / 官方价格 / 失败案例 | 一手事实、独立评测、反例 | 3/查询 | critical-gap / freshness |
| … | … | … | 3/查询 | … |

探测后会展示候选版图和新浮现的约束，再由主 Agent 选择深挖批次；不会预先给每个平台平均抓 15 条。
预算上限：时间 <…>；ASR <…小时/…元>；账号动作 <允许/不允许>。
请批准搜索范围，并分别确认任何付费或账号授权。
```

rough idea 在搜索前只需消除主题歧义；预算、质量、速度等用户尚不知道的问题留到看完候选版图后再问。

## 计划对象

批准后由 `researchctl` 写入，不让 Agent 手改 manifest 或 JSONL。v3 的逻辑形状如下；实际 CLI 参数以 `researchctl <command> --help` 为准。

```json
{
  "plan_version": 3,
  "profile": "technical",
  "risk_overlays": [],
  "dimensions": ["quality", "latency", "cost", "license"],
  "source_requirements": ["official", "independent-test", "failure-signal"],
  "estimates": {
    "p50_minutes": 30,
    "p90_minutes": 90,
    "basis": ["doctor 能力快照 + 每查询最多 3 条；尚无 probe 实测"]
  },
  "budgets": {"wall_minutes": 120, "asr_seconds": 3600, "asr_cost_cny": 1, "account_actions": false},
  "scope_approval_event_id": 6,
  "budget_authorization_event_id": 7,
  "account_authorization_event_id": null,
  "account_action_scope": [],
  "channels": [{
    "name": "github",
    "signals": ["candidate", "maintenance", "license", "issues"],
    "probe": {"queries": ["…", "… alternatives", "… issues"], "limit_per_query": 3}
  }],
  "deepening": [{
    "channel": "github",
    "reason": "critical-gap",
    "decision_gap": "候选 A 是否允许商用且仍维护",
    "queries": ["…"],
    "candidate_ids": ["cand_…"],
    "limit": 5
  }]
}
```

`channels` 必须恰好含八个入口各一次；不可用入口仍保留，并用 `enabled:false` + 非空 `disabled_reason` 记录 capability/license/account failure，不能从计划中静默删除。先运行 `record-event` 保存用户搜索范围批准原话，再执行 `researchctl set-plan --db <DB> --input <PLAN_JSON>`；普通 event 不能替代经过 schema 校验的 canonical plan。

广探测后先以 `phase=probe` 调 workflow；主 Agent 根据结果回填 `deepening`，再以 `phase=deepen` 调用。证据补全使用 `phase=audit`，最后调用 `researchctl gate` 与 `export` 对账。export 直接写 `<OUT_DIR>`，让 renderer/validator 与同一 revision 的 decision、findings、events、report 和 runbook 在同一目录校验；不要另造一份手工 exports 副本。

## v2 兼容

旧输入和旧 run 只允许用 `scripts/audit_v2.py` 只读审计。不得由 workflow 静默映射成已批准 v3 plan；继续研究时必须重新展示 v3 八入口范围，保存新的用户批准事件并调用 `set-plan`。旧文件不原地改写。
