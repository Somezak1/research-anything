# research-anything

一个 Claude Code skill：丢给它一个还没想清楚的想法，它会跨**抖音 / 小红书 / 知乎 / B站 / YouTube / GitHub / Twitter(X) / 通用网页** 8 个渠道收集市面上的真实做法，逐条核实后，产出 1–3 个可落地方案和一份带出处的调研报告。

## 能解决什么问题

你大概遇到过这类场景：

- 有个探索性想法（"想做 AI 漫剧"、"视频转文字该选哪个方案"、"搭一套 XX 工作流"），但不知道市面上的成熟路径是什么，怕闭门造车、做出来才发现方案已经落后好几代；
- 手动调研要开十几个 App 和网站，刷几十条视频和帖子，费时费力，还容易困在单一平台的信息茧房里；
- 让 AI 直接"帮我调研一下"，得到的往往是它训练数据里的旧知识 + 几次浅层网页搜索，看不到抖音/小红书/B站上从业者的一手实操内容。

research-anything 就是为这类问题做的：**把"全渠道搜集 → 证据核实 → 综合成可执行方案"整个流程固化下来，交给 Claude Code 自动跑完。**

## 优势

- **8 渠道全覆盖，不漏一手信息**：中文短视频/图文社区（抖音、小红书、B站、知乎）+ 海外（YouTube、Twitter/X）+ 开源（GitHub）+ 通用网页，一次跑完。很多真正有用的实操经验只存在于短视频和社区帖里，普通网页搜索根本搜不到。
- **不止看标题，证据要完整**：视频会取字幕或转写口播全文，图文笔记会识别配图里的文字，帖子会抓高赞评论，GitHub 项目会核对根目录的真实 LICENSE 文件。结论建立在完整证据上，不靠标题和简介脑补。
- **逐条核实，不轻信**：关键数字和说法分两类定点核查——事实题（价格/授权/有无接口）问官方，品质题（准不准/好不好用）问独立口碑；厂商自评一律标注"厂商自评"，核实不了的明说"未证实"。
- **产出可执行，不甩选择题**：交付的不是一堆并列选项，而是"**默认路径 + 什么情况下切换到备选**"，包含 `report.html`（给人看的完整报告）和 `runbook.json`（给 AI 执行的命令级方案）。
- **全程可追溯**：每个结论都带来源编号，所有原始笔记落盘在你项目的 `docs/research/` 下，随时反查原帖。
- **中途和你对齐**：搜集计划先给你过目批准；出方案前会先把调研中出现的陌生名词、多方印证过的关键结论讲给你听，答疑之后才让你做取舍。
- **费用透明**：整个流程唯一可能花钱的环节是可选的付费语音转写（约 0.8 元/小时），且必须先给出费用上限、经你明确同意才会调用；不配置也能跑（改用各平台免费字幕）。

一次完整调研典型耗时 30–60 分钟（可通过调低每渠道抓取深度换速度）。

## 运行环境要求

- **Claude Code**（skill 依赖其子 agent / Workflow 编排能力运行；安装配置这一步用 Claude Code 或 Codex 代劳都行）
- **macOS**（目前仅在 macOS 上实测；图片文字识别用的是 macOS 系统能力）
- 能正常访问对应平台的网络环境（YouTube / Twitter 视你的网络情况，不可达时对应渠道会如实申报降级，不影响其他渠道）

## 安装（傻瓜式：整段复制给 Claude Code / Codex）

把下面整段话直接粘贴给 Claude Code（或 Codex），让它替你干活：

```text
请一步步帮我安装并配置 research-anything（一个 Claude Code 调研 skill）：

1. 克隆 skill 本体：
   git clone https://github.com/Somezak1/research-anything.git ~/.claude/skills/research-anything

2. 创建工具目录 ~/tools/ 并安装采集工具（skill 文档默认所有工具都在 ~/tools/ 下）：
   - git clone https://github.com/NanmiCoder/MediaCrawler.git ~/tools/MediaCrawler
     并按它的 README 用 uv 装好依赖（用于抖音/小红书/知乎/B站四个平台的采集）
   - 安装 yt-dlp：brew install yt-dlp（用于 YouTube/B站字幕直取）

3. 确认 Claude Code 已配置 GitHub MCP（官方 github 插件/MCP server），没有就帮我配好
   （GitHub 渠道靠它搜索仓库、读 README 和 LICENSE）

4.（可选，要跑 Twitter 渠道才做）在 ~/tools/twscrape 下用 uv 建独立虚拟环境并安装
   twscrape（https://github.com/vladkens/twscrape）

5.（可选，小红书秒级快搜）安装 https://github.com/xpzouying/xiaohongshu-mcp 到
   ~/tools/xiaohongshu-mcp，并注册进 Claude Code 的 MCP 配置
   （不装也不影响：小红书默认走 MediaCrawler 采集）

装完后逐项汇报成功/失败，失败的项告诉我如何手动处理。
```

