/**
 * 云函数本地冒烟测试：用内存数据库桩跑完 /jy-content 的读与写，
 * 部署前就能验逻辑，不需要控制台、不需要真实云环境。
 *
 *   node uniCloud-alipay/test/content-harness.cjs
 *
 * 覆盖：鉴权(401/请求头令牌)、空库读、本地 http 地址过滤、集序 episodeIds、
 * 分页 total、playlist 的 columnId、下架后栏目收缩、上限 300、错误码。
 */
/* 用内存数据库桩跑一遍云函数，验证部署前的逻辑 */
const assert = require('node:assert')
// 根 package.json 写了 "type": "module"，所以本文件必须是 .cjs
const path = require('node:path')

const FN = path.join(__dirname, '..', 'cloudfunctions', 'function-jy-content')
// 没配 secret.js 时用假令牌，只验流程不验真值
const TOKEN = process.env.JY_TEST_TOKEN
  || (() => {
    try { return require(`${FN}/secret.js`).ADMIN_TOKEN }
    catch (e) { return 'test-token' }
  })()

const store = {}
function match(doc, q) {
  return Object.keys(q).every((k) => {
    const want = q[k]
    if (want && typeof want === 'object' && want.$in)
      return want.$in.includes(doc[k])
    if (want && typeof want === 'object' && want.$exists)
      return (doc[k] !== undefined) === want.$exists
    return doc[k] === want
  })
}
function rows(name) { store[name] = store[name] || []; return store[name] }
function clone(x) { return JSON.parse(JSON.stringify(x)) }

function sortDocs(list, orders) {
  return list.slice().sort((a, b) => {
    for (const [field, dir] of orders) {
      const av = a[field]; const bv = b[field]
      if (av === bv)
        continue
      const less = (av === undefined) ? true : (av < bv)
      return dir === 'desc' ? (less ? 1 : -1) : (less ? -1 : 1)
    }
    return 0
  })
}

function collection(name) {
  const state = { where: null, orders: [], limit: 0, skip: 0 }
  const api = {
    where(q) { state.where = q; return api },
    orderBy(field, dir) { state.orders.push([field, dir || 'asc']); return api },
    limit(n) { state.limit = n; return api },
    skip(n) { state.skip = n; return api },
    async get() {
      let list = rows(name)
      if (state.where)
        list = list.filter(d => match(d, state.where))
      list = state.orders.length ? sortDocs(list, state.orders) : list
      if (state.skip)
        list = list.slice(state.skip)
      if (state.limit)
        list = list.slice(0, state.limit)
      return { data: clone(list) }
    },
    async count() {
      let list = rows(name)
      if (state.where)
        list = list.filter(d => match(d, state.where))
      return { total: list.length }
    },
    doc(id) {
      return {
        async get() { return { data: clone(rows(name).filter(d => d._id === id)) } },
        async set(doc) {
          const d = Object.assign({}, doc, { _id: id })
          const list = rows(name)
          const i = list.findIndex(x => x._id === id)
          if (i >= 0)
            list[i] = d; else list.push(d)
          return { updated: i >= 0 ? 1 : 0, created: i >= 0 ? 0 : 1 }
        },
        async update(patch) {
          const list = rows(name)
          const i = list.findIndex(x => x._id === id)
          if (i < 0)
            return { updated: 0 }
          list[i] = Object.assign({}, list[i], patch)
          return { updated: 1 }
        },
        async remove() {
          const list = rows(name)
          const i = list.findIndex(x => x._id === id)
          if (i >= 0)
            list.splice(i, 1)
          return { deleted: i >= 0 ? 1 : 0 }
        },
      }
    },
    async add(doc) {
      const d = Object.assign({ _id: doc._id || `auto-${Math.random().toString(36).slice(2)}` }, doc)
      rows(name).push(d)
      return { id: d._id }
    },
  }
  return api
}

global.uniCloud = {
  database: () => ({
    collection,
    command: { in_: arr => ({ $in: arr }), exists: b => ({ $exists: b }) },
    createCollection: async (n) => { rows(n); return true },
  }),
  deleteFile: async ({ fileList }) => ({ fileList }),
}

const app = require(`${FN}/index.js`)

