## 小红书（Xiaohongshu）—— 两套工具互补

> ⚠️ **并行调研期间（research-anything 扇出）禁止编辑 `~/tools/MediaCrawler/config/` 下任何文件**（SORT_TYPE / PUBLISH_TIME_TYPE / ENABLE_GET_MEIDAS 等一律用默认值完成本次收集，受影响的能力降级并记入 failures）。配置文件是四个平台收集 agent 共享的，同时改会互相污染。下文提到的"编辑 config"仅限单渠道深挖、无并行任务时使用，用完改回默认。另：**同机同秒启动多个 MediaCrawler 实例偶发浏览器启动冲突崩溃**（TargetClosedError，2026-07-13 观测一次）——多平台并行时错开数秒启动，崩了重跑一次即可。


> ⚠️ **运行侧约束：MCP 未登录时，调研直接走工具 B，禁止唤登录**。工具 A（MCP）在调研里只是"秒级快搜"的锦上添花，不是必需；它真正不可替代的用途是**发布笔记**。若 `check_login_status` 显示未登录，**不要调 `get_login_qrcode` 去唤醒登录**（会弹小红书扫码页打扰用户）——直接用工具 B（MediaCrawler，独立持久化登录）完成搜集。登录留给"发布"场景。

### 工具 A：xiaohongshu-mcp（MCP 原生，秒级快搜，可发布）
- **可靠操作**：`check_login_status`、普通 `search_feeds(keyword)`、`get_feed_detail(feed_id, xsec_token)`（默认返回正文+**前 10 条一级评论**，够用）、`user_profile(user_id, xsec_token)`、`publish_content` / `publish_with_video`。
- **超时操作（不要用）**：`search_feeds` 带 filters（筛选要点击交互→超时）、`get_feed_detail` 传 `load_all_comments=true`（滚动加载评论→超时）。
- **能返回（字段级）**：标题 / desc（文案+话题标签）/ type（video/normal）/ 封面图 imageList / 互动数（likes/collects/comments/shares）/ xsecToken / 前 10 条评论（detail 时）。
- **不能返回**：视频下载链接；发布时间（搜索列表里无 time 字段，要时间用工具 B）。
- **耗时/失败/处理**：单次调用秒级；失败场景：①服务未起/网关 502 → `~/tools/xiaohongshu-mcp/server.sh restart`；②登录态失效 → 调研场景一律改走工具 B（禁止唤登录，见顶部⚠️）；仅发布场景才用 `get_login_qrcode` 重新扫码。
- **注意**：字段名映射——`search_feeds` 返回的 `id` 就是后续调用要传的 `feed_id`，返回的 `xsecToken` 传给 `xsec_token` 参数。`user_profile` 需**用户专属** xsec_token；用某条笔记的 xsecToken 调 user_profile 会返回空。
- **前置**：服务需常驻 `~/tools/xiaohongshu-mcp/server.sh start`，**且该 MCP server 需已注册进当前会话的 MCP 配置**——仅服务进程在跑不等于 agent 有工具函数（2026-07-13 实录：收集 agent 发现 MCP 函数未注入）。函数未注入时按未登录同款规则直接走工具 B，不算失败，在 meta.failures 记一笔即可。

### 工具 B：MediaCrawler `--platform xhs`（要视频/评论/发布时间/深度用这个）
- `cd ~/tools/MediaCrawler && uv run main.py --platform xhs --type search --keywords "<词>" --crawler_max_notes_count <N> --get_comment <bool>`。多关键词英文逗号分隔；已知笔记 URL（须带 xsec_token 参数）用 `--type detail --specified_id "<note_url>"`。
- **能返回（字段级）**：note_id / title / desc / type（video/normal）/ **tag_list** / time（发布时间戳）/ nickname / liked·collected·comment·share_count / image_list / **video_url（带签名的 mp4 直链）** / note_url（含 xsec_token）/ xsec_token / source_keyword。评论、`--type creator` 官方支持。落 `~/tools/MediaCrawler/data/xhs/jsonl/`。
- **不能返回**：作者原始 user_id（只有 creator_hash 脱敏哈希 + nickname）；**精确互动数**——liked_count 等是 `"9.3万"`/`"10万+"` 模糊字符串，**不能数值排序**。平台返回顺序由 `~/tools/MediaCrawler/config/xhs_config.py` 的 `SORT_TYPE` 决定（无 CLI flag；**默认 popularity_descending 已是最热优先，热度排序无需改配置**；要追最新动向才需 time_descending——并行调研期间禁改，降级记 failures）。
- **耗时/失败/处理**：纯搜索约 1 分钟（含浏览器启动）；`--crawler_max_notes_count` 是**页粒度近似值**（要 2 条返回整页 20 条）；开 `ENABLE_GET_MEIDAS` 会给整页每条下视频（实测 20 条 19 视频共约 10 分钟，300MB+）。失败场景：①登录态过期→重新扫码；②风控验证码→`HEADLESS=False`（默认）手动过。
- **防封号**：CDP 真实浏览器模式默认开；`CRAWLER_MAX_SLEEP_SEC=2` 别调低；小红书风控较严，单次 ≤1–2 页、批次拉开间隔；搜索阶段不开评论，确定最终入选项后只补这些项的评论。