> 工具目录必须是 `~/tools/`（skill 内所有命令按这个路径写）。已经装在别处的话，做个软链接即可：`ln -s <你的工具目录> ~/tools`。

## 首次配置（一次性，需要你本人在场）

以下几步涉及扫码登录和账号凭据，AI 替代不了你，但每项只需做一次：

1. **四平台登录（必做）**：在 `~/tools/MediaCrawler` 下对抖音/小红书/知乎/B站各跑一次搜索命令（例如 `uv run main.py --platform xhs --type search --keywords "测试"`），弹出浏览器后扫码登录。登录态会持久化，之后无人值守可跑。
2. **Twitter（可选）**：准备一个**小号**（有限流/封号风险，绝不要绑主号），浏览器登录后取 `auth_token` 和 `ct0` 两个 cookie，执行 `~/tools/twscrape/.venv/bin/twscrape add_cookie <用户名> 'auth_token=...; ct0=...'`。不配置则 Twitter 渠道申报失败、其余照跑。
3. **B站 AI 字幕 cookie（可选）**：从浏览器导出 B站 cookie 保存为 `~/tools/bili_cookies.txt`（Netscape 格式，用浏览器扩展如 Get cookies.txt LOCALLY 导出）。不配置则 B站视频走付费转写或申报失败。
4. **付费语音转写（可选）**：开通阿里云百炼的 fun-asr（约 0.8 元/小时，开通后有免费额度），把 API Key 写进 `~/.zshrc`：`export DASHSCOPE_API_KEY=你的key`。不配置则抖音/小红书视频无法转写口播，只能用帖子文字和评论。

以上可选项都遵循同一原则：**缺了哪个，对应能力如实降级并在报告里申报，绝不静默装作没事。**

## 使用

在任意项目里打开 Claude Code，直接说出你的想法即可自动触发，例如：

> 我想做 AI 漫剧，帮我调研一下市面上的成熟做法

> 用 research-anything 调研：视频转文字的方案选型

之后的体验：它先给你一份**搜集计划**（渠道 × 关键词 × 深度 × 预计耗时/费用）等你批准 → 自动跨渠道收集并核实 → 把陌生名词和多方印证的结论**讲给你听、回答你的疑问** → 你做几个关键取舍 → 在你项目的 `docs/research/<主题>/` 下产出：

| 文件 | 用途 |
|---|---|
| `report.html` | 给人看的完整报告：时间线、各渠道景观、默认方案+切换条件、对比矩阵、全部来源 |
| `runbook.json` | 给 AI 执行的方案：命令级步骤、备选切换条件、已核实/未核实清单 |
| `raw/`、`verify/`、`qa.md` | 全部原始笔记、核查裁决、问答存档，供追溯 |

## 注意事项

- 采集的内容仅供个人调研使用，请遵守各平台的服务条款，控制抓取频率（skill 内已写入防风控约束）。
- Twitter 渠道只用小号；所有登录态、cookie、API Key 都只保存在你本机（`~/tools/` 与环境变量），**本仓库不含任何凭据，也永远不要把凭据写进 skill 文件或调研报告**。
- 本 skill 不保证各平台采集工具永久可用（平台风控和接口随时会变），工具失效时请更新对应上游项目。

## 仓库结构

```
research-anything/
├── SKILL.md               # skill 入口：流程与铁律
├── references/            # 各阶段执行规程 + 8 个渠道的操作文档
│   └── channels/
└── scripts/               # 采集编排、日志校验、转写/OCR、报告配图等脚本（含测试）
```
