---
name: research-anything
description: 当用户抛出探索性想法（没做过、不知道成熟路径，如"做 AI 漫剧""搭 XX 工作流""视频转文字选型"），或明确要求"调研某方向/看看有没有成熟做法/收集资料/比较方案"时使用。适用于需要跨抖音/小红书/知乎/B站/YouTube/GitHub/Twitter/通用 web 收集市面方案的场景。
---

# research-anything — 全渠道调研 → 可执行方案

给一个探索性 idea，系统化跨渠道搜集信息、核实比较，产出**1-3 个可落地方案 + 报告**供用户审核。核心价值：**拿到各渠道的先进做法，避免闭门造车落后几代**；产出物是"默认路径+切换条件"的可执行方法论，不是一堆并列选项让人自己选。

## 路径口径（先读这节；后文所有 <占位符> 按此展开）

- `<SKILL_DIR>`：本 skill 安装目录的绝对路径。调用 skill 时 harness 已告知（"Base directory for this skill: …"），照抄即可。
- `<PROJECT_DIR>`：当前会话项目根的绝对路径（`pwd` 的结果）。
- `<OUT_DIR>`：本次调研全部产物的根目录 = `<PROJECT_DIR>/docs/research/<slug>`。
- 爬虫工具常驻 `~/tools/`（与本 skill 安装位置无关）。本 skill 可在任意项目使用，产物落当前项目的 `<OUT_DIR>`。
- **传给任何子 agent / 脚本的路径一律用展开后的绝对路径**，不许传相对路径或未展开的占位符——子 agent 对"当前目录在哪"不做任何假设。

## 执行拓扑（关键）

```
用户（说出 idea）
 └─ 主 agent：读全部渠道文档 → 计划待批 → 派收集扇出 → 亲自执行总结（读 summarize.md 规程）→ 与用户直接沟通（qa.md 只许追加存档）→ 呈现方案
      ├─ 收集扇出（workflow.js）：每渠道 1 agent 实搜落盘 → 独立证据复核补齐视频/评论/图片/许可证 → 返回小指针
      └─ 总结（主 agent 本人执行，用投影脚本通读全部笔记；不派总结 agent，见 Stage 3）：
           ├─ 派 sub：生词建卡（不设数量上限）
           ├─ 派 sub：定点核查（事实题问官方、品质题问口碑；不设数量上限）
           ├─ 【必经，无论约束多完整】出方案前先在 qa.md 写「名词/地点速览」+「交叉印证」讲给用户、答疑到无疑惑，再出选择题；问答原文只许追加进 qa.md
           └─ 落盘 report.html + runbook.json，按文件呈现
```

**判断力集中在总结者 = 主 agent 本人**（Stage 3 它必须亲自通读全部笔记投影后再综合）；收集 agent 只做忠实笔记员。**收集 agent 不加载本 SKILL.md**——其规程靠"必读原文文件"直达（渠道文档 + log-format.md），不靠主 agent 转述；总结规程（summarize.md + report-format.md）由主 agent 在 Stage 3 开工前自读原文。

## 5 阶段流程（严格按序）

### Stage 0 — 意图澄清 + 成熟度判定
判断 idea 是 **refined（已知目标/场景/预算/成功标准）** 还是 **rough（只有一句想法）**，写入 `<OUT_DIR>/manifest.json`（含 idea 原话、maturity、已知 constraints，并预置 `"asr_authorization":{"authorized":false,"max_hours":0,"max_cost_cny":0}` 默认值——让"未授权"从创建起就是显式状态，Stage 1 用户明确同意后才覆盖）。
- refined：记录约束，直接进 Stage 0.5。**refined 不免除 Stage 3 的用户沟通**（调研中会浮现用户事先想不到的新约束，如金钱/时间预算），只会让那轮问题更少更聚焦。
- rough：**只问 1 个问题**确认调研主题没理解偏，**其余问题一律留到 Stage 3**（看完市面上有什么再问，问题才有质量）。不要在调研前逼问用户还答不好的目标/预算。

