## Twitter / X（twscrape，独立 venv）

- **前置（必查）**：`cd ~/tools/twscrape && .venv/bin/twscrape accounts` 确认已有可用账号（cookie 账号 logged_in 显示 0 属正常）。**accounts.db 为空时搜索会静默返回空/报错**——先按下方"已知坑"的 add_cookie 方式加账号。搜索返回空时第一排查项就是账号状态。
- **推荐工具/方法**：`cd ~/tools/twscrape && .venv/bin/twscrape search "<query>" --limit <N>`（输出 JSONL 每行一条推文）；`user_by_login <name>` 查账号 → `user_tweets <user_id> --limit <N>` 拉时间线；`tweet_details <id>` 单推详情；**`tweet_replies <id> --limit <N>` 拉某条推文下网友回复的正文**（搜索/详情只给回复数量，要读回复内容用这个）；`tweet_thread <id>` 拉作者自己的连续长贴（thread）。多账号轮换、内置限流处理。
- **高级搜索语法（2026-07-09 实测）**：`from:OpenAI`、`since:2026-06-01 until:2026-07-01`、`min_faves:100`、`min_retweets:10`、`min_replies:5`（挑有讨论的帖）、`lang:zh`、`filter:native_video`（带视频的推文）均可用；**`filter:video` 会报 API 错误（"Dependency: Unspecified"），必须用 `filter:native_video`**。组合如：`"AI video workflow min_faves:100 since:2026-06-01"`。
- **能返回（字段级）**：推文 id / url / date（发布时间）/ user（username·displayname·followersCount 等完整作者信息）/ **rawContent 正文** / likeCount·retweetCount·replyCount·quoteCount·viewCount / lang / media（图片与视频变体链接）。**网友回复的正文**不在搜索结果里（搜索只给 replyCount 回复数量），要读回复内容 → 拿该推文 id 调 `tweet_replies`（返回同样的推文结构：每条回复的 rawContent 正文 / 作者 / likeCount）。
- **不能返回**：登录墙后私密内容/受限账号；推文全文超长折叠部分可能截断；X 反爬/Cloudflare 变更时可能整体失效需升级 twscrape。（回复内容不是"不能返回"，是要多走一步 `tweet_replies`，见上。）
- **耗时/失败/处理**：一次搜索约 **7s/页**；`--limit` 是**页粒度近似值**（一页 ~20 条，`--limit 3` 也返回整页）。失败场景：①限流（约 50 次/15 分钟/账号）→ twscrape 自动轮换等待，多等即可；②个别高级操作符报 "API unknown error ... Dependency"（如 filter:video）→ 换等价操作符；③cookie 失效→重新取 cookie `add_cookie`（见坑）。
- **防封号**：**只用小号**（有养号/限流/封号风险，绝不绑主号）；凭据只在 `~/tools/twscrape/accounts.db`，**绝不写进 skill/报告**；控制频率（依赖内置限流即可，勿绕过）；查询量大时多加几个小号轮换。
- **信息收集推荐用法**：查海外同题趋势与方法论帖；按 likeCount/viewCount 排序取高互动，deprioritize 纯 hype；`from:` 锁定官方账号。最终入选推文必须用 `tweet_replies` 抓点赞较高的前 10 条有用回复，失败要写明原因。
- **已知坑/限制**：
  - **登录方式**：用户名/密码 `login_accounts` 常被 X 的 Cloudflare 403 拦截；**改用 cookie 方式**——浏览器登录后取 `auth_token`+`ct0` cookie，`.venv/bin/twscrape add_cookie <user> 'auth_token=...; ct0=...'`（若账号已存在需先 `del_accounts <user>` 再加）。`accounts` 列表里 cookie 账号 logged_in 显示 0 属正常，能搜就是好的。
  - twscrape 用 uv 独立 venv 安装（`uv pip install --python ~/tools/twscrape/.venv/bin/python twscrape`），不污染 MediaCrawler 环境；X 接口变更时 `-U` 升级。

### 示例（真实请求 + 返回，节选）
请求：
    cd ~/tools/twscrape
    .venv/bin/twscrape search "AI video generation min_faves:100" --limit 3
返回（JSONL 每行一条，节选关键字段）：
    {"id": 2074518428229210466,
     "url": "https://x.com/GrowAIHub/status/2074518428229210466",
     "date": "2026-07-07 15:38:30+00:00",
     "rawContent": "...", "likeCount": ..., "viewCount": ...}
读某条推文下的网友回复（2026-07-09 实测）：
    .venv/bin/twscrape tweet_replies 2074929748518855127 --limit 3
    → 每行一条回复，结构同推文：{"user":{"username":"..."},"rawContent":"Easy workflow with impressive results","likeCount":0}

### 入选内容证据补全（在收集阶段完成）
**所有入选且带视频的推文都必须取得字幕或执行 ASR，正文已经足够也不是例外。** 有可用字幕时优先取字幕；否则从 twscrape 返回的媒体变体选最高码率 mp4 直链，运行 `python3 <SKILL_DIR>/scripts/transcribe.py --url "<mp4直链>" --out <输出前缀> --language <推文/视频语种>`；没有直链时再用 yt-dlp 下载推文 URL 后走 `--file`。调用付费 ASR 前必须核对 `<OUT_DIR>/manifest.json` 的 `asr_authorization`，未获明确授权时不得调用，并将 `capture.video` 记为带具体 `error` 的 `failed`。成功的字幕/ASR artifact 必须是真实非空文本且全文已合入 finding 的 `content`；finding 的 capture 必须记录视频、回复的完成状态、产物路径或具体失败原因，不能因正文足够而写 `not_present` 或跳过。
