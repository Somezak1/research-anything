## YouTube（yt-dlp，已装 /opt/homebrew/bin/yt-dlp）

海外教程/工作流视频的主渠道，且是**唯一不需要转写管线就能拿到视频文字内容**的视频渠道（多数教程视频有字幕，直取即口播全文）。**连通性随网络环境波动**：2026-07-09 实测本机直连可达；2026-07-13 实测直连超时且本地代理（127.0.0.1:7897）上游故障、全程不可达——开工先自检 `curl -sS -m 8 -o /dev/null -w '%{http_code}' https://www.youtube.com`，不可达时走文末"降级 SOP"并在 meta.failures 如实申报，不反复无效重试。

- **推荐工具/方法**：
  - 搜索：`yt-dlp "ytsearchN:<英文关键词>" --flat-playlist --dump-json`（N=条数，如 ytsearch10:）；补充面用 `WebSearch` 加 `site:youtube.com`。
  - 详情：`yt-dlp --dump-json "<视频URL>"`（全量元数据，含可用字幕语言列表）。
  - **字幕直取（核心用法）**：`yt-dlp --write-subs --write-auto-subs --sub-langs "en,zh.*" --skip-download -o "<out>" "<视频URL>"` → 产出 `.vtt`。
- **能返回（字段级）**：搜索 → title / url / duration（秒）/ view_count / channel；详情 → 另有 like_count / upload_date（发布时间）/ description / chapters / 字幕语言列表 / channel_id；字幕 .vtt（口播全文带时间轴）；`--write-comments` 可拉评论（慢，按需）。
- **不能返回**：会员专属/私享视频；dislike 数；auto-subs 对专有名词识别较差（人工字幕优先：--write-subs 有则用它）。
- **耗时/失败/处理**：搜索 ~10s；字幕下载秒级（百 KB）。失败场景：①无字幕视频（教程类少见）→ 留给转写管线；②偶发限流/JS 播放器变更 → 升级 `brew upgrade yt-dlp` 基本能解；③auto-subs 的 .vtt 是滚动式、行大量重复 → 需去重清洗（已验证的清洗逻辑：逐行去 `<tag>`、跳过时间轴/头部行、按整行去重，即得干净全文）。
- **防封号**：无账号体系风险（匿名访问公开内容）；控制批量（一次 ≤10–20 个视频的字幕），避免高频大量拉取触发 IP 限流。
- **信息收集推荐用法**：英文关键词搜教程/工作流 → 按 view_count/duration 选中长教程 → 字幕直取全文进 content；对最终入选视频用 `--write-comments` 抓点赞较高的前 10 条有用评论。频道页可当“标杆账号”深挖。

### 示例（真实请求 + 返回，2026-07-09 实测）
请求：`yt-dlp "ytsearch3:AI comic drama workflow tutorial" --flat-playlist --dump-json`
返回（节选字段）：
    {"title": "How To Make an AI Animated Short Film (Full Workflow)",
     "url": "https://www.youtube.com/watch?v=zYPgz6sOy74",
     "duration": 997, "view_count": 302387, "channel": "Higgsfield AI"}
字幕直取：`yt-dlp --write-auto-subs --sub-langs "en" --skip-download -o t "https://www.youtube.com/watch?v=zYPgz6sOy74"`
→ `t.en.vtt`（116KB），清洗去重后 382 行干净口播全文："Animation used to be studio-exclusive, requiring huge budgets and years of training. Now, it takes a couple hours and the right prompts. ..."

### 入选内容证据补全（在收集阶段完成）
本渠道多数视频字幕直取即可。仅无字幕视频：`yt-dlp -f "bv*[height<=720]+ba/b" -o v.mp4 "<URL>"` 下载，再按详情元数据的语言调用转写；英文视频使用 `python3 <SKILL_DIR>/scripts/transcribe.py --file v.mp4 --out <输出前缀> --language en`。finding 的 capture 必须记录字幕/ASR、评论的完成状态、产物路径或具体失败原因。

### 直连不可达时的降级 SOP（2026-07-13 实录）
- **搜索**：`WebSearch`/tavily 加 `site:youtube.com` 找候选。排序是相关性而非播放量，只能在已抓候选内按 view_count 择优——该局限要写进 meta.failures，不冒充全量排序。
- **元数据/字幕**：`tavily_extract` 渲染 watch 页可拿标题/简介/章节和**部分**字幕片段（含 `[...]` 缺口，非完整 .vtt）。残片可按 `subtitle` 记录，但必须在 note 与 meta.failures 注明"tavily 渲染残片"，承重结论不得只压在缺口段上；渲染多轮仍只回播放器 UI 的条目按 failed 申报。
- **评论兜底**：Invidious comments API（如 inv.nadeko.net，经 tavily 服务器端调用）可拿点赞排序评论；Invidious captions API 与第三方字幕站（youtubetranscript.com / tactiq）已被 YouTube 封锁，实测不可用，别再浪费轮次。
- **ASR 兜底不可行**：本机连不上就下载不了媒体文件，已授权也无从转写——按 failed 如实申报，不硬凑。
