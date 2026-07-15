# 统一日志格式规范（收集层落盘）

所有渠道的收集 agent，不管用什么工具搜什么平台，落盘的日志**必须是同一种格式**。这样后续的总结/核查会话（含无交互 Claude Code）读任何渠道的日志都用同一套解析，新增渠道零改动。

## 路径根

本文所有相对路径均以 `<OUT_DIR>` 为根。`<OUT_DIR>` = 调研发起会话的项目根下的 `docs/research/<slug>`，**绝对路径由主 agent 计算，经 workflow args（outDir）/派发词传给每个下游 agent**——任何 agent 都不许自行猜测落盘位置。

## 为什么是 JSONL

每行一个独立 JSON 对象（JSON Lines）。原因：本项目生态已全是它（MediaCrawler 落盘、workflow 日志）；**可追加写、坏一行不毁全文件、可按行程序化处理**。Markdown 机器难解析、CSV 装不下嵌套、单个大 JSON 不能流式追加，均不选。

> ⚠️ **给读取方的警告**：finding 是单行 JSON、行内嵌原文全文（可达数万字），agent 的 Read 工具对超长行会截断——**总结层禁止用 Read 直接读本格式文件**，必须用 `<SKILL_DIR>/scripts/project_notes.py` 做字段投影（用法见 summarize.md 的"读取协议"）。

## 目录布局

每次调研一个目录，按阶段分层：

```
<OUT_DIR>/                     # = <项目根>/docs/research/<slug>
├── manifest.json              # 本次调研元信息（见下）
├── raw/                       # ← 收集层落盘（本规范管这里）
│   ├── findings.github.jsonl
│   ├── findings.xiaohongshu.jsonl
│   └── findings.<渠道>.jsonl   # 每渠道一个文件；新渠道=新文件，格式零改动
├── artifacts/                 # ← 字幕 / ASR / OCR / 评论 / 许可证的真实文本产物（供 capture.artifact 回指）
│   ├── dy-001.asr.txt
│   ├── xhs-007.ocr.txt
│   ├── xhs-007.comments.txt
│   └── gh-001.license.txt
├── coverage.json              # ← 证据覆盖统计（Stage 2 校验后生成）
├── verify/                    # ← 总结/核查层产物（记录 type 另用 verdict | term，见 summarize.md）
│   ├── verdicts.jsonl
│   └── glossary.jsonl
├── qa.md                      # ← 总结阶段与用户的问答原文（唯一权威记录，只许追加）
├── report.html                # 交付：给人看
├── runbook.json               # 交付：给 AI 执行
└── assets/                    # 本地化图片
```

**每渠道一个文件**是硬性要求：多个收集 agent 并行运行，各写各的文件才不会并发写坏同一个文件。

## manifest.json

```json
{"slug":"video-transcription-cn",
 "idea":"（用户原话想法）",
 "maturity":"rough | refined",
 "constraints":{"预算":"…","场景":"…","已知目标":"…"},
 "plan":{"channels":[{"name":"douyin","keywords":["…"],"signals":["…"],"depth":15}],"dimensions":["…"]},
 "approved":true,
 "asr_authorization":{"authorized":true,"max_hours":10,"max_cost_cny":8},
 "created":"2026-07-11T10:00+08:00"}
```

- `plan` 在 Stage 1 用户批准后由主 agent **回填**（渠道名转英文标准名）——Stage 3 总结以 manifest 为准、不依赖回看对话（对话可能被压缩），不回填则用户批准的对比维度丢失。
- `asr_authorization` 记录 Stage 1 对可能产生费用的 ASR 的**单独授权**。只有用户明确同意数值化的 `max_hours` 与 `max_cost_cny` 上限时，`authorized` 才能为 `true`；批准搜集计划本身不等于授权付费。未明确同意（含无交互运行）必须写 `authorized:false,max_hours:0,max_cost_cny:0`，此时可用免费/原生字幕，但需要 ASR 的视频只能在 `capture.video` 申报 `failed` 并写明未获授权。schema v2 校验器会读取该对象，未授权时任何 `video.status:"asr"` 都不能通过。
- `maturity` 是给总结层的钩子：**只影响 Stage 3 提问的数量与聚焦**（refined 少而聚焦新浮现约束、rough 多而定向），**不免除提问**。

## 记录信封：每行必有 type

`type` 取值：`meta` | `finding`（核查层另用 `verdict` | `term`）。**未来新增阶段加新 type，不破坏旧读取方——这就是"槽位"机制。**

### 每个 findings 文件第一行是 meta

让总结/核查方一眼知道这个渠道的覆盖面与缺口，**不静默遗漏**：

