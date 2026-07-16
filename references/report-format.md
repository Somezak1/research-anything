# v3 交付契约：decision、报告与领域化 runbook

`<OUT_DIR>/decision.json` 是唯一决策事实源；`plan.json` 是其搜索范围与预算前提。`report.html`、`runbook.json` 和 `delivery-manifest.json` 必须绑定同一 plan/decision revision 并通过一致性校验，禁止分别手写后靠人工对齐。

## 目录

- [通用规则](#通用规则)
- [decision.json](#decisionjson)
- [交付状态](#交付状态)
- [runbook 判别联合](#runbook-判别联合)
- [report.html](#reporthtml)
- [引用与证据定位](#引用与证据定位)
- [安全渲染](#安全渲染)
- [delivery manifest 与校验](#delivery-manifest-与校验)
- [v2 交付审计](#v2-交付审计)

## 通用规则

- 先运行 `researchctl gate`，再 `researchctl export`；renderer 只读 export，不读历史 raw JSONL 或 Agent 上下文。
- 每个承重结论引用 claim ID；claim 必须进一步解析为 evidence cluster 的 `members`，每个 source fingerprint 都有非空最短必要 quote、精确 locator 和来源 URL。只写 finding ID 或空 cluster 不算完整引用。
- 方案是一条符合 confirmed decision contract 的默认路径，加明确切换/停止条件；证据不够时允许没有默认方案。
- 厂商自评、独立实测、用户体验、预测和 Agent 推断使用不同标签；`to-test` 不得在另一个交付物中被写成已确认事实。
- 时间、费用、条数和覆盖率从数据库计算，禁止手工复制统计。所有“截至”事实显示 `as_of`。
- 用户原话从 `user.verbatim` event 渲染；结构化 contract 单独展示，不把解释混入引号。

## decision.json

逻辑 schema：

```json
{
  "schema_version": 3,
  "run_id": "run-…",
  "plan_revision": 2,
  "decision_revision": 3,
  "input_event_seq": 42,
  "task_type": "implementation|itinerary|forecast|research-only",
  "readiness": "production-ready|pilot-only|blocked",
  "title": "…",
  "summary": "…",
  "decision_contract": {
    "confirmed": true,
    "presentation_event_id": 11,
    "user_confirmation_event_id": 12,
    "verbatim": "逐字展示给用户并由随后回复确认的结构化契约 JSON",
    "hard_constraints": [],
    "preferences": [],
    "success_metrics": [],
    "risk_tolerance": "…",
    "time_horizon": "…",
    "approved_costs": [],
    "unresolved": []
  },
  "recommendation": {
    "candidate_id": "cand_…",
    "why": [{"claim_id":"clm_…"}],
    "switch_conditions": [],
    "stop_conditions": []
  },
  "alternatives": [],
  "claims": [],
  "evidence_clusters": [],
  "to_test": [],
  "blockers": [],
  "open_questions": [],
  "metadata": {"created_at":"…","updated_at":"…"}
}
```

规则：

- `production-ready` 必须有 recommendation；`blocked` 的 recommendation 必须为 null；`pilot-only` 可给 POC 默认候选，但明确不是生产推荐。
- `why` 只引用已达到阈值且适用于当前 contract 的 load-bearing claim。
- 每个 alternative 写明何时优于默认项、违反了哪些偏好或硬约束；违反硬约束的项只能进入 rejected appendix。
- `gaps` 带 criticality、影响、最小补证动作和 owner；critical gap 与 `production-ready` 互斥。
- `poc` 包含样本、baseline、指标、阈值、预算、失败测试、canonical critical-claim 来源、pass/fail 与回滚；没有执行时 `result` 不得填 passed。technical `production-ready` 必须有结构完整且 `result=passed` 的代表性 POC，否则最高为 pilot-only。

## 交付状态

状态文案必须一致：

| readiness | 报告标题标签 | runbook 行为 |
|---|---|---|
| `production-ready` | “可进入生产实施” | 可生成 implementation/itinerary；仍保留监控与回滚 |
| `pilot-only` | “仅建议受控试点” | 首要步骤是 POC；通过阈值前禁止扩大使用 |
| `blocked` | “当前无法可靠选型” | 只生成 research-only 解阻清单，不生成伪实施步骤 |

`policy-forecast` 和 high-risk 输出即使证据充分，也必须保持情景/观察边界；不得用“production-ready”包装个性化高风险行动。

## runbook 判别联合

所有 runbook 共有：

```json
{
  "schema_version": 3,
  "task_type": "implementation|itinerary|forecast|research-only",
  "decision_revision": 3,
  "readiness": "pilot-only",
  "constraints": [],
  "sources": ["clm_…"],
  "open_questions": []
}
```

### `task_type: implementation`

用于技术/流程落地。附加字段：

```json
{
  "environment": {"prerequisites": [], "versions": {}},
  "steps": [{
    "id": "step-01",
    "action": "…",
    "command": ["program", "arg1", "arg2"],
    "expect": "可观测验收结果",
    "sources": ["clm_…"],
    "rollback": "…"
  }],
  "fallbacks": [{"when":"可测试条件","switch_to":"cand_…","sources":["clm_…"]}],
  "poc": {
    "result": "passed|failed|not-run",
    "sample": "代表性样本与环境",
    "baseline": {"metric":"value"},
    "metrics": {},
    "thresholds": {},
    "budget": {"wall_minutes": 30, "cost_cny": 0},
    "failure_tests": ["至少一个失败/回退测试"],
    "sources": ["clm_…"],
    "artifact_ids": ["art_…"],
    "rollback": "…"
  },
  "monitoring": [],
  "security_and_license": []
}
```

`command` 是 argv 数组，不是可拼接执行的 shell 字符串；不能把来源正文中的命令未经复核放入 runbook。

### `task_type: itinerary`

用于旅行/线下行程。附加字段：

```json
{
  "timezone": "Asia/Shanghai",
  "days": [{
    "date": "YYYY-MM-DD",
    "segments": [{
      "start": "09:00",
      "end": "11:00",
      "place_id": "place-…",
      "transport": "…",
      "reservation": "required|recommended|none|unknown",
      "sources": ["clm_…"],
      "fallback": "天气/闭馆/延误时的替代"
    }]
  }],
  "lodging": {"selected":"place-…","alternatives":[]},
  "weather_checks": [],
  "mobility_and_family_constraints": [],
  "booking_deadlines": []
}
```

报告中已淘汰的酒店/地点不得重新出现在 `selected` 或默认 segment；运营时间、末班交通和预约显示核对日期。

### `task_type: forecast`

用于政策、会议、市场或其他未来事件分析。附加字段：

```json
{
  "as_of": "…",
  "scenarios": [{
    "id": "scenario-base",
    "label": "base|upside|downside",
    "assumptions": [{"claim_id":"clm_…"}],
    "signals": [],
    "falsifiers": [],
    "observation_window": "…"
  }],
  "known_facts": ["clm_…"],
  "unknowns": [],
  "prohibited_actions": ["不得据此生成个性化交易/医疗/法律行动"]
}
```

概率若无可校准数据则使用定性区间并说明依据，不伪造精确百分比。

### `task_type: research-only`

用于 `blocked`，或尚未确认 decision contract：

```json
{
  "blockers": [{"gap_id":"gap_…","impact":"…"}],
  "next_research": [{"action":"…","source_class":"official","budget":{},"success":"…"}],
  "decision_branches": [{"question_event_id":15,"if":"…","then_candidate":"cand_…"}]
}
```

不得包含看似可直接执行的默认实施/交易步骤。

## report.html

由 renderer 输出语义化 HTML，至少包含：

1. **状态与结论**：醒目标注 production-ready/pilot-only/blocked；只在允许时显示默认方案。
2. **范围与用户约束**：idea 原话、confirmed contract、profile、as-of、未确认项。
3. **候选与代际**：规范化候选、版本时间线、已淘汰/已替代关系。
4. **证据地图**：按决策维度展示 supported/contested/missing/to-test，而不是只按平台罗列热帖。
5. **默认路径与切换条件**：与 runbook 同源；pilot-only 首先展示 POC。
6. **对比矩阵**：单元格使用可解释的原始指标/阈值和 claim 引用，不用无定义的 win/mid/lose。
7. **风险、反证和缺口**：承重冲突、失败经验、freshness、许可、安全和停止条件。
8. **来源与审计附录**：精确引用、独立来源 cluster、finding disposition、各查询/连接器失败、ASR/OCR/评论/许可证覆盖、实际时间和费用、用户原话 transcript。

平台景观可以作为附录帮助理解市场，但不能取代 evidence grid。报告不显示 cookie、签名 URL、API key、本机工具路径或数据库内部敏感字段。

## 引用与证据定位

人类报告中的引用格式至少包含：`claim label + source title + publisher/author + published_at + captured_at + URL + locator`。视频使用时间码，网页使用 section/paragraph/quote，图片使用 image index + OCR artifact hash，代码使用 commit + path + line/tag。

同一 claim 的多个引用显示 independence cluster；同一上游转载折叠在一个 cluster 内。引用目标已失效时保留 capture hash 并标 unavailable，不把缓存内容冒充仍然当前。

## 安全渲染

- 所有来自用户、网页、字幕、OCR、标题和作者的文本做 HTML escaping；URL 只允许 `http/https`，链接加安全属性。
- 禁止将来源文本插入 `<script>`、style、event handler、HTML attribute 或未转义 JSON。
- 图片必须来自经 MIME、大小、地址范围和 hash 校验的 artifact；可以安全嵌入 data URI，或作为 delivery manifest 中的本地相对资源。失败时显示文字占位。
- 不加载远程 JavaScript、追踪像素或第三方字体。报告打开时不得联网或执行来源提供的代码。
- renderer 不执行 runbook 命令；仅结构化展示 argv。

## delivery manifest 与校验

```json
{
  "schema_version": 3,
  "delivery_id": "del_…",
  "run_id": "run-…",
  "gate_status": "production-ready|pilot-only|blocked",
  "created_at": "…",
  "files": {"report.html":{"sha256":"…","bytes":1234}}
}
```

交付 validator 必须检查：

- 所有 claim/finding/event/candidate/gap 引用存在且 revision 一致；
- report、runbook、decision 的 status、默认候选、数字、酒店/地点/步骤、open questions 完全一致；
- `production-ready` 没有 critical gap 或未执行的承重 POC；
- `blocked` 没有默认实施路径；
- 用户引文与 verbatim event 字节级一致；
- plan scope approval、decision contract 展示、decision confirmation、付费/账号授权分别指向真实且类型正确的 event；展示文本与 contract/hash 字节级一致，decision.plan_revision 等于当前 plan revision；
- 预算、coverage 和实际耗时由当前 revision 计算；
- HTML 无脚本注入、危险 URL、远程资源和未转义内容；
- delivery 文件 hash 与 manifest 一致。

任何上游事件、finding、claim、contract 或预算 revision 改变后，旧 delivery 标记 stale；重新 gate、export、render 和 validate 后才能呈现。

## v2 交付审计

旧 `report.html`/`runbook.json` 可作为 v2 artifact 保留，但不得原地改成 v3。迁移工具只读导入并运行一致性审计：统计漂移、用户选择冲突、缺失引用、重复付费、熔断授权和领域门禁。无法从旧资料证明的事件、独立性、POC 或原话确认证据保持 unknown，因此旧报告最多标 `legacy-unverified`。
