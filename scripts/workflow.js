export const meta = {
  name: 'research-anything-collect',
  description: '按渠道实搜落盘，再独立补齐视频、评论、图片与许可证证据；跨渠道综合留给 Stage 3',
  phases: [
    { title: 'Collect', detail: '每个已批准渠道 1 个 agent 实搜并落盘笔记' },
    { title: 'Evidence', detail: '逐渠道补全视频、评论、图片文字与许可证证据' },
  ],
}

// ── args 校验（fail loud，根治"落盘位置歧义"）──────────────────────────────
// 主 agent 必须传绝对路径：skillDir=本 skill 安装目录；outDir=<项目根>/docs/research/<slug>
const plan = args || {}
const { idea, slug, skillDir, outDir } = plan
if (!slug || !skillDir || !outDir) {
  throw new Error('args 必须包含 slug / skillDir / outDir（skillDir=skill 安装目录绝对路径，outDir=<项目根>/docs/research/<slug> 绝对路径）——见 SKILL.md Stage 2')
}
if (!String(skillDir).startsWith('/')) throw new Error(`skillDir 必须是绝对路径，收到：${skillDir}`)
if (!String(outDir).startsWith('/')) throw new Error(`outDir 必须是绝对路径，收到：${outDir}`)

// ── 渠道标准名白名单 + 常见别名归一；id 前缀与 references/log-format.md 前缀表一致 ──
const PREFIX = { douyin: 'dy', xiaohongshu: 'xhs', zhihu: 'zh', bilibili: 'bili', youtube: 'yt', github: 'gh', twitter: 'tw', web: 'web' }
const ALIAS = { '抖音': 'douyin', '小红书': 'xiaohongshu', 'xhs': 'xiaohongshu', '知乎': 'zhihu', 'b站': 'bilibili', 'bili': 'bilibili', 'x': 'twitter', '推特': 'twitter', '通用web': 'web', '通用 web': 'web' }
const channels = (plan.channels || []).map(ch => {
  const rawName = String(ch.name || '').trim()
  const lower = rawName.toLowerCase()
  const name = PREFIX[lower] ? lower : (ALIAS[rawName] || ALIAS[lower])
  if (!name || !PREFIX[name]) throw new Error(`未知渠道名 "${ch.name}"。合法标准名：${Object.keys(PREFIX).join(' / ')}`)
  return { ...ch, name }
})
if (!channels.length) throw new Error('channels 为空——至少要有一个已批准渠道')

const rawDir = `${outDir}/raw`

// 收集 agent 的返回只含小指针——全量笔记在盘上，绝不经返回通道（v1 截断病根在此根除）
const POINTER = {
  type: 'object', required: ['channel', 'count', 'file'],
  properties: {
    channel: { type: 'string' },
    count: { type: 'number', description: '写入的 finding 条数' },
    file: { type: 'string', description: 'findings.<渠道>.jsonl 的绝对路径' },
    headlines: { type: 'array', items: { type: 'string' }, description: '前 5 条一句话标题，供主 agent 速览' },
    failures: { type: 'array', items: { type: 'string' }, description: '失败/跳过的关键词或页，不静默遗漏；count=0 时必须非空' },
    coverage: { type: 'object', description: '证据补全统计：视频/评论/图片/许可证的完成与失败数' },
  },
}