```json
{"type":"meta","schema_version":2,"channel":"xiaohongshu","slug":"video-transcription-cn",
 "queries":["视频转文字 工具","提取文案"],
 "started":"2026-07-11T10:20+08:00","finished":"2026-07-11T10:31+08:00",
 "count":16,
 "failures":["关键词'AI字幕'第2页触发验证码，跳过"],
 "skipped":[]}
```

收集开始时先写 meta 占位行（count:0），结束时用 finalize_log.py 回填 finished / count / failures（见"落盘纪律"）。
**count=0 时 failures 必须非空**——零结果必须申报原因（如"账号未配置""全部关键词无命中"），不许静默。校验器会拦。
文件名与 `meta.channel` 必须一致，例如 `findings.twitter.jsonl` 的 channel 只能是 `twitter`；不能用一个空壳渠道文件冒充另一个渠道已覆盖。`count` 必须是非负整数，并与实际 finding 条数一致。
`queries` 必须与 manifest 中本渠道的批准关键词完全一致。某个词没有对应 finding 时，`failures` / `skipped` 必须同时写出该词和具体原因；只写“跳过”“已跳过”“因故跳过”“搜索失败”等空话仍会校验失败。

新建日志必须声明 `"schema_version":2`。历史日志未声明该字段时，单独运行不带 `--manifest` 的兼容检查仍按旧规则校验，不要求补写 `capture`；正式 Stage 2 始终传入 `--manifest`，此时缺少版本号会直接失败，不能借旧格式绕过新证据规则。声明其他版本同样会报错。

GitHub 的 schema v2 meta 还必须记录三路候选发现结果，例如：

```json
{"discovery":{"keyword":{"status":"completed","count":2,"proof":["speech-to-text stars:>1000","whisper alternative"]},
              "category":{"status":"completed","count":1,"proof":["https://github.com/topics/speech-recognition"]},
              "related":{"status":"completed","count":1,"proof":[{"from":"https://github.com/openai/whisper","found":"https://github.com/SYSTRAN/faster-whisper","via":"readme"}]}}}
```

每一路固定为 `{status,count,proof,reason?}`：`completed` 必须 `count > 0`、proof 不重复且 `count == proof.length`；`failed` 必须 `count:0,proof:[]` 并写非空 `reason`。三路 proof 的形状不同：`keyword.proof` 必须与本文件 `meta.queries` 完全一致；`category.proof` 写 `github.com/topics/...`、`github.com/collections/...` 或仓库名含 awesome 的列表仓库；`related.proof` 每项写 `{from,found,via}`，其中 `from` 必须是本日志已入选仓库，`found` 是从它继续发现的另一仓库，`via` 只取 `readme` / `dependency` / `related`。不能写占位词、候选总数或无关说明凑数。某一路失败时仍按上面的空 proof + 具体原因记录。

category/related 路线发现的 finding，其 `query` 字段仍从批准关键词中选语义/限定符最接近、且该仓库真实满足限定符的一个（保持"query 可复现"的校验口径），同时**必须**在 `raw.discovered_via` 记录真实发现路线（如 `{"route":"related","from":"<已入选仓库URL>","via":"readme"}` 或 `{"route":"category","proof":"<topics/awesome URL>"}`），并与 meta.discovery 对应路线的 proof 一致。这是正常路径而非降级，无需记入 failures（2026-07-13 定）。

### finding 记录（示例为真实数据）

```json
{"type":"finding",
 "id":"xhs-007",
 "ts":"2026-07-11T10:30:22+08:00",
 "channel":"xiaohongshu",
 "tool":"mediacrawler/xhs-search",
 "query":"视频转文字 工具",
 "source_url":"https://www.xiaohongshu.com/explore/667a3b2f...",
 "title":"视频提取文案在线工具推荐：通义听悟",
 "author":"某昵称",
 "published_at":"2024-06-25",
 "headline":"通义听悟免费转6小时视频、批量50个、分说话人",
 "note":"博主推荐通义听悟(阿里)做长视频转文字：完全免费、支持6小时以内、一次批量50个、按声纹自动区分发言人、可导出文本；此前用剪映需开VIP故转用。全程网页手动上传，未提及程序接口。",
 "metrics":{"likes":4239,"collects":4647,"comments":204,"shares":930},
 "media":[{"kind":"image","url":"http://sns-webpic..."}],
 "unknown_terms":["通义听悟"],
 "capture":{"content_sources":["post","ocr","comments"],
   "video":{"present":false,"status":"not_present"},
   "comments":{"status":"captured","count":10,"artifact":"artifacts/xhs-007.comments.txt"},
   "images":{"present":true,"status":"ocr","total":6,"processed":6,"artifact":"artifacts/xhs-007.ocr.txt"},
   "license":{"status":"not_applicable"}},
 "content":"（帖子原文全文，不截断）\n\n[OCR]\n（xhs-007.ocr.txt 的完整文本）\n\n[评论]\n（xhs-007.comments.txt 的完整文本）",
 "raw":{"note_id":"667a3b2f...","tag_list":"...","xsec_token":"..."}}
```

