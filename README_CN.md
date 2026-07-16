<h1 align="center">research-anything</h1>

<p align="center"><b>让真实生产选型建立在可追溯证据上。</b></p>

<p align="center">一个面向 Claude Code 的调研 skill：可以从模糊问题出发，先看清真实的方案空间，再帮你补齐事前不知道该提供的约束，并阻止薄弱证据被包装成自信推荐。</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README_CN.md">简体中文</a> ·
  <a href="README_JA.md">日本語</a> ·
  <a href="README_KO.md">한국어</a> ·
  <a href="README_ES.md">Español</a> ·
  <a href="README_FR.md">Français</a> ·
  <a href="README_DE.md">Deutsch</a> ·
  <a href="README_PT.md">Português</a> ·
  <a href="README_RU.md">Русский</a>
</p>

> 比如你问“AI 漫剧应该怎么做”，一开始通常还不知道成本、生成时延、一致性、版权、可维护性等约束哪个最要命。research-anything 不会要求你凭空先回答完这些问题，而是先调研，再带着真实的候选、证据和矛盾帮你把需求收敛；证据不够时，它也必须明确说当前还不能用于生产。

## v3 做了什么改变

普通调研 Agent 往往收集一批链接，再把它们压缩成一段听起来合理的答案。v3 把调研改造成一套可恢复、可审计的决策过程：

1. **从用户的真实需求开始。** 初始需求和后续回答按原话保存，不由 Agent 改写成更方便处理的版本。
2. **先广泛探测。** 当前以抖音、小红书、知乎、B站、YouTube、GitHub、Twitter/X 和通用网页作为发现层。它们只是探针，不代表“搜完八个平台就是全面”。
3. **按价值自适应深挖。** 预算跟着新候选、矛盾、独立证据、时效性和承重证据缺口走，不再给所有渠道相同的固定配额。
4. **先讲清楚，再提问。** Claude 先解释陌生术语、候选版图、已交叉印证的结论、争议和未知项，再询问真正会改变选择的少数问题。
5. **约束改变答案时必须二次调研。** 新发现的预算、时延、许可、无障碍、安全或运维约束如果会改变排序，就触发针对性补研，不能拿第一次宽泛搜索直接回答已经收窄的新问题。
6. **执行生产门禁。** 最终状态只能是 `production-ready`、`pilot-only` 或 `blocked`。缺失关键证据时，精美报告也不能把结论升级。

八个渠道只是广泛发现层。不同领域还必须补齐相应的一手来源。技术选型至少要检查官方文档、论文或模型卡、仓库和 issue、独立 benchmark、当前价格、安全与许可证；旅行调研必须核对运营方、预订、交通、天气及目的地官方信息；金融、医疗、法律等高风险领域采用更严格的边界。

## 工作流

```mermaid
flowchart LR
    A["模糊或明确的需求"] --> B["全渠道广探测"]
    B --> C["按价值自适应深挖"]
    C --> D["证据地图与候选注册表"]
    D --> E["讲解、提问、确认决策契约"]
    E --> F["必要时定向二次调研"]
    F --> G["证据门禁与生产门禁"]
    G --> H["决策、报告与领域化 runbook"]
```

v3 不承诺“30–60 分钟跑完”。资料简单且可访问时可能很快；遇到视频转写、访问失败、来源冲突、用户澄清或代表性 POC 时也可能需要数小时。系统记录真实进度、成本和能力缺口，而不是给一个营销式时长。

## 三种交付状态

| 状态 | 含义 |
|---|---|
| `production-ready` | 决策契约已经由用户明确确认；每个关键结论都有充分且可精确追溯的证据；全部 finding 已消费或排除；预算已结算；生产相关验证已经闭环。 |
| `pilot-only` | 已有可信候选，但质量、集成、性能、运维或其他重要条件仍需一个有边界的 POC。它不是生产推荐。 |
| `blocked` | 关键需求、权限、一手来源、许可证、安全事实、预算或证据缺失/冲突。交付物只给出最小解阻动作，不伪造默认方案。 |

门禁可以把 Agent 请求的状态降级；报告渲染器不能把它升级。

## 可恢复、可审计的状态

每次 v3 调研以 `research.db` 作为唯一真实状态源。它是 SQLite/WAL 数据库；JSON/JSONL 是供审阅和交付的投影，不能反过来手工修改成工作流状态。

`docs/research/<topic>/` 下的典型产物：