### Stage 0.5 — 【强制】读完所有渠道文档（不可跳过）
做搜集计划**之前**，你（主 agent）MUST 完整读完 `<SKILL_DIR>/references/channels/` 下的**每一个** `*.md`（douyin / xiaohongshu / zhihu / bilibili / youtube / github / twitter / web）+ `<SKILL_DIR>/references/log-format.md`。这些文档写明每个渠道用什么工具、能/不能返回什么、耗时、失败与处理、防封号、运行侧安全约束、真实示例。

### Stage 1 — 搜集计划待批
按 `<SKILL_DIR>/references/search-plan.md` 把 idea 拆成结构化计划（**含预计耗时**）。铁律：
- **8 渠道全覆盖，不许跳过任何渠道**（任何题目在任何平台都可能有人发布相关内容）。
- **无"权重"列**；深度统一（默认 15/渠道），用户可按渠道调深度但不删渠道。
- 每渠道给多角度关键词（正面/痛点/对标/英文同义）+ 要提取的信号。
- 计划必须披露可能使用按量计费 ASR，并给出明确的预计转写时长上限与人民币费用上限（当前参考价约 0.8 元/小时，以服务商实际计费为准）。**付费 ASR 必须由用户单独明确同意；批准搜集计划不自动授权费用。**
呈现表格 → 用户增删 → **等用户明确批准**再继续。
**批准后立即把最终计划回填 `manifest.json` 的 `plan` 字段**（channels 转英文标准名 + dimensions），并把费用决定写入顶层 `asr_authorization:{authorized,max_hours,max_cost_cny}`——下游 agent 只看 manifest，不回看对话。`max_hours` / `max_cost_cny` 是用户批准的硬上限；只有用户明确同意展示过的数值上限才能写 `authorized:true`。无视频或全程只用原生/免费字幕时写 `authorized:false,max_hours:0,max_cost_cny:0`；一旦某条需要付费 ASR，未同意时只能申报失败，不能调用服务或伪装成功。
（无交互运行时例外：不等待批准，按默认计划执行，并在 manifest 记 `"approved": false`、`"asr_authorization":{"authorized":false,"max_hours":0,"max_cost_cny":0}`。）

### Stage 2 — 收集扇出 + 证据补全
用批准的计划调收集脚本（批准只授权 Workflow 执行，不授权付费 ASR；ASR 权限只认 manifest 的 `asr_authorization`）：

    Workflow({ scriptPath: "<SKILL_DIR>/scripts/workflow.js",
               args: { idea: "<idea 原话>", slug: "<slug>",
                       skillDir: "<SKILL_DIR>", outDir: "<OUT_DIR>",
                       channels: [{name,keywords,signals,depth}, …], dimensions: […] } })

- `channels[].name` 必须用英文标准名（douyin/xiaohongshu/zhihu/bilibili/youtube/github/twitter/web）。脚本会校验并归一常见中文别名，未知名直接报错。
- 脚本先让每渠道 1 agent 实搜落盘，再让独立复核 agent 逐条补齐证据。复核只补字幕/口播、评论、图片文字、许可证和处理状态，不改候选集合、不做跨渠道综合。
- schema_version=2 的 finding 必须按 log-format.md 写 `capture`：正文来自哪里、视频/评论/图片/许可证是否处理、失败原因和产物路径。标题或简介非空不等于视频处理完成。
收集完成后，主 agent 依次跑三条命令：
1. 格式 + 覆盖校验（渠道清单填计划里实际批准的渠道；manifest 用于核对计划关键词与 artifact 产物路径）：
   `python3 <SKILL_DIR>/scripts/validate_log.py --raw-dir <OUT_DIR>/raw --channels douyin,xiaohongshu,zhihu,bilibili,youtube,github,twitter,web --manifest <OUT_DIR>/manifest.json`
2. 证据覆盖统计（落盘并传给 Stage 3）：
   `python3 <SKILL_DIR>/scripts/coverage_report.py --raw-dir <OUT_DIR>/raw --out <OUT_DIR>/coverage.json`
