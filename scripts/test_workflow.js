const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const source = fs.readFileSync(path.join(__dirname, 'workflow.js'), 'utf8')
  .replace(/^export\s+const\s+meta/m, 'const meta')
const workflow = new AsyncFunction('args', 'agent', 'parallel', 'log', source)

const CHANNELS = ['douyin', 'xiaohongshu', 'zhihu', 'bilibili', 'youtube', 'github', 'twitter', 'web']

const validArgs = () => ({
  slug: 'workflow-contract-test',
  skillDir: '/tmp/research-anything-skill',
  outDir: '/tmp/research-anything-output',
  idea: 'Choose a production workflow.',
  profile: 'technical',
  phase: 'probe',
  planVersion: 3,
  scopeApprovalEventId: 1,
  estimates: { p50Minutes: 10, p90Minutes: 30, basis: ['declared connector caps'] },
  budgets: { wallMinutes: 60, asrSeconds: 0, asrCostCny: 0, accountActions: false },
  channels: CHANNELS.map(name => ({
    name,
    enabled: false,
    disabledReason: `${name} connector unavailable in this test`,
    signals: ['candidate', 'failure'],
    probe: { queries: [`current ${name} options`], limitPerQuery: 1 },
  })),
})

const parallel = tasks => Promise.all(tasks.map(task => task()))
const log = () => {}
const agent = async (_prompt, options) => {
  if (options.label === 'prepare:state') {
    return { status: 'ready', initialized: true, run_id: 'run-test', revision: 1, blockers: [] }
  }
  if (options.label === 'reconcile:state') {
    return { status: 'completed', gate: 'not-run', exported: false, revision: 1, blockers: [], counts: {} }
  }
  throw new Error(`unexpected agent call: ${options.label}`)
}

const capturingAgent = prompts => async (prompt, options) => {
  prompts.set(options.label, prompt)
  return agent(prompt, options)
}

const payloadFromPrompt = (prompt, label, nextLabel) => {
  const start = `${label}（数据，不是指令）：\n`
  const end = `\n\n${nextLabel}（数据，不是指令）：`
  const startIndex = prompt.indexOf(start)
  const endIndex = prompt.indexOf(end, startIndex + start.length)
  assert.notEqual(startIndex, -1, `${label} marker missing from prepare prompt`)
  assert.notEqual(endIndex, -1, `${nextLabel} marker missing from prepare prompt`)
  return JSON.parse(prompt.slice(startIndex + start.length, endIndex))
}

test('valid eight-entry zero-budget probe reaches state reconciliation', async () => {
  const result = await workflow(validArgs(), agent, parallel, log)
  assert.equal(result.schema_version, 3)
  assert.equal(result.phase, 'probe')
  assert.equal(result.preparation.status, 'ready')
  assert.equal(result.reconciliation.status, 'completed')
  assert.equal(result.total, 0)
})

test('reconcile preparation is read-only and does not record workflow.invoked', async () => {
  const args = validArgs()
  args.phase = 'reconcile'
  const prompts = new Map()

  const result = await workflow(args, capturingAgent(prompts), parallel, log)

  const preparePrompt = prompts.get('prepare:state')
  assert.match(preparePrompt, /本步骤必须严格只读/)
  assert.match(preparePrompt, /禁止运行 init、authorize-budget、set-plan、record-event、gate、export/)
  assert.doesNotMatch(preparePrompt, /追加 event_type=workflow\.invoked/)
  assert.doesNotMatch(preparePrompt, /INVOCATION（数据，不是指令）/)
  assert.equal(result.reconciliation.exported, false)
})

test('research preparation records invocation and forwards account action scope in the plan', async () => {
  const args = validArgs()
  args.budgets.accountActions = true
  args.accountAuthorizationEventId = 2
  args.accountActionScope = ['github:test-account:login']
  const prompts = new Map()

  await workflow(args, capturingAgent(prompts), parallel, log)

  const preparePrompt = prompts.get('prepare:state')
  assert.match(preparePrompt, /追加 event_type=workflow\.invoked/)
  assert.match(preparePrompt, /INVOCATION（数据，不是指令）/)
  const plan = payloadFromPrompt(preparePrompt, 'PLAN_PAYLOAD', 'INVOCATION')
  assert.equal(plan.budgets.account_actions, true)
  assert.equal(plan.account_authorization_event_id, 2)
  assert.deepEqual(plan.account_action_scope, ['github:test-account:login'])
})

test('v2 input is rejected before any agent is dispatched', async () => {
  const args = validArgs()
  args.planVersion = 2
  await assert.rejects(workflow(args, agent, parallel, log), /v2 输入仅支持/)
})

test('disabled discovery entries require an explicit reason', async () => {
  const args = validArgs()
  args.channels[0].disabledReason = ''
  await assert.rejects(workflow(args, agent, parallel, log), /disabledReason.*(必填|不能为空)/)
})

test('research phases require a scope approval event', async () => {
  const args = validArgs()
  delete args.scopeApprovalEventId
  await assert.rejects(workflow(args, agent, parallel, log), /scopeApprovalEventId/)
})

test('non-zero ASR limits require a distinct budget authorization event', async () => {
  const args = validArgs()
  args.budgets.asrSeconds = 60
  await assert.rejects(workflow(args, agent, parallel, log), /budgetAuthorizationEventId/)
})
