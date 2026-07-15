## GitHub（内置 GitHub MCP，无需装第三方）

- **推荐工具/方法**：会话内置 GitHub MCP 工具：`search_repositories`、`search_code`、`search_issues`、`get_file_contents`（读 README/源码）、`list_commits` / `get_latest_release`（看活跃度）。
- **搜索限定符（提质关键）**：`stars:>500`、`pushed:>2026-01-01`（近期仍维护）、`language:python`、`topic:whisper`、`in:name,description,readme`、`license:mit`。组合示例（2026-07-09 实测）：`search_repositories(query="video transcription whisper stars:>500 pushed:>2026-01-01 language:python")` → 精准命中活跃项目。issue 挖坑：`search_issues(query="pixverse api is:issue")` 看真实使用痛点与 workaround。
- **能返回（字段级）**：仓库元数据（star / fork / license / description / topics / updated_at / created_at / default_branch）、代码搜索命中（文件路径+片段）、issue/PR（标题/正文/评论/状态）、任意公开文件内容、提交历史与 release。
- **不能返回（含配套弥补）**：私有仓库（无权限时，无解）；`search_code` 只索引默认分支、且小众仓库可能未被索引 → 补：`get_file_contents` 按已知路径直接读文件；**GitHub Discussions 讨论区内容**（内置 GitHub 工具无此能力）→ 补：用通用 web 渠道的 `WebFetch`/`tavily_extract` 直接抓讨论区页面 URL（`github.com/<owner>/<repo>/discussions`），常有 issue 里没有的真实使用问答与避坑。
- **耗时/失败/处理**：单次调用 1–3s，全渠道最快。失败场景：搜索 API 速率限制（触发时报 rate limit）→ 分页 5–10 条/次、控制调用频率、稍等重试即可，无封号风险。
- **防封号**：无需担心（官方 API + 已认证账号，正常调用量不会触限）。
- **信息收集推荐用法**：找现成开源方案/SDK/工作流/awesome 列表；对每个候选**必看 star + 最后提交时间（pushed/updated_at）+ license** 判断成熟度与可复用性；用 `get_file_contents` 读 README 核实其真实声称的能力，而非只看简介；用 `search_issues` 核实"实际用起来的坑"。
- **候选发现必须走三路并合并去重**：①批准的关键词与 star/活跃度限定；②项目分类标签/awesome 列表；③头部项目 README、依赖或“相关项目”中出现的候选。第三路只用于扩面，不能只凭 agent 事先知道的项目名补齐数量。`meta.discovery.keyword.proof` 原样写全部批准关键词；category proof 写分类页/awesome 仓库 URL；related proof 写 `{from,found,via}`，明确从哪个已入选仓库、经 README/依赖/相关项目发现了哪个仓库。失败路线写空 proof 和具体原因。meta.failures 还要记录每路命中数量和任何降级。
- **许可证只认实际文件**：对最终入选项目从 HTTPS source 读取仓库根目录的 `LICENSE`/`LICENSE.*`/`COPYING`，原样保存到 license artifact 并合入 content。文件存在且内容可判断时 capture.license 标 verified 并记录 SPDX/文件 URL；缺失、冲突或自定义条款看不懂时标 unknown 并写原因。README 自称 MIT/Apache、依赖目录中的 LICENSE 或手写一份常见许可证文本都不足以确认商用许可。

### 示例（真实请求 + 返回，节选）
请求：`search_repositories(query="video transcription whisper stars:>500 pushed:>2026-01-01 language:python")`
返回（节选）：`SamurAIGPT/AI-Youtube-Shorts-Generator`（4171 star，Python，updated 2026-07-09，topics 含 whisper/auto-clip）→ 再 `get_file_contents` 读其 README 核实 Whisper 转写 pipeline 细节。

### 入选内容证据补全（在收集阶段完成）
对每个最终入选项目核对 README、活跃度、实际许可证文件，并至少抽查与调研承重能力相关的 issue。finding 的 capture 必须记录许可证 verified/unknown；unknown 必须带原因，禁止猜测。