3. 规模统计（Stage 3 总结的输入之一，用于熔断判断）：
   `python3 <SKILL_DIR>/scripts/project_notes.py --raw-dir <OUT_DIR>/raw --mode stats`
4. ASR 费用对账：合计 `<OUT_DIR>/artifacts/asr_ledger.jsonl` 各行 billed_seconds，换算小时后与 manifest 的 `asr_authorization.max_hours` 对照；超限、或存在 `video.status:"asr"` 的 finding 却没有台账时，报告用户。
不合格 → 报给用户并按 SOP 重派，**不静默容忍**。
**单渠道重派 SOP**：① 删掉半成品：`rm <OUT_DIR>/raw/findings.<渠道>.jsonl`，并同步删除该渠道旧 artifact（`setopt null_glob; rm -f <OUT_DIR>/artifacts/<渠道id前缀>-*`，前缀见 log-format.md 前缀表；不删则新 finding 复用同 id 时 validate 可能对着旧残片误通过）；② 重调上面的 Workflow，args 完全同前但 channels 只留该渠道；③ 重跑上述校验命令。

### Stage 3 — 总结（主 agent 亲自执行，不派总结 agent）
校验通过后，主 agent 用 Read 完整阅读两份规程原文并严格执行，不接受记忆版（log-format.md 已在 Stage 0.5 读过）：
- `<SKILL_DIR>/references/summarize.md`（总结执行规程）
- `<SKILL_DIR>/references/report-format.md`（交付物规范）

输入即 Stage 2 产物：manifest.json + raw/findings.*.jsonl + coverage.json + 规模统计。生词建卡与定点核查照旧派 sub agent 并行，但**通读、判断、综合、与用户沟通、写报告全部由主 agent 本人完成**——与用户的沟通不经任何中转（2026-07-15 用户反馈拍板：中转通信绕、且子 agent 进程退出会让对话续不上）。
警告不变：禁止用 Read 直接读 findings.*.jsonl（单行内嵌原文全文，超长行会被截断），一律用 `python3 <SKILL_DIR>/scripts/project_notes.py` 投影读取（用法见 summarize.md 的"读取协议"）；熔断、两遍读法、绝不截断等规矩以 summarize.md 为准。

**问答存档铁律**：用户问答以 `<OUT_DIR>/qa.md` 为唯一权威记录（只许追加）——主 agent 先把「名词/地点速览」「交叉印证」两节与问题**原文**写入 qa.md，再呈现给用户；用户每次回答/追问，主 agent **立即一字不改**追加回 qa.md（防上下文被压缩后原话丢失）。禁止只在对话里问而不落盘；禁止改写/臆想/代答。

### 交付
主 agent 落盘 `report.html` / `runbook.json` 后，呈现方式：定位 report.html 的 `<section id="summary">`、`<section id="plans">`、`<section id="reco">` 三节并读取原文，**引用原文**呈现要点（不整读全文、不凭记忆复述），并给出两份文件路径供审核。呈现要点时，对首次出现的关键地名/术语附一行短释（取自 glossary 生词卡）——不许拿用户没见过的名词裸讲方案。注意：report.html 常为长行/压缩 HTML，Read 行区间对超长行会**静默截断**——一律用程序化切片提取（如 `python3` 正则取 `<section id="...">…</section>` 再剥标签），不要用 Read 行区间硬读（2026-07-13 实录）。

## 渠道路由表
| 渠道（标准名） | 文档 | 渠道（标准名） | 文档 |
|---|---|---|---|
| 抖音 douyin | channels/douyin.md | YouTube youtube | channels/youtube.md |
| 小红书 xiaohongshu | channels/xiaohongshu.md | GitHub github | channels/github.md |
| 知乎 zhihu | channels/zhihu.md | Twitter/X twitter | channels/twitter.md |
| B站 bilibili | channels/bilibili.md | 通用 web web | channels/web.md |

