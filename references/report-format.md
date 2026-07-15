# 交付物规范：报告 + runbook（人机分离）

总结者（主 agent 本人）产出**两份**，人机分离：
- `<OUT_DIR>/report.html` —— 给人审的报告（自包含单文件，浏览器直接打开）。
- `<OUT_DIR>/runbook.json` —— 给 AI 执行的方案（机器可读，命令级）。
外加过程资产 `verify/verdicts.jsonl`、`verify/glossary.jsonl`、`qa.md`、`raw/*.jsonl` 原样保留供追溯。

## 铁律（两份都适用）
- **每个结论必须带 finding id 或 verdict id（vd-xxx）引用**——无出处的结论不许出现（治"报告说得头头是道但查无实据"）。
- **可执行 > 罗列**：方案区是"**默认路径 + 切换条件**"，不是并列卡片让人做选择题。
- **代际透明**：必含时间线，标清推荐方案处于哪一代。
- **诚实**：厂商自评数字标"厂商自评"；核不动的标"未证实"；被砍的覆盖面、图片下载失败数都在附录公示（不静默遗漏）。

## report.html — 结构

单文件、内联 CSS。8 节：
1. **范围与口径**：idea 原话、maturity、约束（含 qa.md 里用户新补的）、本机环境。
2. **执行摘要**：3-5 行给默认推荐 + 一句话总纲。
3. **时间线**（核心）：本领域方法/模型/工具按发布时间排列（数据来自 glossary.jsonl），当前推荐处于哪一代一目了然。例：`Whisper(2022) → SenseVoice(2024) → FireRedASR(2025初) → FireRedASR2/Qwen3-ASR(2025) → MOSS(2026)`。
4. **各渠道景观**：每渠道密集发现（带链接+指标+代表性图片）；标注哪些是官方自评、哪些是用户口碑。
5. **方案：默认路径 + 切换条件**：
   - 一条**默认主路线**：命令级步骤（`pip install funasr` 这种，不是"安装工具"）。
   - 每条**备选**注明"**什么情况下切换过来**"（如"若中文准确率实测不达标 → 切 FireRedASR-AED"）。
6. **对比矩阵**：方案 × 维度（维度以 manifest.plan.dimensions 为基础，可补充），单元格标 win/mid/lose。
7. **推荐与下一步**：默认推荐 + 理由 + **待实测清单**（to_test，官方自评类结论在此列出"需亲手验证"）。
8. **附录：来源与调研日志**：
   - verdicts 全文（含"未证实"清单、事实/品质分类）。
   - glossary（生词卡 + 时间线依据）。
   - qa.md 问答原文（用户约束的第一手记录）。
   - 各渠道 meta + coverage.json 汇总：逐个批准关键词列命中数或失败/跳过原因；逐渠道列视频字幕/ASR成功与失败、评论条数、图片文字识别完成与失败、许可证已确认/未知数量——**不静默遗漏**。