| 文件 | 用途 |
|---|---|
| `research.db` | 保存事件、已批准 plan revision、finding、候选、claim、独立证据簇、任务尝试、预算、决策和交付 revision，可从中断处恢复。 |
| `manifest.v3.json` | 本次 run、领域 profile、门禁结果、数量、预算和生成时间。 |
| `events.jsonl` | 只追加的对话与系统事件；用户发言在 `verbatim` 中逐字保留，与 Agent 的结构化解释分开。 |
| `plan.json` / `plan-revisions.jsonl` | 经过校验的八入口范围、估时、硬预算、批准事件绑定及只追加计划历史。 |
| `findings.jsonl` | 带稳定指纹的逐帖精华笔记，以及已消费/已排除处置。 |
| `finding-revisions.jsonl` | finding 笔记/正文的只追加 revision 历史；内容变化后会重新回到 pending。 |
| `candidates.jsonl` / `artifacts.jsonl` | 可选候选 registry 与内容寻址证据产物；生产 POC 必须引用真实且 hash 匹配的 `poc-result` 文件。 |
| `claims.jsonl` | 原子化决策结论及其证据充分性。 |
| `evidence-clusters.jsonl` | 来源独立性分组，避免同源转载或共同上游被算成多方印证。 |
| `attempts.jsonl` | ASR 等计费任务的预留、服务商任务 ID、最终费用和未知账单。 |
| `decision.json` | 约束、状态、推荐、备选、证据缺口和 POC 要求的唯一机器可读决策源。 |
| `decision-revisions.jsonl` | 与每次所用 plan revision 绑定的只追加决策历史。 |
| `report.html` | 从当前 decision 确定性生成并做安全转义的人类报告。 |
| `runbook.json` | 从同一 decision 生成的 `implementation`、`itinerary`、`forecast` 或 `research-only` 类型化执行手册。 |
| `delivery-manifest.json` | 交付 revision 和文件 hash，用于发现过期或互相矛盾的产物。 |

运行被打断后，从数据库、笔记 cursor 和 attempt journal 恢复。重试保留历史并生成新 attempt，不再删除整个渠道的证据。

结构化 decision contract 也做完整性绑定：主 Agent 先记录并逐字展示 contract JSON，用户再用独立回复确认；两个 event ID 都进入不可变 decision revision。

## ASR 硬预算与幂等

付费语音转写默认关闭：新 run 未明确填写数值预算时，ASR 时长和金额上限都是零。

每个计费请求发出前，v3 会原子预留预计时长和金额；服务商返回后再结算、释放，或标记为 `unknown`。并发调用无法预留超过上限的预算，未知账单会继续占用额度。媒体指纹加模型/参数指纹构成幂等键，即使平台刷新了带签名的 CDN URL，也不会把同一媒体重复付费转写。

这是代码层的硬账本，不是提示词里让 Agent “记得别超预算”。

## 快速开始

### 环境要求