- **信息收集推荐用法**：要快速探关键词/看封面文案 → 工具 A；最终入选内容一律用工具 B 补齐发布时间、视频链接和点赞较高的前 10 条有用评论（`--get_comment true`）。`SORT_TYPE=time_descending` 可追最新动向。评论失败必须写明原因。

### 示例（真实请求 + 返回，节选）
工具 A：`search_feeds(keyword="柯基萌娃")`
    {"id":"6954013d00000000210285fc","noteCard":{"type":"video",
     "displayTitle":"羡慕我的小孩啊！一出生就有狗陪",
     "interactInfo":{"likedCount":"3585","commentCount":"179","collectedCount":"325"}},
     "xsecToken":"ABxxxxxxxxxxxxxxxxxxxx..."}
工具 B（2026-07-09 实测）：`uv run main.py --platform xhs --type search --keywords "AI漫剧" --crawler_max_notes_count 2`
    {"note_id":"...","type":"video","title":"AI漫剧保姆级教程，全流程只要一个人就够了",
     "liked_count":"1.3万","tag_list":"...","time":...,
     "video_url":"http://sns-video-zl.xhscdn.com/stream/.../01ea...mp4?sign=811b...&t=...",
     "note_url":"https://www.xiaohongshu.com/explore/...?xsec_token=..."}

### 入选内容证据补全（在收集阶段完成）
- **下载（已实测 2026-07-09）**：工具 B 的 `video_url` 是带签名直链，**新鲜时 `curl -o out.mp4 "<video_url>"` 直接可下（200，无需 referer）**，**时效比想象中长（2026-07-13 实测修正）**：14 小时前的链接仍可用且速度正常；5 天前的链接仍返回 200 但服务端拉取慢约 50 倍（fun-asr 任务 6 秒变 313 秒）——稳妥做法仍是抓完尽快处理。两种可靠做法：①抓完立刻对入选视频 curl 下载；②抓取时就开 `ENABLE_GET_MEIDAS=True`（落 `~/tools/MediaCrawler/data/xhs/videos/<note_id>/0.mp4`，代价是整页全量下载耗时）。工具 A 拿不到视频链接。
- **转写（2026-07-13 实测）**：新鲜 `video_url` 可**免下载直传** fun-asr：`python3 <SKILL_DIR>/scripts/transcribe.py --url "<video_url>" --out <输出前缀>`；慢链/失效链接脚本自动降级为下载-上传。调研收集时**对全部入选视频转写口播、全文进 content（不设条数预算——2026-07-13 拍板）**。注意：带人声演唱的 BGM 段会转出歌词乱码（本渠道实测样本即出现）。前置：DASHSCOPE_API_KEY（见 SKILL.md 前置依赖）。
- **图片文字**：对最终入选**图文笔记**的 `image_list` 全部执行 `python3 <SKILL_DIR>/scripts/ocr_images.py --out <输出前缀> <图片URL...>`。`capture.images.total` 必须等于原始 `image_list` 的实际数量，不能只写已处理数量；把 `.ocr.txt` 中的文字按图片顺序追加到 content，并在 note 中吸收与调研相关的步骤/价格/踩坑；装饰图识别为空也要计入 processed。视频记录里的 `image_list` 只是封面，不强制 OCR。任何图片失败都写入 capture 和渠道 failures。
- finding 的 capture 必须记录视频、评论、图片的完成数量、产物路径或具体失败原因；正文非空不代表配图/视频已处理。
