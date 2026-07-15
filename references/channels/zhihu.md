## 知乎（Zhihu）

> ⚠️ **并行调研期间（research-anything 扇出）禁止编辑 `~/tools/MediaCrawler/config/` 下任何文件**（SORT_TYPE / PUBLISH_TIME_TYPE / ENABLE_GET_MEIDAS 等一律用默认值完成本次收集，受影响的能力降级并记入 failures）。配置文件是四个平台收集 agent 共享的，同时改会互相污染。下文提到的"编辑 config"仅限单渠道深挖、无并行任务时使用，用完改回默认。另：**同机同秒启动多个 MediaCrawler 实例偶发浏览器启动冲突崩溃**（TargetClosedError，2026-07-13 观测一次）——多平台并行时错开数秒启动，崩了重跑一次即可。


- **推荐工具/方法**：MediaCrawler CLI（工具目录固定为 `~/tools/MediaCrawler`，命令用绝对路径，与当前项目位置无关），`cd ~/tools/MediaCrawler && uv run main.py --platform zhihu --type search --keywords "<词>" --crawler_max_notes_count <N> --get_comment <true|false>`。多关键词英文逗号分隔；`--start <页>` 翻页；已知某个高赞回答/专栏/视频的 URL 时用 `--type detail --specified_id "<URL>"` 直抓全文（支持 answer / article / zvideo 三种 URL，zvideo 路径未实测）。
- **能返回（字段级）**：content_type（answer/article）/ **content_text 全文**（数百到数万字）/ title / desc / voteup_count / comment_count / content_url / content_id / question_id / created_time / updated_time（发布与更新时间）/ user_nickname / source_keyword，落 `~/tools/MediaCrawler/data/zhihu/jsonl/search_contents_*.jsonl`；评论（`--get_comment true`）→ content / like_count / dislike_count / publish_time / sub_comment_count / parent_comment_id / user_nickname，落同目录 `search_comments_*.jsonl`。`--type creator` 官方支持。
- **不能返回**：作者原始 user_id/主页链接（只有 creator_hash 脱敏哈希 + user_nickname，深挖作者需打开 content_url 页面自取）；搜索结果实测只出现 answer/article，**未见视频类型**（知乎视频 zvideo 只能已知 URL 走 detail 直抓）；付费盐选内容全文。
- **耗时/失败/处理**：一次搜索约 **30s**（2026-07-09 实测：32s 返回 19 条，含浏览器启动）；`--crawler_max_notes_count` 是页粒度近似值（要 5 条返回 ~19 条）。失败场景：①登录态过期→重新扫码；②高频翻页可能触发验证→保持默认 sleep、单次 ≤2–3 页。
- **防封号**：CDP 真实浏览器模式默认开；`CRAWLER_MAX_SLEEP_SEC=2` 别调低；知乎对未登录/高频请求较敏感，批次间拉开间隔。
- **信息收集推荐用法**：挖用户对某方向的真实看法/痛点/避坑/成本数据；搜索返回按 voteup_count 本地排序；高赞回答所在的 question_id 值得用 detail 模式追同问题下其它回答。最终入选回答/文章默认抓点赞较高的前 10 条有用评论，失败必须写明原因。
- **已知坑/限制**：首次需扫码登录（登录态已持久化）。

### 示例（真实请求 + 返回，节选）
请求：`uv run main.py --platform zhihu --type search --keywords "AI漫剧" --crawler_max_notes_count 5`
返回（data/zhihu/jsonl/search_contents_<date>.jsonl，节选两行）：
    {"content_type":"answer","title":"ai漫剧真能赚钱吗？行内人说说？","voteup_count":...,
     "content_url":"https://www.zhihu.com/question/1990728643920561512/answer/...",
     "content_text":"（7526 字从业者全文）..."}
    {"content_type":"article","title":"AI漫剧是不是下一个风口？","content_text":"（26675 字长文）..."}

### 入选内容证据补全（在收集阶段完成）
（知乎搜索实测拿不到视频；若已知 zvideo URL 可 detail 直抓其文字描述。视频本体下载+转写场景极少，留空。）