const WRITE_PROTOCOL = (ch, keywords = []) => `
## 落盘协议（先读规范原文，再动笔）
0. 【必读】用 Read 完整读 ${skillDir}/references/log-format.md ——字段定义、note 质量标准（含 skill 作者原话与京都笔记示范）、落盘纪律全以它为准。本协议只是操作顺序提要，两者不一致时以 log-format.md 为准，并把不一致处写进返回的 failures。
你的产物文件（绝对路径，禁止自行改地址、禁止对"当前目录"做任何假设）：${rawDir}/findings.${ch}.jsonl
1. 开工先 \`mkdir -p ${rawDir} ${outDir}/artifacts\`（幂等，并发安全），随即用 Write 创建产物文件并写入第 1 行 meta 占位（type:"meta"/schema_version:2/channel:"${ch}"/slug:"${slug}"/queries:${JSON.stringify(keywords)}/started/count:0/failures:[]/skipped:[]，紧凑单行 JSON）。queries 必须逐字保留这里列出的全部计划关键词，不能删除失败或零命中的词。
2. 【关键词覆盖硬约束】计划关键词为 ${JSON.stringify(keywords)}，必须逐个真实执行。每个关键词最终必须满足二选一：①至少一条入选 finding 的 query 与该关键词完全一致；②meta.failures 或 meta.skipped 中有一条包含该关键词及具体原因（工具失败/零命中/无合格结果/明确跳过原因）。禁止漏跑、静默零命中或只搜近义词后声称完成。
3. 【边搜边落盘】每跑完一个关键词、整理好该批笔记，就用 Bash heredoc 追加（每条一行紧凑 JSON，记录内不换行）：
   cat >> ${rawDir}/findings.${ch}.jsonl <<'JSONL'
   {"type":"finding",...}
   JSONL
   绝不把全部笔记攒到最后一次性写——中途失败时，已追加的条目就是抢救成果。
4. id 从 ${PREFIX[ch]}-001 起递增；channel 恒为 "${ch}"。必填字段：id / ts / channel / tool / query / source_url / title / headline(≤40字一句话) / note(精华笔记，质量标准见 log-format.md) / metrics / content(原文全文，绝不因怕大而截断) / raw(渠道原生字段原样保留，无则空对象 {}) / capture（正文来源及视频、评论、图片、许可证处理结果，严格照 log-format.md）。可选：author / published_at / media / unknown_terms(拿不准的新名词放这，别瞎猜含义)。
5. 付费 ASR 不是搜集计划的隐含授权。调用前必须读取 ${outDir}/manifest.json 的 asr_authorization；仅 authorized=true 且不超过 max_hours/max_cost_cny 时可调用。未授权或会超上限时不得调用，需要 ASR 的 finding 必须写 capture.video.status=failed 及具体 error。
6. 收尾前再次逐项核对全部计划关键词；未产出 finding 的词必须先写入 meta.failures 或 meta.skipped。然后回填 meta（自动重算 count、写 finished、合并 failures）：
   python3 ${skillDir}/scripts/finalize_log.py --file ${rawDir}/findings.${ch}.jsonl --failures '<JSON数组，无则[]>'
   零结果不许静默：若最终 count=0，failures 必须写明原因（如"账号未配置""全部关键词无命中"），校验器会拦。
7. 【并行安全】本次是多渠道并行收集：禁止修改 ~/tools/MediaCrawler/config/ 下任何文件（SORT_TYPE / PUBLISH_TIME_TYPE / ENABLE_GET_MEIDAS 等一律用默认值完成本次收集）——配置是四个平台 agent 共享的，同时改会互相污染。受影响的能力（按时间排序/时间范围过滤/批量下媒体）降级完成并记入 failures；热度排序不受影响（默认配置即最热优先）。
8. 只返回小指针（channel/count/file/headlines[前5]/failures），不要把 findings 内容塞进返回值。`

const COLLECT_RO = [
  '你是收集 agent，职责是"忠实笔记员"：只如实搜集+做精华笔记+落盘，不做跨渠道判断、不做方案推荐、不做验证。',
  '你的笔记将被一个只读笔记、不读原帖的总结 agent 使用——笔记漏掉的信息对它就是不存在的，这就是笔记质量标准存在的原因。',
  '可用：Bash 跑 MediaCrawler CLI/yt-dlp、会话内 MCP 工具（xiaohongshu-mcp、GitHub）、WebSearch/WebFetch/tavily、twscrape、Write。',
  '每条发现必须带真实 source_url + 指标 + 一手证据；没有来源的结论不要写。',
  '计划中的每个关键词都必须真实执行并留下 finding，或逐词写入 meta.failures/meta.skipped 说明原因；禁止静默遗漏。',
  '可见浏览器若弹出登录/验证码，只提示一次并最多等待 120 秒（Bash 调用也设置对应超时）；无人响应就终止该渠道本轮操作，把当前及尚未执行的每个关键词逐项写入 failures，禁止反复弹窗或把未抓到写成成功。',
].join('\n')

const EVIDENCE_RO = [
  '你是证据补全 agent。只复查并补全已有 findings，不新增/删除候选，不做跨渠道判断或方案推荐。',
  '第一步完整读本渠道文档和 log-format.md；随后逐条检查 capture。不能只看 content 非空，因为标题或简介不等于视频正文。',
  `开始补全前先执行 mkdir -p ${outDir}/artifacts。所有字幕/ASR/OCR/评论/许可证文本产物都必须放在该目录，并在 capture.artifact 写相对 OUT_DIR 的 artifacts/... 路径。`,
  '必须执行渠道文档要求的补全：视频字幕/ASR、入选社交内容前10条有用评论、小红书图文笔记全部配图文字（视频封面不强制）、GitHub 根目录 LICENSE 文件。',
  '每项必须落成“成功”或“失败/不可用+具体原因”；禁止用“跳过”“稍后处理”蒙混。成功 artifact 必须是真实非空文本且全文已合入 finding.content，并在 capture 记录来源和产物路径。',
  '同时核对 manifest 里的全部计划关键词：每个词必须有同名 finding.query，或在 meta.failures/meta.skipped 中逐词说明原因。',
  '完成后运行带 --manifest 的 validate_log.py 校验该文件；不合格就修正，绝不把未处理状态留给总结阶段。',
].join('\n')