## 字段规定

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✓ | `<渠道前缀>-<3位序号>`，全文件内唯一，供结论回指。**前缀表（三处代码/文档统一以此为准）**：douyin=`dy`、xiaohongshu=`xhs`、zhihu=`zh`、bilibili=`bili`、youtube=`yt`、github=`gh`、twitter=`tw`、web=`web`。如 `xhs-007`、`gh-005` |
| `ts` | ✓ | 抓取时间（ISO8601 带时区） |
| `channel` | ✓ | 渠道英文标准名，与文件名一致 |
| `tool` | ✓ | 所用工具，如 `mediacrawler/xhs-search`、`github-mcp/search_repositories`、`yt-dlp/subs` |
| `query` | ✓ | 命中本条的关键词——可复现 |
| `source_url` | ✓ | 原始帖子/仓库/页面链接 |
| `title` | ✓ | 原标题 |
| `headline` | ✓ | **一句话（≤40字）**，供总结者第一遍速览全集用（两遍读法） |
| `note` | ✓ | **本条精华笔记，见下方质量标准** |
| `metrics` | ✓ | 对象。能数值化就数值化（`"likes":4239`）；平台给模糊字符串就原样保留（`"likes":"10万+"`）。GitHub 的 star/fork 也进这里 |
| `content` | ✓ | 实际取到的原文全文 / 视频字幕全文 / README 节选。**落盘不怕大**，笔记漏了回这查——这是"从笔记反查原文"的正规通道 |
| `raw` | ✓（可空 `{}`） | 渠道原生字段**原样保留**——GitHub 的 License、小红书的 xsec_token、抖音的 aweme_id 等都塞这。这是跨渠道统一的"槽位"：核心字段人人一致，渠道特有的进 raw。反查/深挖时靠它 |
| `capture` | schema v2 必填 | 内容采集和补充处理的可追查记录；固定形状和硬检查见下节。旧日志未声明 `schema_version` 时可省略 |
| `author` | 可选 | 有则填 |
| `published_at` | 可选 | 尽力而为——是代际/时效的线索之一 |
| `media` | 可选 | `[{"kind":"image|video","url":"…"}]`，代表性封面/关键帧 |
| `unknown_terms` | 可选 | 收集者拿不准的新名词（如 `FireRedASR2`/`AED`）。**不懂就地承认，留给总结层建卡**，不要瞎猜含义 |

**不设 `claims` 字段**（v1 教训：收集者没有全局视野，由它预判"什么值得核实"就是新的有损压缩。承重断言由读过全集的总结者自己挑）。

## schema v2：采集记录 `capture`

每条 finding 都必须有以下四块，即使某类内容不存在也不能省略。`content_sources` 写本条 `content` 和 `note` 实际使用的来源；建议使用稳定短名：`post`、`subtitle`、`asr`、`ocr`、`comments`、`readme`、`license`、`page`。

```json
{"content_sources":["post","asr","comments"],
 "video":{"present":true,"status":"asr","artifact":"artifacts/dy-001.asr.txt"},
 "comments":{"status":"captured","count":10,"artifact":"artifacts/dy-001.comments.txt"},
 "images":{"present":false,"status":"not_present","total":0,"processed":0},
 "license":{"status":"not_applicable"}}
```

| 子项 | 固定字段与状态 | 规则 |
|---|---|---|
| `video` | `present` 布尔值；`status`: `not_present` / `subtitle` / `asr` / `failed`；`artifact` 在 subtitle/asr 时必填，`error` 在 failed 时必填 | 无视频时用 `present:false,status:not_present`。失败时必须写非空 `error`；成功时必须写产物 `artifact`，且 `content_sources` 包含 `subtitle`/`asr`。Bilibili/YouTube 的每条入选 finding，以及从 `raw`、`media` 或 `present:true` 判断为视频的抖音/小红书/Twitter finding，必须是 `subtitle`、`asr` 或带错误的 `failed` |
| `comments` | `status`: `captured` / `not_available` / `failed` / `not_applicable`；`count` 整数；`artifact` 在 captured 时必填；`reason` 在 failed/not_available 或 captured 且少于 10 条时必填 | 社交渠道 captured 时必须抓取 1–10 条有用评论、填写真实非空 `artifact`，并让 `content_sources` 包含 `comments`；不足 10 条时用 `reason` 说明实际不足原因。无法取得评论则写 `not_available` / `failed`、`count:0` 与具体 `reason`。`not_applicable` 仅用于 GitHub/Web 等非社交渠道 |
| `images` | `present` 布尔值；`status`: `not_present` / `ocr` / `failed` / `not_applicable`；`total`、`processed` 非负整数；`artifact` 在 ocr 时必填，`reason` 在 failed 时必填 | 数量必须满足 `0 <= processed <= total`。小红书图文 finding 的 `total` 必须等于 `raw.image_list` / `raw.images` / `media` 可核对出的实际配图数，不能少报；`ocr` 表示全部处理完成，必须 `processed == total`、填写 `artifact`，且 `content_sources` 包含 `ocr`。视频 `image_list` 仅是封面，不强制 OCR |
| `license` | `status`: `verified` / `unknown` / `not_applicable`；可选 `spdx`；`source`、`artifact` 在 verified 时必填，`reason` 在 unknown 时必填 | GitHub finding 必须读取实际许可证文件后才能写 `verified`：`source` 必须是该 finding 同一 GitHub 仓库**根目录**的 LICENSE/COPYING 文件 URL，不能拿依赖或子目录的许可证替代；`artifact` 保存该文件的真实非空文本，且 `content_sources` 包含 `license`；否则写 `unknown` 并说明原因。不能根据 README 自称推断 |