## 前置依赖（工具全部住在 ~/tools/）
- **Stage 2 派发前连通性预检（主 agent）**：`curl -sS -m 8 -o /dev/null -w '%{http_code}\n' https://x.com https://www.youtube.com`（常用代理时另测代理链路）。不可达的渠道不要静默空转：把结论告知用户，由用户选择照跑（渠道按规程申报失败）/ 修好网络再跑。2026-07-13 实录：代理上游节点故障导致 Twitter 整渠道 0 条、YouTube 全程降级。
- 小红书 MCP 服务：`~/tools/xiaohongshu-mcp/server.sh start`（未起则先起；**未登录时调研直接走 MediaCrawler 工具 B，禁止唤登录二维码**）。
- MediaCrawler 四平台登录态已持久化；Twitter 需 twscrape + 账号（见 twitter.md）。
- YouTube/B站字幕需 yt-dlp（已装 `/opt/homebrew/bin/yt-dlp`）。B站 ai-zh 字幕用已导出的 cookie 文件取（`~/tools/bili_cookies.txt`，2026-07-13 导出，零弹窗；**禁止 `--cookies-from-browser`**——每次触发钥匙串弹窗，仅 cookie 过期重导时用一次）。YouTube 字幕直取无需 cookie，批量拉注意限流。
- 视频口播提取（`<SKILL_DIR>/scripts/transcribe.py`，fun-asr）需环境变量 `DASHSCOPE_API_KEY`（阿里云百炼 API Key，放 `~/.zshrc`；**凭据绝不写进 skill/报告**）。计费按语音内容时长（约 0.8 元/时，开通后 90 天 10 小时免费）；2026-07-13 实测约 60 倍实时、抖音/小红书直链可免下载直传。即使可能命中免费额度，也必须先按 Stage 1 获得明确的时长/费用上限授权并写入 manifest，未授权不得调用。每次调用自动在输出目录追加费用台账 `asr_ledger.jsonl`（每行含 billed_seconds），Stage 2 校验时主 agent 据此对账授权上限。最终入选的抖音/小红书视频全量转写；B站优先 ai-zh 字幕、无字幕才转写；YouTube 优先原字幕；Twitter 所有带视频的入选推文均须字幕/ASR。用法见各视频渠道文档。
- 小红书配图文字用 `<SKILL_DIR>/scripts/ocr_images.py` 识别（macOS 系统文字识别，无额外 Python 依赖）。处理最终入选图文笔记的全部配图，视频封面不强制；结果写入 finding 的 content/note，并在 capture 记录处理数量和产物路径。

## 关键设计约束（不可违背）
- **绝不截断**：任何环节不许静默丢材料；笔记全集读不下→报错停止问用户，不取子集。
- **可追溯**：报告/runbook 每个结论必须带 finding id 或 verdict id（vd-xxx）引用。
- **证据完整性**：入选视频必须有字幕/ASR 或明确失败原因；入选社交内容必须抓前 10 条有用评论或明确不可用；小红书图文笔记配图必须全部识别或逐项报错；GitHub 许可证只认根目录实际 LICENSE 文件。validate_log.py 不通过就禁止进入 Stage 3。
- **费用边界**：付费 ASR 只认 manifest 中用户明确批准的 `asr_authorization` 数值上限；计划批准、已有 API Key 或可能有免费额度都不构成费用授权。未授权或预计超限时停止调用并申报失败，超限必须重新询问用户。
- **核实分类**：事实题（价格/授权/接口）问官方即权威；品质题（准不准/好不好）**官方自评不算数**，必须搜口碑/独立评测，不够则进 runbook 的 to_test 待实测。
- **代际感**：生词建卡含发布时间，由卡片拼出方法/模型时间线，防"推荐落后几代还不自知"。
- **原话神圣**：本 skill 中标注"原话/一字不改"的引用块（log-format.md 的笔记目的句与京都三条示范、qa.md 的问答记录）在任何未来迭代中**禁止润色/概括/替换**——此前迭代中已发生过三次被 AI 顺手改写，勿重蹈。
