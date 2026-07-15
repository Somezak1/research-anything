## B站（Bilibili）

> ⚠️ **并行调研期间（research-anything 扇出）禁止编辑 `~/tools/MediaCrawler/config/` 下任何文件**（SORT_TYPE / PUBLISH_TIME_TYPE / ENABLE_GET_MEIDAS 等一律用默认值完成本次收集，受影响的能力降级并记入 failures）。配置文件是四个平台收集 agent 共享的，同时改会互相污染。下文提到的"编辑 config"仅限单渠道深挖、无并行任务时使用，用完改回默认。另：**同机同秒启动多个 MediaCrawler 实例偶发浏览器启动冲突崩溃**（TargetClosedError，2026-07-13 观测一次）——多平台并行时错开数秒启动，崩了重跑一次即可。


- **推荐工具/方法**：MediaCrawler CLI（工具目录固定为 `~/tools/MediaCrawler`，命令用绝对路径，与当前项目位置无关），`cd ~/tools/MediaCrawler && uv run main.py --platform bili --type search --keywords "<词>" --crawler_max_notes_count <N> --get_comment <bool>`。多关键词英文逗号分隔一次跑；`--start <页>` 翻页；`--specified_id <BV号或视频URL>` 直抓指定视频。
- **能返回（字段级）**：video_id / title / desc / video_type / create_time（发布时间戳）/ nickname / video_play_count / liked_count / **video_danmaku（弹幕数）** / video_coin_count / video_favorite_count / video_share_count / video_comment / video_cover_url / **video_url（注意：是视频页链接 `bilibili.com/video/av…`，不是可下载直链）** / source_keyword。评论（`--get_comment true`，默认 10 条/视频，`--max_comments_count_singlenotes` 调整；二级评论 `--get_sub_comment true`）。落 `~/tools/MediaCrawler/data/bili/jsonl/`（目录名是 `bili`）。
- **不能返回**：视频文件直链（下载见下方"视频内容提取"）；UP主原始 uid（只有 creator_hash 脱敏哈希 + nickname，深挖账号需打开 video_url 页面自取 uid 再 `--type creator`）；CC/AI 字幕（用 yt-dlp 拿，见下）。
- **耗时/失败/处理**：纯搜索（不开评论不下媒体）约 30–60s/次（含浏览器启动 ~15s）；`--crawler_max_notes_count` 是**页粒度近似值**（一页 ~20 条，要 2 条也会返回整页）。失败场景：①登录态过期→重新扫码（跑一次非 headless 手动扫）；②原版登录选择器过期（本地已打补丁，见已知坑）；③开 ENABLE_GET_MEIDAS 下载视频时**可能无限期挂死**（实测：连接 ESTABLISHED 但零流量、无超时无进度输出）→ 必须外部加超时（如 300s）并 kill 重跑，或改用 yt-dlp 下载。
- **防封号**：默认 CDP 真实浏览器模式反检测最好，别改 headless；`CRAWLER_MAX_SLEEP_SEC=2` 别调低；单次 ≤2–3 页、拉开批次间隔；`MAX_CONCURRENCY_NUM=1` 保持；搜索阶段不开评论，确定最终入选项后只补这些项的评论。
- **信息收集推荐用法**：看**深度测评/教程/长视频**；用 video_play_count + video_danmaku 看真实热度；对最终入选视频抓点赞较高的前 10 条有用评论。时间范围搜索需编辑 `~/tools/MediaCrawler/config/bilibili_config.py` 的 `BILI_SEARCH_MODE="all_in_time_range"` + `START_DAY/END_DAY`。
- **已知坑/限制**：**原版登录选择器过期，本地已打补丁**（`media_platform/bilibili/login.py`：改走 passport.bilibili.com 登录页 + 登录后导航回首页）。`git pull` 升级 MediaCrawler 会覆盖补丁，需重打。

### 示例（真实请求 + 返回，节选）
请求：`uv run main.py --platform bili --type search --keywords "AI漫剧" --crawler_max_notes_count 2`
返回（data/bili/jsonl/search_contents_<date>.jsonl 一行，节选）：
    {"video_id":"116414367144127","title":"我的ai漫剧制作流程全分享","video_type":"video",
     "video_play_count":"...","video_danmaku":"...","create_time":...,
     "video_url":"https://www.bilibili.com/video/av116414367144127","source_keyword":"AI漫剧"}

### ⚠️ Cookie 运行侧约束
**读 cookie 的方式有讲究**：禁止用 `--cookies-from-browser`（每次读 Chrome cookie 都触发一次 macOS 钥匙串授权弹窗）；一律用已导出的 cookie 文件 `--cookies ~/tools/bili_cookies.txt`（2026-07-13 导出，零弹窗、无人值守可跑）。cookie 过期（B站登录态以月计）→ 重新扫码登录后用 `--cookies-from-browser` 重导一次（会弹一次窗，需用户在场）。凭据文件绝不写进 skill/报告。

### 入选内容证据补全（在收集阶段完成；优先 ai-zh、无字幕才 fun-asr）
调研收集时对全部入选视频取口播全文进 content（**不设条数预算**——2026-07-13 拍板）。
1. **ai-zh 字幕直取（首选：零成本、零下载、无人值守，2026-07-13 实测）**：B站多数视频有官方 AI 字幕（ai-zh），用导出的 cookie 文件直取（秒级、几 KB，不用下载视频）：

       yt-dlp --cookies ~/tools/bili_cookies.txt \
              --write-subs --sub-langs "ai-zh" --skip-download -o "<out>" "<视频页链接>"

   产出 `<out>.ai-zh.srt`（逐句口播全文）。**已知弱点（2026-07-13 同视频对照实测）**：专有名词易错——ai-zh 把"通义听悟"写成"同意提物"、"纪要"写成"记要"并偶有丢字，fun-asr 同视频全对（两者整体相似度 97.5%）。**笔记里承重的工具名/术语若来自 ai-zh 字幕要留心，存疑就用路线 2 精转**。"无字幕"判定：`--list-subs` 无 ai-zh 行，或执行后无 .srt 产出 → 走路线 2。
2. **fun-asr 转写（无字幕视频的兜底；也是承重术语存疑时的精转选项，2026-07-13 实测）**：`yt-dlp -f "bv*[height<=720]+ba/b" -o v.mp4 "<视频页链接>"`（匿名下载无弹窗；14 分钟 720p 实测仅 19MB）→ `python3 <SKILL_DIR>/scripts/transcribe.py --file v.mp4 --out <输出前缀>`。实测：14 分钟视频 17 秒、70 分钟音频 66 秒（≈60 倍实时）；直传视频容器与抽音轨 mp3 结果等价（相似度 99.3%），无需 ffmpeg。前置：DASHSCOPE_API_KEY（见 SKILL.md 前置依赖）。旧的手动路线（复用 MediaCrawler 登录态直读浏览器 cookie）已废弃：
   该方式每次触发钥匙串弹窗，仅在 cookie 文件过期需要重导时用一次（见顶部 ⚠️）。
3. 弹幕 xml 无需登录可直接下（`--list-subs` 可见 danmaku），是观众反应信号；MediaCrawler `ENABLE_GET_MEIDAS=True` 下载视频有挂死风险（见上），一律用路线 1 的 yt-dlp 代替。
4. finding 的 capture 必须记录字幕/ASR、评论的完成状态、产物路径或具体失败原因；只有标题/简介不合格。ai-zh 中承重工具名存疑时必须走路线 2 复核。