`artifact` 是相对 `<OUT_DIR>` 且位于 `artifacts/` 下的 UTF-8 字幕、ASR、OCR、评论或许可证文本路径。成功状态（视频 `subtitle` / `asr`、图片 `ocr`、评论 `captured`、许可证 `verified`）下它是**硬校验项**：路径必须指向真实存在、可读取且非空的文本文件，不能写占位文件、虚构路径或 `artifacts/` 外的路径；产物文本去除首尾空白后必须完整合入该 finding 的 `content`，artifact 只负责留档与追查，不能成为绕开 `content` 的旁路。`license.source` 在 `license.status=verified` 时同样是硬校验项，必须是 HTTPS、同一 GitHub 仓库根目录的实际 LICENSE/COPYING 文件 URL；证据 agent 必须从该 source 读取并原样保存 artifact，禁止手写许可证正文。任一条件做不到就不能填写成功状态，必须按上表记录 `failed` / `not_available` / `unknown` 及具体原因。

## note 字段质量标准（收集 agent 必读）

> 🔒 以下两个引用块是 skill 作者的**原话，一字不改**。任何未来对本 skill 的迭代**禁止润色/概括/替换**这两个块（此前已发生过三次被 AI 顺手改写）。

笔记的目的（作者原话）：

> **笔记的目的是，尽可能涵盖这个文字帖/视频里作者的观点/建议/论断/流程/方案等精华，以后回顾时如无必要只看精华笔记，无需再看冗长的原帖。**

作者人工做十一京都游玩攻略时的笔记示范（作者原话——note 就该长这样）：

> 1. 帖子 A：贵船神社可以不去，距离市区较远，而且最后一班下山的公交是 7.30，如果没赶上需要走下山或打车。走下山的话路程长而且山里夜色笼罩很恐怖，打车也很难打。
> 2. 帖子 A：清水寺建议凌晨去，人少出片，而且不要打车到正门，正门一路上山很累，可以打车到后门，一路下山，反向游览。
> 3. 帖子 B：京都琉璃光院 10.1 号是今年首次开放，需要网上提前预约。非常值得去，幽静、出片。

由示范导出的操作规则：
- **论断要带原因**："贵船神社可以不去"必须带上"最后一班下山公交 7.30、赶不上要走恐怖夜路或打难打的车"，不是光写"别去"。
- **流程/方案类内容要记步骤与顺序**：如清水寺条——打车到后门→一路下山→反向游览。不许把流程压缩成光秃秃的结论。
- **数字原样抄录**，不四舍五入、不脑补（"错字率 7.81%"照抄，不写"约 8%"）。
- **观点要注明是谁说的、是不是官方自评**（"**FunASR 官方自称** CER 7.81%"，区别于"某用户实测…"）。
- **只记这条源真正讲了的，不替它补充、不替它下结论。**

## 落盘纪律

- 一个渠道一个文件。**边搜边落盘**：开工先用 Write 创建文件并写入第 1 行 meta 占位（count:0），此后**每整理好一批就用 `cat >> 文件 <<'JSONL'` 追加**（每条一行紧凑 JSON，记录内不换行）。**绝不攒到最后一次性写**——中途失败时，已追加的条目就是抢救成果。
- 收尾回填 meta：`python3 <SKILL_DIR>/scripts/finalize_log.py --file <文件> --failures '<JSON数组，无则[]>'`（自动重算 count、写 finished、合并 failures）。
- 收集 agent **只落自己渠道的盘、不读别人的**，返回给上层的只是指针：`{channel, count, file, headlines:[前5条], failures:[]}`。
- **原文全文进 content，绝不因怕大而截断**——磁盘就是为了兜住内存兜不住的量。
