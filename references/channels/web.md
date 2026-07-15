## 通用 web（WebSearch / tavily / WebFetch / web-extractor）

- **推荐工具/方法**：`WebSearch`（关键词检索）、tavily（`tavily_search` / `tavily_map` / `tavily_extract`，中文内容表现好）、`WebFetch`（对单页精确提问式提取）、`web-extractor`（Readability+Tavily 合并提取干净正文）。
- **能返回**：任意公开网页的检索结果与正文（掘金/知乎专栏/公众号转载/官方文档/newsletter/Reddit）。检索结果含标题/URL/摘要/日期；提取可拿全文。
- **不能返回**：登录墙/付费墙后的内容；YouTube 视频内容（深挖走 youtube.md 渠道，字幕直取）；动态渲染很重的页面 WebFetch 可能拿不全（换 tavily_extract 或 Playwright）。
- **耗时/失败/处理**：单次搜索/提取 2–10s。失败场景：①WebFetch 对跨域重定向会返回新 URL → 用新 URL 再调一次；②反爬页面提取为空 → 换 web-extractor 或 tavily_extract（advanced 深度）；③tavily 偶发限流 → 稍等重试。
- **防封号**：无账号风险；正常调用量无需担心。
- **信息收集推荐用法**：抖音/小红书爆款常被二次转载到可检索网页，web 搜是低成本的**交叉验证与补充**；官方文档/定价页优先 `WebFetch` 精确提取具体字段；用多角度查询词避免单一视角。
- **已知坑/限制**：结果质量参差，营销软文多，只取有方法论的；注明来源日期。

### 示例（真实请求 + 返回，节选）
请求：`WebSearch(query="AI 漫剧 工作流 教程 2026")` → 命中掘金/腾讯云开发者社区长文 → `WebFetch(url, "提取该文的完整 pipeline 步骤与所用工具")`。
返回（节选）：结构化步骤清单（剧本→分镜→图生视频→剪辑→配音→发布）+ 工具名。

### 入选内容证据补全（在收集阶段完成）
（web 页嵌入的视频一般不下载；YouTube 见 youtube.md（字幕直取已可用）；其它站点视频如需下载可先试 yt-dlp——它支持上千站点。）