async function call(action, params = {}, { token = '', headers = null } = {}) {
  const body = Object.assign({ action }, params)
  if (token)
    body.token = token
  const event = { body: JSON.stringify(body), isBase64Encoded: false, headers: headers || {} }
  return JSON.parse(JSON.stringify(await app.main(event)))
}

async function run() {
  const out = []
  const log = (name, res, extra) => {
    out.push(`${name}: code=${res.code} ${extra || ''} ${res.code !== 0 ? `msg=${res.message}` : ''}`)
    assert.strictEqual(res.code, 0, `${name} 失败: ${res.message}`)
    return res.data
  }

  log('ping', await call('ping'))
  log('version', await call('version'))

  // 空库时读接口应正常返回而不是报错
  const m0 = log('manifest(空库)', await call('manifest'))
  assert.strictEqual(m0.dataVersion, 0)
  assert.strictEqual(m0.counts.episodes, 0)

  // 写接口无 token 必须挡住
  const noTok = await call('initSite')
  assert.strictEqual(noTok.code, 401, `无 token 竟然放行了: ${JSON.stringify(noTok)}`)
  out.push('鉴权: 无 token -> code=401 ✓')
  const badTok = await call('initSite', {}, { token: 'wrong-wrong-wrong' })
  assert.strictEqual(badTok.code, 401)
  const headerOk = await call('initSite', {}, { headers: { 'x-admin-token': TOKEN } })
  log('initSite(header token)', headerOk, JSON.stringify(headerOk.data && headerOk.data.collections))

  log('upsertAccounts', await call('upsertAccounts', {
    items: [{ id: 'shiyuan-qingtan', slug: 'shiyuan-qingtan', name: '史苑轻谈', enabled: true, maxItems: 0, awemeCount: 310, scannedItems: 310 }],
  }, { token: TOKEN }))

  const eps = [
    { id: 'a1', awemeId: 'a1', title: '大风歌 第一集', sort: -1700000003, columnId: 'col-dfg', columnName: '大风歌', episodeNo: 1, episodeTotal: 40, duration: 620, audioUrl: 'https://x.normal.cloudstatic.cn/jiugeyinxue/a1.m4a', articleUrl: 'https://x.normal.cloudstatic.cn/jiugeyinxue/a1.txt', summary: 's1', category: '历史', enabled: true },
    { id: 'a2', awemeId: 'a2', title: '大风歌 第二集', sort: -1700000002, columnId: 'col-dfg', columnName: '大风歌', episodeNo: 2, episodeTotal: 40, duration: 540, audioUrl: 'http://127.0.0.1:8766/media/audio?aweme_id=a2', articleUrl: 'http://127.0.0.1:8766/media/article/text?aweme_id=a2', summary: 's2', category: '历史', enabled: true },
    { id: 'a3', awemeId: 'a3', title: '最新一集', sort: -1700000001, columnId: 'col-wlq', columnName: '王立群读汉武帝', episodeNo: 1, episodeTotal: 70, duration: 400, audioUrl: 'https://x.normal.cloudstatic.cn/jiugeyinxue/a3.m4a', articleUrl: 'https://x.normal.cloudstatic.cn/jiugeyinxue/a3.txt', summary: 's3', category: '历史', enabled: true },
  ]
  log('upsertEpisodes', await call('upsertEpisodes', { items: eps }, { token: TOKEN }))
  const saved = rows('jy_episodes').find(d => d._id === 'a2')
  assert.ok(!saved.audioUrl, '本地 http 地址竟然入库了')
  out.push('本地预览地址过滤: a2 无 audioUrl ✓')

  log('upsertColumns', await call('upsertColumns', {
    items: [
      { id: 'col-dfg', name: '大风歌', accountId: 'shiyuan-qingtan', accountName: '史苑轻谈', sort: 1, episodeTotal: 40, episodeIds: ['a1', 'a2'], nEpisodes: 2 },
      { id: 'col-wlq', name: '王立群读汉武帝', accountId: 'shiyuan-qingtan', accountName: '史苑轻谈', sort: 2, episodeTotal: 70, episodeIds: ['a3'], nEpisodes: 1 },
    ],
  }, { token: TOKEN }))

  const rel = log('pushRelease', await call('pushRelease', { note: '首批 3 集', episodes: 3, columns: 2 }, { token: TOKEN }))
  assert.strictEqual(rel.dataVersion, 1)
  out.push(`pushRelease: dataVersion=${rel.dataVersion} counts=${JSON.stringify(rel.counts)}`)

  const man = log('manifest(有数据)', await call('manifest'))
  assert.strictEqual(man.dataVersion, 1)
  assert.strictEqual(man.counts.episodes, 3)
  assert.strictEqual(man.counts.columns, 2)
  assert.ok(man.app.name, 'app.name 丢失')

  const cols = log('columns', await call('columns'))
  assert.strictEqual(cols.items.length, 2)
  assert.ok(!cols.items[0].episodes, '列表接口不该内联 episodes')
  assert.strictEqual(cols.items[0].nEpisodes, 2)
  out.push('columns: 列表不内联分集 ✓ nEpisodes 正确 ✓')

  const one = log('column(col-dfg)', await call('column', { id: 'col-dfg' }))
  assert.deepStrictEqual(one.item.episodes.map(e => e.awemeId), ['a1', 'a2'], '集序应按 episodeIds')
  assert.ok(one.item.episodes[0].articleUrl.includes('a1.txt'))
  out.push(`column: ${one.item.episodes.length} 集，按 episodeIds 排序 ✓`)

  const art = log('articles(分页)', await call('articles', { offset: 0, limit: 2 }))
  assert.strictEqual(art.total, 3)
  assert.strictEqual(art.items.length, 2)
  assert.strictEqual(art.items[0].id, 'a1', 'sort 升序 = 最新在前的相对顺序')
  const art2 = await call('articles', { offset: 2, limit: 2 })
  assert.strictEqual(art2.data.items[0].id, 'a3')
  out.push(`articles: total=${art.total} 翻页正确 ✓`)

  const pl = log('playlist', await call('playlist', {}))
  assert.strictEqual(pl.items.length, 3)
  assert.ok(pl.items[0].columnId, 'playlist 缺 columnId，文章页连播会失效')
  out.push(`playlist: 带 columnId=${pl.items[0].columnId} settings=${JSON.stringify(pl.settings)}`)

  const single = log('article(a1)', await call('article', { id: 'a1' }))
  assert.strictEqual(single.item.title, '大风歌 第一集')
  const batch = log('episodes', await call('episodes', { ids: 'a1,a3,ghost' }))
  assert.strictEqual(batch.items.length, 2, '不存在的 id 应被跳过')

  const stats = log('remoteStats', await call('remoteStats', {}, { token: TOKEN }))
  out.push(`remoteStats: ${JSON.stringify(stats)}`)

  assert.strictEqual(m0.settings.showAudio, false, '听文章入口默认必须关闭')
  assert.strictEqual(man.settings.showAudio, false, '听文章入口默认必须关闭')

  // 关闭状态：四条读路径一律不吐 audioUrl，正文地址必须完好
  const offArt = await call('articles', { offset: 0, limit: 3 })
  const offCol = await call('column', { id: 'col-dfg' })
  const offPl = await call('playlist', {})
  const offOne = await call('article', { id: 'a1' })
  const offBatch = await call('episodes', { ids: 'a1,a3' })
  // 库里各集的原样地址，用来比对「关音频不能动正文」
  const rawById = Object.fromEntries(rows('jy_episodes').map(d => [String(d._id), d]))
  const offAll = [
    ...offArt.data.items,
    ...offCol.data.item.episodes,
    ...offPl.data.items,
    offOne.data.item,
    ...offBatch.data.items,
  ]
  assert.ok(offAll.length >= 9, '音频开关用例覆盖到的条目太少')
  offAll.forEach((one) => {
    assert.strictEqual(one.audioUrl, '', `听文章关闭时仍吐出了 audioUrl: ${one.id || one.awemeId}`)
  })
  // playlist 协议上不带正文字段，其它接口必须和库里一致
  const keepText = [...offArt.data.items, ...offCol.data.item.episodes, offOne.data.item, ...offBatch.data.items]
  for (const one of keepText) {
    const key = String(one.id || one.awemeId)
    assert.strictEqual(one.articleUrl || '', rawById[key].articleUrl || '', `关闭音频弄丢了正文地址: ${key}`)
  }
  out.push(`听文章关闭: ${offAll.length} 条全部无 audioUrl，正文地址与库里一致 ✓`)

  log('updateSettings', await call('updateSettings', { settings: { autoplayNext: false, playMode: 'sequence' } }, { token: TOKEN }))
  const man2 = await call('manifest')
  assert.strictEqual(man2.data.settings.autoplayNext, false)
  out.push('updateSettings 生效 ✓')

  // 过审后手改 jy_meta 打开音频，再改站点名不能被抹回默认值
  log('打开听文章', await call('updateSettings', { settings: { showAudio: true } }, { token: TOKEN }))
  assert.strictEqual((await call('manifest')).data.settings.showAudio, true)
  log('只改站点名', await call('updateSettings', { app: { name: '九哥阅读' } }, { token: TOKEN }))
  const man3 = await call('manifest')
  assert.strictEqual(man3.data.app.name, '九哥阅读')
  assert.strictEqual(man3.data.settings.showAudio, true, '改站点名不该把手开的音频关回去')
  assert.strictEqual(man3.data.settings.autoplayNext, false, '改 settings 里的一个字段不该丢另一个字段')
  out.push('showAudio：默认关，手改后不被其它设置覆盖 ✓')

  // 打开后 audioUrl 必须原样回来，保证以后开音频不用再动代码
  const onArt = await call('articles', { offset: 0, limit: 3 })
  const onOne = await call('article', { id: 'a1' })
  const onCol = await call('column', { id: 'col-dfg' })
  const onPl = await call('playlist', {})
  assert.strictEqual(onOne.data.item.audioUrl, rawById.a1.audioUrl, '打开后 audioUrl 没恢复')
  for (const one of [...onArt.data.items, ...onPl.data.items]) {
    assert.strictEqual(one.audioUrl || '', rawById[String(one.id)].audioUrl || '', `打开后 audioUrl 与库里不一致: ${one.id}`)
  }
  assert.ok(onArt.data.items[0].audioUrl, '打开后 articles 仍无 audioUrl')
  assert.ok(onCol.data.item.episodes[0].audioUrl, '打开后 column 仍无 audioUrl')
  assert.ok(onPl.data.items.some(e => e.audioUrl), '打开后 playlist 仍无 audioUrl')
  assert.strictEqual(onPl.data.settings.showAudio, true, 'playlist 没回传 settings')
  out.push('打开听文章: 四条读路径 audioUrl 全部恢复 ✓')

  log('deleteEpisodes(下架)', await call('deleteEpisodes', { ids: ['a2'] }, { token: TOKEN }))
  const one2 = await call('column', { id: 'col-dfg' })
  assert.strictEqual(one2.data.item.episodes.length, 1, '下架后栏目里还引用着')
  out.push('下架: 栏目 episodeIds 同步收缩 ✓')

  log('deleteEpisodes(彻底删)', await call('deleteEpisodes', { ids: ['a2'], purgeFiles: true }, { token: TOKEN }))
  const notFound = await call('article', { id: 'a2' })
  assert.strictEqual(notFound.code, 404)
  const badAction = await call('nope')
  assert.strictEqual(badAction.code, 404)
  const missId = await call('column', {})
  assert.strictEqual(missId.code, 400)
  out.push('未知 action / 缺参数 / 不存在 id 的错误码正确 ✓')

  // ── articleText：正文的服务端兜底通道 ──
  const https = require('node:https')
  const { EventEmitter } = require('node:events')
  const realGet = https.get
  const fetched = []
  let textScenario = () => ({ status: 200, body: '# 正文\n\n正文内容' })
  https.get = function patched(urlStr, opts, cb) {
    const target = String(urlStr)
    fetched.push(target)
    const res = new EventEmitter()
    res.headers = {}
    res.resume = () => {}
    const req = new EventEmitter()
    req.destroy = err => setImmediate(() => req.emit('error', err || new Error('destroyed')))
    const want = textScenario(target)
    setImmediate(() => {
      res.statusCode = want.status
      cb(res)
      setImmediate(() => {
        const buf = Buffer.from(want.body, 'utf8')
        res.emit('data', buf)
        res.emit('end')
      })
    })
    return req
  }

  const OSS_XML = '<?xml version="1.0" encoding="UTF-8"?>\n<Error>\n  <Code>SignatureDoesNotMatch</Code>\n</Error>'
  const REAL_TXT = 'https://env-00jxu1ytdn0v.normal.cloudstatic.cn/jiugeyinxue/x/col-y/article/a9.txt'

  const badHost = await call('articleText', { url: 'https://evil.example.com/a.txt' })
  assert.strictEqual(badHost.code, 400, '白名单外的地址竟然放行了')
  const noArg = await call('articleText', {})
  assert.strictEqual(noArg.code, 400)
  const ghost = await call('articleText', { id: 'ghost' })
  assert.strictEqual(ghost.code, 404)
  out.push('articleText: 域名白名单 / 缺参数 / 不存在 id 的错误码正确 ✓')

  log('upsertEpisodes(a9)', await call('upsertEpisodes', {
    items: [{ id: 'a9', awemeId: 'a9', title: '兜底验证集', sort: -1700000009, columnId: 'col-dfg', columnName: '大风歌', episodeNo: 9, duration: 100, articleUrl: REAL_TXT, enabled: true }],
  }, { token: TOKEN }))

  // CDN 第一次就撞上被缓存的 403 错误页，换缓存键之后取到真正文
  textScenario = (url) => {
    if (!url.includes('jyrb='))
      return { status: 403, body: OSS_XML }
    return { status: 200, body: '# 换键后的正文' }
  }
  fetched.length = 0
  const retried = log('articleText(403 后换键重试)', await call('articleText', { id: 'a9' }))
  assert.ok(retried.bust === true && retried.host === 'cdn', `应记为 CDN 换键命中: ${JSON.stringify(retried)}`)
  assert.ok(retried.text.includes('换键后的正文'), `重试结果没拿到: ${retried.text}`)
  assert.strictEqual(fetched.length, 2, `应正好两次取数: ${JSON.stringify(fetched)}`)
  out.push('articleText: CDN 错误页不当正文，换缓存键二次取数成功 ✓')

  // 整个 CDN 节点都坏：换键也没用，这时必须绕到源站域名
  textScenario = (url) => {
    if (url.includes('cloudstatic.cn'))
      return { status: 403, body: OSS_XML }
    return { status: 200, body: '# 源站直取正文' }
  }
  fetched.length = 0
  const origin = log('articleText(绕开 CDN 走源站)', await call('articleText', { id: 'a9' }))
  assert.strictEqual(origin.host, 'origin', `应落到源站那条路: ${JSON.stringify(origin)}`)
  assert.ok(origin.text.includes('源站直取'), `源站结果没拿到: ${origin.text}`)
  assert.strictEqual(fetched.length, 3, `CDN 两条 + 源站一条: ${JSON.stringify(fetched)}`)
  out.push('articleText: CDN 全挂时绕开 CDN 从源站取到 ✓')

  // 四条路都不通时明确报错，让前端继续往下一条通道走
  textScenario = () => ({ status: 403, body: OSS_XML })
  const dead = await call('articleText', { id: 'a9' })
  assert.strictEqual(dead.code, 502)
  out.push(`articleText: 全失败 -> code=502 ${JSON.stringify(dead.message)}`)

  // 错误页 XML 就算混在 200 里也必须被识破
  textScenario = () => ({ status: 200, body: OSS_XML })
  const poisoned = await call('articleText', { url: REAL_TXT })
  assert.strictEqual(poisoned.code, 502, '200 的错误页竟然算成功')
  out.push('articleText: 200 但内容是 OSS XML，判失败 ✓')

  textScenario = () => ({ status: 200, body: '' })
  const empty = await call('articleText', { url: REAL_TXT })
  assert.strictEqual(empty.code, 502)
  https.get = realGet
  out.push('articleText: 空 body 判失败 ✓')

  // 大 body：面板单批 300 条的上限
  const big = await call('upsertEpisodes', { items: Array.from({ length: 301 }, (_, i) => ({ id: `x${i}`, title: 't' })) }, { token: TOKEN })
  assert.strictEqual(big.code, 400)
  out.push('301 条被拒（上限 300）✓')

  console.log(out.join('\n'))
  console.log(`\n全部通过：${out.length} 项检查`)
}

const out2 = []
run().catch((e) => {
  console.error('\n失败:', e && e.message)
  console.error(e && e.stack)
  process.exit(1)
})