// Collect：先读渠道文档与落盘规范，实搜并边搜边落盘。
const collectChannel = ch => agent(
    `${COLLECT_RO}\n\n## 本次调研 idea（用户原话，仅作语境）\n${idea || '（未提供）'}\n` +
    `注意：idea 只帮你理解关键词语境（如多义词消歧），笔记取舍仍以"源作者说了什么"为准，不做与 idea 的相关性裁剪。\n\n` +
    `## 渠道：${ch.name}\n先用 Read 完整读 ${skillDir}/references/channels/${ch.name}.md，` +
    `严格按其中真实命令/工具操作，文档中所有标注 ⚠️ 的运行侧安全约束每条都必须遵守；文档里的 <SKILL_DIR> 占位符一律展开为 ${skillDir}。\n` +
    `关键词（多角度全跑）：${(ch.keywords || []).join(' / ')}。\n` +
    `要提取的信号：${(ch.signals || []).join('、')}。\n深度：约 ${ch.depth || 15} 条，按互动/热度排序取头部。\n` +
    WRITE_PROTOCOL(ch.name, ch.keywords || []),
    { label: `collect:${ch.name}`, phase: 'Collect', schema: POINTER }
  ).then(r => r || { channel: ch.name, count: 0, file: `${rawDir}/findings.${ch.name}.jsonl`, headlines: [], failures: ['agent 未返回'] })

// 四个 MediaCrawler 渠道会各开可见浏览器；顺序执行，避免同时弹窗和 CDP 启动冲突。
const browserNames = new Set(['douyin', 'xiaohongshu', 'zhihu', 'bilibili'])
const pointers = []
for (const ch of channels.filter(ch => browserNames.has(ch.name))) {
  pointers.push(await collectChannel(ch))
}
pointers.push(...await parallel(
  channels.filter(ch => !browserNames.has(ch.name)).map(ch => () => collectChannel(ch))
))

// 第二阶段独立复核，避免收集 agent 漏做视频/评论/图片/许可证处理后仍凭简介过关。
const auditChannel = ch => agent(
    `${EVIDENCE_RO}\n\n渠道：${ch.name}\n` +
    `渠道文档：${skillDir}/references/channels/${ch.name}.md\n` +
    `格式规范：${skillDir}/references/log-format.md\n` +
    `待复核文件：${rawDir}/findings.${ch.name}.jsonl\n` +
    `校验命令：python3 ${skillDir}/scripts/validate_log.py --file ${rawDir}/findings.${ch.name}.jsonl --manifest ${outDir}/manifest.json\n` +
    `图片文字识别工具：python3 ${skillDir}/scripts/ocr_images.py --out <输出前缀> <图片路径或URL...>\n` +
    `只返回小指针及 coverage 统计，不要返回 findings 全文。`,
    { label: `evidence:${ch.name}`, phase: 'Evidence', schema: POINTER }
  ).then(r => r || { channel: ch.name, count: 0, file: `${rawDir}/findings.${ch.name}.jsonl`, headlines: [], failures: ['证据补全 agent 未返回'], coverage: {} })

// MediaCrawler 的可见浏览器同秒启动会偶发冲突；四个平台顺序复核，其余渠道并行。
const audited = []
for (const ch of channels.filter(ch => browserNames.has(ch.name))) {
  audited.push(await auditChannel(ch))
}
audited.push(...await parallel(
  channels.filter(ch => !browserNames.has(ch.name)).map(ch => () => auditChannel(ch))
))

const ok = audited.filter(Boolean)
const total = ok.reduce((n, p) => n + (p.count || 0), 0)
log(`收集完成：${total} 条笔记，来自 ${ok.length} 个渠道，已落盘 ${rawDir}/`)

// 只返回指针清单给主 agent —— 全量笔记在盘上，主 agent 据此跑校验后派总结 agent（Stage 3）
return { slug, outDir, rawDir, channels: ok, total }
