/**
 * 运行时 in 写法兼容性测试：同一份云函数代码，四种批量查询条件下都要能取到整串分集。
 *
 *   node uniCloud-alipay/test/in-mode-harness.cjs
 *
 * 起因是线上真实故障：lib 早期写死 JQL 的 in_，支付宝云运行时不认，
 * ping 一片绿灯，合集详情页整条 500。lib.js 里 _inMode 是模块级缓存，
 * 一个进程只能代表一台运行时，所以每种模式各起一个子进程，父进程汇总输出。
 */
const path = require('node:path')
const assert = require('node:assert')
const { spawnSync } = require('node:child_process')

const FN = path.join(__dirname, '..', 'cloudfunctions', 'function-jy-content')
const MODES = {
  in: '运行时只认 in',
  in_: '运行时只认 in_（JQL 写法）',
  none: '运行时两个都没挂（逐条兜底）',
  throws: '运行时挂着 in 但一查就抛错（逐条兜底）',
}

function makeStub(mode) {
  const eps = {}
  const ids = []
  for (let i = 1; i <= 250; i++) {
    const id = `ep${i}`
    ids.push(id)
    eps[id] = {
      _id: id,
      title: `第${i}集`,
      sort: i,
      publishedAt: '2026-10-01',
      episodeNo: i,
      duration: 640,
      enabled: true,
      audioUrl: `https://x/${id}.m4a`,
      articleUrl: `https://x/${id}.txt`,
      cover: 'https://y/ep.jpg',
    }
  }
  const cols = {
    'col-dfg': {
      _id: 'col-dfg',
      name: '《王立群大风歌》',
      episodeIds: ids,
      nEpisodes: 250,
      sort: 1,
      cover: 'https://y/col.jpg',
    },
  }
  const stats = { inQueries: 0, docGets: 0 }
  // throws 模式照样把 in 挂上，重点是查询本身会炸
  const command = mode === 'in_'
    ? { in_: v => ({ $in: v }) }
    : (mode === 'none' ? {} : { in: v => ({ $in: v }) })

  function get(cond, src) {
    stats.inQueries++
    if (mode === 'throws') {
      throw new Error('该运行时不支持 in 查询')
    }
    assert.ok(Array.isArray(cond._id.$in), 'where 里必须带 $in 数组')
    assert.ok(cond._id.$in.length <= 100, '单块 in 最多 100 个 id')
    return { data: cond._id.$in.map(id => src[id]).filter(Boolean) }
  }

  return {
    stats,
    db: {
      command,
      createCollection: async () => true,
      collection(name) {
        const src = name === 'jy_episodes' ? eps : cols
        return {
          where: cond => ({ limit: () => ({ get: async () => get(cond, src) }) }),
          doc: id => ({
            get: async () => { stats.docGets++; return { data: src[id] ? [src[id]] : [] } },
            set: async () => ({ id }),
            update: async () => ({ updated: 1 }),
            remove: async () => ({ deleted: 1 }),
          }),
          orderBy() { return this },
          limit() { return this },
          skip() { return this },
          get: async () => ({ data: [] }),
          count: async () => ({ total: 0 }),
        }
      },
    },
  }
}

async function child(mode) {
  const stub = makeStub(mode)
  global.uniCloud = { database: () => stub.db, deleteFile: async () => ({ fileList: [] }) }
  const fn = require(`${FN}/index.js`)
  const post = body => fn.main({ body: JSON.stringify(body) })

  const ver = await post({ action: 'version' })
  assert.strictEqual(ver.code, 0, 'version 必须通')
  assert.ok(ver.data.inMode, `version 要回报运行时的 in 写法: ${JSON.stringify(ver.data)}`)

  const col = await post({ action: 'column', id: 'col-dfg' })
  assert.strictEqual(col.code, 0, `column 失败: ${col.message}`)
  const list = col.data.item.episodes
  assert.strictEqual(list.length, 250, `集数不全: ${list.length}`)
  assert.strictEqual(list[0].awemeId, 'ep1', '第一集不是 ep1')
  assert.strictEqual(list[249].episodeNo, 250, '集序乱了')
  assert.ok(list[0].audioUrl.indexOf('https://x/') === 0, '音频地址没映射上')

  const many = await post({ action: 'episodes', ids: 'ep7,ep8,ep7,查无此集' })
  assert.deepStrictEqual(many.data.items.map(i => i.id), ['ep7', 'ep8'], '去重/过滤不对')

  const del = await post({ action: 'deleteEpisodes', token: 'x', ids: ['ep1', 'ep2'] })
  assert.notStrictEqual(del.code, 500, `deleteEpisodes 被 in 写法拖崩: ${del.message}`)

  const miss = await post({ action: 'column', id: 'col-404' })
  assert.strictEqual(miss.code, 404, '缺栏目该 404')

  const needDoc = mode === 'none' || mode === 'throws'
  if (needDoc && col.data.fetchMode !== 'doc')
    throw new Error(`应该走逐条兜底，实际 ${col.data.fetchMode}`)
  if (!needDoc && col.data.fetchMode !== mode)
    throw new Error(`应该直接用 ${mode}，实际 ${col.data.fetchMode}`)
  console.log(mode.padEnd(7), `取数=${col.data.fetchMode.padEnd(5)}`, '250集✓', `in查询${String(stub.stats.inQueries).padStart(2)}次`, `逐条${String(stub.stats.docGets).padStart(4)}次`)
}

function parent() {
  let fails = 0
  Object.keys(MODES).forEach((mode) => {
    const r = spawnSync(process.execPath, [__filename], { env: Object.assign({}, process.env, { JY_IN_MODE: mode }), encoding: 'utf8' })
    // 摘要只看 stdout 最后一行，lib 的降级告警走 stderr，别把它当成结果
    const line = (r.stdout || '').trim().split('\n').filter(Boolean).pop() || '没输出'
    if (r.status !== 0)
      fails++
    console.log(`${(r.status === 0 ? '  ✓ ' : '  ✗ ') + line}   ${MODES[mode]}`)
    if (r.status !== 0)
      console.log((r.stderr || '').trim())
  })
  if (fails) {
    console.error(`\n${fails} 种运行时模式下云函数取不到完整分集`)
    process.exit(1)
  }
  console.log('\n四种模式全部通过：批量查询降级正常')
}

if (process.env.JY_IN_MODE) {
  child(process.env.JY_IN_MODE).catch((err) => {
    console.error(`${process.env.JY_IN_MODE} FAIL: ${err.message}`)
    process.exit(1)
  })
}
else {
  parent()
}