- [Claude Code](https://claude.com/claude-code)。v3 当前只支持 Claude Code。
- Python 3.11 或以上。
- Git。
- 只为你希望启用的渠道配置可选工具和账号。

### 安装

```bash
git clone https://github.com/Somezak1/research-anything.git ~/research-anything
cd ~/research-anything

# 检查 Claude Code、Python、安装同步状态和可选连接器。
python3 scripts/install_skill.py doctor

# 把唯一正式运行副本安装到 ~/.claude/skills/research-anything。
python3 scripts/install_skill.py install

# 校验安装副本与当前仓库完全一致。
python3 scripts/install_skill.py check
```

`install` 不会覆盖内容不同的旧副本。确认差异后，可使用带时间戳备份的强制更新：

```bash
cd ~/research-anything
git pull
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
python3 scripts/install_skill.py check
```

用 `CLAUDE_SKILLS_DIR` 或 `--target` 修改 skill 安装位置。可选连接器不在 `~/tools` 时，设置：

```bash
export RESEARCH_TOOLS_DIR="$HOME/my-research-tools"
python3 scripts/install_skill.py doctor
```

doctor 只报告能力缺口，不会静默安装第三方爬虫、登录账号或替你授权。

## 使用

在希望接收调研产物的项目里开启一个全新的 Claude Code 会话，直接描述真实问题。需求可能被理解成普通网页搜索时，建议显式点名 skill：

```text
请使用 research-anything 调研 AI 漫剧如何用于真实产品。我还不知道应该选哪种
流程，也不清楚哪些约束最关键。请先研究市面方案和生产实践，讲清取舍后再帮我选型。
```

也可以一开始就给出已有约束：

```text
请使用 research-anything 规划一次三天家庭自驾游。同行有两位成年人、一名幼儿和
一名不便长时间步行的老人；请在生成行程前核对当前交通、预约、天气和无障碍信息。
```

Claude 会在真正需要授权或做决策时暂停。尤其是，它必须保存你的完整原话、展示结构化 decision contract，并取得明确确认；没有这一步，结果不能成为 `production-ready`。

检查正在运行的 v3 调研：

```bash
python3 ~/.claude/skills/research-anything/scripts/researchctl.py doctor \
  --db docs/research/<topic>/research.db
python3 ~/.claude/skills/research-anything/scripts/researchctl.py status \
  --db docs/research/<topic>/research.db
```

## 连接器能力与限制

渠道是否可用取决于工具、地区、认证、平台行为和你授予的权限。缺失连接器会被记录为 capability gap，不能伪装成“成功搜索但结果为零”。

| 发现来源 | 常见能力 | 重要限制 |
|---|---|---|
| 通用网页与官方网站 | 当前一手文档、价格、政策、班次和事实核查 | 访问和动态渲染取决于环境中的 Claude Code 网页/浏览器能力。 |
| GitHub | 仓库、release、代码、许可证和 issue 证据 | 需要可用的 GitHub 访问；热度不能替代生产适配证据。 |
| YouTube 与 B站 | 元数据与字幕；必要时使用已授权 ASR | 可能受 `yt-dlp`、cookie、地区、字幕和媒体可访问性限制。 |
| 抖音、小红书、知乎与 B站社区内容 | 帖子、评论、配图和视频引用 | 平台登录和反自动化机制可能阻断采集；系统不会默认你同意账号操作。 |
| Twitter/X | 帖子、thread 和回复 | 认证与平台控制经常变化；失败必须如实披露。 |

`MediaCrawler` 只作为个人、非商业学习/研究的可选连接器，其上游采用 [NON-COMMERCIAL LEARNING LICENSE](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE)。它不是商业调研的默认连接器。企业或商业用途必须另外采用已获授权的路径，例如官方 API、合规的浏览器辅助采集或用户自行提供的导出数据。

本仓库不包含账号、cookie、API key，也不授予任何平台的数据采集许可。请自行检查服务条款、内容权利、隐私要求、账号风险、API 费用和地区规定。Claude 使用量、API、ASR、代理、商业数据访问及其他连接器都可能产生费用；本项目不声称“除了 ASR 以外都免费”。

## 校验交付物

skill 会在工作流中执行这些门禁，也可以手动用于审计或 CI：

```bash
python3 ~/.claude/skills/research-anything/scripts/researchctl.py gate \
  --db docs/research/<topic>/research.db
python3 ~/.claude/skills/research-anything/scripts/researchctl.py export \
  --db docs/research/<topic>/research.db \
  --out-dir docs/research/<topic>
python3 ~/.claude/skills/research-anything/scripts/render_delivery.py \
  --decision docs/research/<topic>/decision.json \
  --findings docs/research/<topic>/findings.jsonl \
  --events docs/research/<topic>/events.jsonl \
  --report docs/research/<topic>/report.html \
  --runbook docs/research/<topic>/runbook.json \
  --delivery-manifest docs/research/<topic>/delivery-manifest.json
python3 ~/.claude/skills/research-anything/scripts/validate_delivery.py \
  --out-dir docs/research/<topic>
```

`report.html` 和 `runbook.json` 都从 `decision.json` 生成；重试后不能靠手工分别修补来“对齐”。

## 审计旧版 v2 调研

v2 产物仍可阅读，但不能视为 v3 证据。只读审计器会识别报告过期、统计冲突、ASR 未结算、用户原话记录薄弱、artifact 未被引用等问题，且不会重写旧目录：

```bash
python3 scripts/audit_v2.py \
  --out-dir /path/to/legacy/docs/research/<topic> \
  --out /tmp/v2-audit.json
```

在 CI 或严格复核中增加 `--strict`，遇到 blocker/high 问题时返回非零状态码。

## 仓库结构

```text
research-anything/
├── SKILL.md                  # 精简的 Claude Code 编排契约
├── references/               # 领域、渠道、证据、总结与交付协议
├── scripts/
│   ├── install_skill.py      # 公开 skill 的 install/check/doctor
│   ├── researchctl.py        # v3 SQLite 状态源与生产门禁
│   ├── render_delivery.py    # 安全、确定性的报告/runbook 渲染器
│   ├── validate_delivery.py  # 跨产物一致性校验
│   └── audit_v2.py           # 旧版只读审计器
└── pyproject.toml            # Python 3.11+ 与测试配置
```

## 开发验证

运行时只依赖 Python 标准库，测试使用 `pytest`：

```bash
python3 -m pytest
python3 -m py_compile scripts/*.py
```

## 能力边界

research-anything 能提高调研的可追溯性和决策纪律，但不能凭空打开无法访问的资料、不能把同源转载变成独立证据，也不能替代真实生产环境的代表性测试。`blocked` 是一种有效且经常很有价值的结果。