### HTML 骨架（复制填充）
```html
<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>调研报告：{{IDEA}}</title>
<style>
 body{font:16px/1.7 -apple-system,PingFang SC,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#222}
 h1{border-bottom:3px solid #c0392b;padding-bottom:.4rem} h2{border-bottom:2px solid #eee;padding-bottom:.3rem;margin-top:2.2rem}
 table{border-collapse:collapse;width:100%;font-size:14.5px} th,td{border:1px solid #ddd;padding:.5rem;vertical-align:top} th{background:#f7f7f7}
 img{max-width:420px;border-radius:8px} .metric{color:#c0392b;font-weight:600}
 .default{border:2px solid #2a7;border-radius:10px;padding:1rem 1.2rem;margin:1rem 0;background:#f4fbf4}
 .alt{border:1px solid #e5e5e5;border-radius:10px;padding:.8rem 1.2rem;margin:.8rem 0;background:#fcfcfc}
 .when{color:#c78;font-weight:600} .win{color:#2a7;font-weight:600}.lose{color:#c0392b;font-weight:600}.mid{color:#c78}
 .timeline{font-size:15px;background:#f7f7ff;padding:.6rem 1rem;border-radius:8px}
 code{background:#f2f2f2;padding:.05rem .3rem;border-radius:3px;font-size:13.5px} .src{font-size:12.5px;color:#777}
 .note{background:#fffbe6;border-left:3px solid #e0c000;padding:.5rem .8rem;margin:.6rem 0}
</style></head><body>
<h1>调研报告：{{IDEA}}</h1>
<section id="scope"><h2>1. 范围与口径</h2>{{SCOPE}}</section>
<section id="summary"><h2>2. 执行摘要</h2>{{SUMMARY}}</section>
<section id="timeline"><h2>3. 时间线</h2><div class="timeline">{{TIMELINE}}</div></section>
<section id="landscape"><h2>4. 各渠道景观</h2>{{LANDSCAPE_WITH_IMAGES}}</section>
<section id="plans"><h2>5. 方案：默认路径 + 切换条件</h2>{{DEFAULT_PLAN_AND_FALLBACKS}}</section>
<section id="matrix"><h2>6. 对比矩阵</h2>{{MATRIX_TABLE}}</section>
<section id="reco"><h2>7. 推荐与下一步（含待实测清单）</h2>{{RECOMMENDATION}}</section>
<section id="appendix"><h2>8. 附录：来源与调研日志</h2>{{SOURCES_VERDICTS_GLOSSARY_QA_META}}</section>
</body></html>
```

### 图片规则
- URL 来源：finding 的 `media[].url`（抖音 cover、小红书 image、B站 cover 等）。
- 用 `python3 <SKILL_DIR>/scripts/fetch_assets.py --manifest <specs.json> --out <OUT_DIR>` 下载到 `<OUT_DIR>/assets/`（**绝对路径**；SKILL_DIR/OUT_DIR 按 SKILL.md 路径口径展开），HTML 用相对路径 `assets/<file>` 引用（规避图床时效戳）。
- 下载失败 → 降级文字占位（不留死图）；每图注明来源帖链接；**失败数在附录公示**。

## runbook.json — 给 AI 执行

```json
{"idea":"…","slug":"…","maturity":"refined|rough",
 "constraints":{"…":"…（含 qa.md 里用户新补的约束）"},
 "default_plan":{
   "name":"FunASR + SenseVoice-Small（本地）",
   "steps":[
     {"cmd":"ffmpeg -i <video> -ar 16000 -ac 1 out.wav","expect":"16k 单声道 wav"},
     {"cmd":"pip install funasr","expect":"…"},
     {"cmd":"<加载 SenseVoice-Small 转写 out.wav>","expect":"带标点中文文本"}
   ],
   "fallbacks":[{"when":"中文准确率实测不达标","switch_to":"alt-firered"}],
   "sources":["gh-005","vd-003"]
 },
 "alternatives":[
   {"id":"alt-firered","name":"FireRedASR-AED","when_to_use":"准确度压倒一切、可接受慢/重部署","steps":["…"],"sources":["gh-012"]}
 ],
 "timeline":[{"name":"SenseVoice-Small","released":"2024-07","supersedes":null}],
 "verified":[{"claim":"…","verdict":"confirmed","evidence":"…","finding_ids":["…"],"verdict_id":"vd-003"}],
 "unverified":["知乎从业者称风噪场景 whisper 反超讯飞——无法独立复核"],
 "to_test":[{"claim":"SenseVoice 中文自评CER 7.81%","how":"用已下载的抖音/小红书真实短视频亲手实测，官方基准是干净长音频"}],
 "open_questions":["（仅 headless 时）要自动化管线还是手动工具？→ 自动化用 default_plan，手动用 alt-…"]}
```
- 每个 step 到命令/模型名级，`expect` 写验收动作。
- `sources` 引用 finding id（如 `gh-005`）或 verdict id（如 `vd-003`），两种 id 都必须真实存在于 raw/ 或 verify/ 产物中。
- `to_test`：所有"官方自评未经独立验证"的品质结论落这，留给动手实测。
- `open_questions`：无交互模式下无法当面问用户，问题连同分支默认值写这（无论 maturity）。
