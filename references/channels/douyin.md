## 抖音（Douyin）

> ⚠️ **并行调研期间（research-anything 扇出）禁止编辑 `~/tools/MediaCrawler/config/` 下任何文件**（SORT_TYPE / PUBLISH_TIME_TYPE / ENABLE_GET_MEIDAS 等一律用默认值完成本次收集，受影响的能力降级并记入 failures）。配置文件是四个平台收集 agent 共享的，同时改会互相污染。下文提到的"编辑 config"仅限单渠道深挖、无并行任务时使用，用完改回默认。另：**同机同秒启动多个 MediaCrawler 实例偶发浏览器启动冲突崩溃**（TargetClosedError，2026-07-13 观测一次）——多平台并行时错开数秒启动，崩了重跑一次即可。


- **推荐工具/方法**：MediaCrawler CLI（工具目录固定为 `~/tools/MediaCrawler`，命令用绝对路径，与当前项目位置无关），`cd ~/tools/MediaCrawler && uv run main.py --platform dy --type search --keywords "<词>" --crawler_max_notes_count <N> --get_comment <true|false>`。多关键词英文逗号分隔一次跑；`--start <页>` 翻页；按发布时间筛选需编辑 `~/tools/MediaCrawler/config/dy_config.py` 的 `PUBLISH_TIME_TYPE`（0 不限/1 一天内/7 一周内/180 半年内，无 CLI flag）。
- **能返回（字段级）**：aweme_id / aweme_type / title / desc / create_time（发布时间戳）/ nickname / liked_count·collected_count·comment_count·share_count / **video_download_url（无水印视频直链）** / note_download_url（图文帖图片）/ music_download_url / cover_url / aweme_url / source_keyword；评论（`--get_comment true`）→ content / like_count / nickname / sub_comment_count / parent_comment_id（默认 10 条/帖，`--max_comments_count_singlenotes` 调整；二级评论 `--get_sub_comment true`）。落 `~/tools/MediaCrawler/data/douyin/jsonl/`。
- **不能返回**：视频转写/字幕（抖音无公开字幕接口，转文字须下载后转写）；作者原始 sec_uid（只有 creator_hash 脱敏哈希 + nickname，深挖账号需打开 aweme_url 页面从 URL 自取 sec_uid 再 `--type creator --creator_id <sec_uid>`）。
- **耗时/失败/处理**：纯搜索约 30–60s/次（含浏览器启动 ~15s）；`--crawler_max_notes_count` 是**页粒度近似值**（要 2 条实际返回 ~6+ 条）。失败场景：①登录态过期→重新扫码；②风控滑块/手机验证→保持 `HEADLESS=False`（默认）手动过一次即恢复；③`--type detail` 有登录检测 bug，用 `--type search` 或按 URL 搜标题代替；④开 ENABLE_GET_MEIDAS 会给**整页所有条目**串行下视频（实测 6 条 ~15 分钟、230MB），且有挂死风险→外部加超时。
- **防封号**：默认 CDP 真实浏览器模式；`CRAWLER_MAX_SLEEP_SEC=2` 别调低；单次 ≤2–3 页、批次拉开间隔；搜索阶段不开评论，确定最终入选项后只补这些项的评论；**不开 ENABLE_GET_MEIDAS**。
- **信息收集推荐用法**：`--type search` 关键词搜 → 按 liked_count 数值排序取头部 → 对最终入选视频用 `--get_comment true` 抓点赞较高的前 10 条有用评论；深挖标杆账号用 `--type creator`。评论失败必须写明原因，不能以“按需”跳过。
- **已知坑/限制**：本地已设 `config/base_config.py` 的 `CDP_CONNECT_EXISTING=False`（不接管日常 Chrome，起独立实例）。

### 示例（真实请求 + 返回，节选）
请求：
    cd ~/tools/MediaCrawler
    uv run main.py --platform dy --type search --keywords "萌娃柯基" --crawler_max_notes_count 10 --get_comment false
返回（data/douyin/jsonl/search_contents_<date>.jsonl 一行，节选）：
    {"aweme_id":"7335764691886673192","title":"呆萌萌小公主来啦～#柯基 #狗狗 #养狗的乐趣",
     "liked_count":1367498,"comment_count":93315,"share_count":1680527,
     "video_download_url":"https://www.douyin.com/aweme/v1/play/?video_id=v0300fg10000cn6ttdjc77u6dodim56g&line=0",
     "aweme_url":"https://www.douyin.com/video/7335764691886673192","source_keyword":"萌娃柯基"}

### 入选内容证据补全（在收集阶段完成）
- **下载（已实测 2026-07-09）**：`video_download_url` 是**稳定 API 链接**（抓取次日仍有效），302 到时效 CDN 直链；`curl -L -o out.mp4 "<video_download_url>"` 即可，**无需 referer、无需 UA**。推荐流程：搜完先按互动数选片，只对入选视频 curl 下载（别开 ENABLE_GET_MEIDAS 全量下）。
- **转写（2026-07-13 实测）**：`video_download_url` 可**免下载直传** fun-asr：`python3 <SKILL_DIR>/scripts/transcribe.py --url "<video_download_url>" --out <输出前缀>`（3 条实测全部成功，3.6–17 秒/条，可并发；脚本内置 180s 慢链熔断 + 下载-上传自动降级）。调研收集时**对全部入选视频转写口播、全文进 content（不设条数预算——2026-07-13 拍板）**。注意：带人声演唱的 BGM 段会转出歌词乱码，做笔记时对开头/间奏段留意。前置：DASHSCOPE_API_KEY（见 SKILL.md 前置依赖）。
- finding 的 capture 必须记录视频与评论的完成状态、产物路径或具体失败原因；只写标题/简介不合格。
