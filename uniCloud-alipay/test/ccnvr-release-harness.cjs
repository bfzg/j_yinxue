/**
 * 云函数本地冒烟测试：用内存数据库桩跑完 /ccnvr-release 的读与写，
 * 部署前就能验逻辑，不需要控制台、不需要真实云环境。
 *
 *   node uniCloud-alipay/test/ccnvr-release-harness.cjs
 *
 * 覆盖：鉴权(401/请求头令牌)、空库读、部分更新深合并、官网 manifest 形状、
 * 更新源 Index 契约(秒级 released_at / sha256)、草稿隔离、分页、错误码。
 */
/* 用内存数据库桩跑一遍云函数，验证部署前的逻辑 */
const assert = require('node:assert')
const path = require('node:path')

const FN = path.join(__dirname, '..', 'cloudfunctions', 'function-ccnvr-release')
// 没配 secret.js 时用假令牌，只验流程不验真值
const TOKEN = process.env.CCNVR_TEST_TOKEN
  || (() => {
    try { return require(`${FN}/secret.js`).ADMIN_TOKEN }
    catch (e) { return 'test-token' }
  })()
// 本函数目录默认不带 secret.js，靠环境变量注入，等价于线上控制台配 ADMIN_TOKEN
if (!process.env.ADMIN_TOKEN)
  process.env.ADMIN_TOKEN = TOKEN

const store = {}
function match(doc, q) {
  return Object.keys(q).every((k) => {
    const want = q[k]
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
    createCollection: async (n) => { rows(n); return true },
  }),
}

const app = require(`${FN}/index.js`)

async function call(action, params = {}, { token = '', headers = null } = {}) {
  const body = Object.assign({ action }, params)
  if (token)
    body.token = token
  const event = { body: JSON.stringify(body), isBase64Encoded: false, headers: headers || {} }
  return JSON.parse(JSON.stringify(await app.main(event)))
}

const WIN_UP = { url: 'https://dl.example.com/stable/windows-amd64_1.0.13.zip', fileName: 'windows-amd64_1.0.13.zip', size: 12345678, sha256: 'a'.repeat(64), minVersion: '1.0.3', schemaVersion: 1, mandatory: false, restartRequired: true }
const WIN_IN = { url: 'https://dl.example.com/installer/ccnvr-1.0.13-win-x64.zip', fileName: 'ccnvr-1.0.13-win-x64.zip', size: 60123456 }
const LIN_UP = { url: 'https://dl.example.com/stable/linux-amd64_1.0.13.zip', fileName: 'linux-amd64_1.0.13.zip', size: 11223344, sha256: 'b'.repeat(64), minVersion: '1.0.3' }

async function run() {
  const out = []
  const log = (name, res, extra) => {
    out.push(`${name}: code=${res.code} ${extra || ''} ${res.code !== 0 ? `msg=${res.message}` : ''}`)
    assert.strictEqual(res.code, 0, `${name} 失败: ${res.message}`)
    return res.data
  }

  log('ping', await call('ping'))
  log('version', await call('version'))

  // 空库读接口应正常返回而不是报错
  const empty = log('list(空库)', await call('list'))
  assert.strictEqual(empty.total, 0)
  assert.deepStrictEqual(empty.items, [])
  const emptyMan = log('manifest(空库)', await call('manifest'))
  assert.strictEqual(emptyMan.latestVersion, '0.0.0')
  assert.strictEqual(emptyMan.changelog.length, 0)

  // 写接口无 token 必须挡住
  const noTok = await call('upsertVersion', { item: { version: '1.0.13' } })
  assert.strictEqual(noTok.code, 401, `无 token 竟然放行了: ${JSON.stringify(noTok)}`)
  const badTok = await call('initDb', {}, { token: 'wrong-wrong-wrong' })
  assert.strictEqual(badTok.code, 401)
  out.push('鉴权: 无/错 token -> code=401 ✓')

  log('initDb(header token)', await call('initDb', {}, { headers: { 'x-admin-token': TOKEN } }))

  // 先只推 windows 平台（安装包 + 升级包）
  log('upsertVersion(v1.0.13/windows)', await call('upsertVersion', {
    item: {
      version: '1.0.13',
      channel: 'stable',
      published: true,
      summary: { zh: '支持多路解码优化', en: 'Faster decoding' },
      notes: { zh: '## 1.0.13\n稳定性提升', en: 'Stability' },
      changelog: [
        { type: 'new', tag: '新增', tagEn: 'New', text: { zh: '支持 ONVIF 接入', en: 'ONVIF support' } },
        { type: 'improve', tag: '优化', tagEn: 'Improved', text: { zh: '多路解码性能', en: 'Decoding perf' } },
      ],
      platforms: {
        'windows-amd64': { installer: WIN_IN, upgrade: WIN_UP },
      },
    },
  }, { token: TOKEN }))

  // 再只推 linux 平台：深合并不能把 windows 抹掉
  log('upsertVersion(v1.0.13/linux 部分更新)', await call('upsertVersion', {
    item: {
      version: '1.0.13',
      platforms: { 'linux-amd64': { upgrade: LIN_UP } },
    },
  }, { token: TOKEN }))

  const saved = rows('ccnvr_versions').find(d => d._id === 'v1.0.13')
  assert.ok(saved, 'v1.0.13 没写进去')
  assert.ok(saved.platforms['windows-amd64'].upgrade.sha256 === WIN_UP.sha256, 'windows 升级包被抹掉了')
  assert.ok(saved.platforms['linux-amd64'].upgrade.sha256 === LIN_UP.sha256, 'linux 升级包没写进去')
  assert.strictEqual(saved.platforms['windows-amd64'].label.zh, 'Windows', '平台标签没自动补')
  assert.strictEqual(saved.changelog.length, 2, 'changelog 部分更新后被抹掉了')
  assert.strictEqual(saved.summary.zh, '支持多路解码优化', 'summary 被部分更新抹掉了')
  assert.strictEqual(saved.channel, 'stable')
  assert.strictEqual(saved.published, true)
  assert.ok(saved.releasedAt > 0 && saved.createdAt > 0 && saved.updatedAt > 0, '时间戳没自动补')
  out.push('部分更新深合并: 后推 linux 不抹 windows / changelog / summary ✓')

  // 官网形状
  const man = log('manifest', await call('manifest'))
  assert.strictEqual(man.latestVersion, '1.0.13')
  assert.strictEqual(man.platforms.windows.url, WIN_IN.url, 'windows 下载地址应取安装包')
  assert.strictEqual(man.platforms.linux.url, LIN_UP.url)
  assert.strictEqual(man.platforms.docker.url, '', '没有 docker 产物时应为空')
  assert.strictEqual(man.changelog[0].version, '1.0.13')
  assert.deepStrictEqual(man.changelog[0].notes.zh, ['[新增] 支持 ONVIF 接入', '[优化] 多路解码性能'])
  assert.deepStrictEqual(man.changelog[0].notes.en, ['[New] ONVIF support', '[Improved] Decoding perf'])
  out.push(`manifest: windows=${man.platforms.windows.url} changelog=${JSON.stringify(man.changelog[0].notes.zh)}`)

  // 更新源索引（ccnvr_server Index 契约）
  const idx = log('index(windows-amd64)', await call('index', { platform: 'windows-amd64' }))
  assert.strictEqual(idx.product, 'ccnvr')
  assert.strictEqual(idx.latest_version, '1.0.13')
  assert.strictEqual(idx.package_sha256, WIN_UP.sha256)
  assert.strictEqual(idx.package_size, WIN_UP.size)
  assert.strictEqual(idx.min_version, '1.0.3')
  assert.strictEqual(idx.package_name, 'windows-amd64_1.0.13.zip')
  assert.strictEqual(Math.floor(saved.releasedAt / 1000), idx.released_at, 'released_at 必须是秒')
  out.push(`index: sha256=${idx.package_sha256.slice(0, 12)}… released_at=${idx.released_at}(秒) mandatory=${idx.mandatory}`)

  const idxs = log('indexes', await call('indexes'))
  assert.ok(idxs.indexes['windows-amd64'] && idxs.indexes['linux-amd64'])
  assert.ok(!idxs.indexes['linux-arm64'], '没有升级包的平台不该进 indexes')
  out.push(`indexes: ${Object.keys(idxs.indexes).join(',')}`)

  const missPlat = await call('index', {})
  assert.strictEqual(missPlat.code, 400, 'index 缺 platform 应 400')
  const noPkg = await call('index', { platform: 'linux-arm64' })
  assert.strictEqual(noPkg.code, 404, '没有升级包应 404')
  out.push('index: 缺参/无包的错误码正确 ✓')

  // 草稿隔离：published=false 默认读不到，管理令牌才看得到
  log('upsertVersion(v1.0.14 草稿)', await call('upsertVersion', {
    item: { version: '1.0.14', published: false, releasedAt: Date.now() + 1000, summary: { zh: '未发布' } },
  }, { token: TOKEN }))

  const pubList = log('list(默认只看已发布)', await call('list'))
  assert.strictEqual(pubList.total, 1, `草稿竟然出现在公开列表里: ${JSON.stringify(pubList.items.map(i => i.version))}`)
  const draftNoTok = await call('list', { published: 'all' })
  assert.strictEqual(draftNoTok.code, 401, '看全部草稿竟然不需要令牌')
  const allList = log('list(all, 带令牌)', await call('list', { published: 'all' }, { token: TOKEN }))
  assert.strictEqual(allList.total, 2)
  const man2 = await call('manifest')
  assert.strictEqual(man2.data.latestVersion, '1.0.13', '草稿不该成为最新版本')
  out.push('草稿隔离: 公网读不到，令牌可见 ✓')

  // 分页
  const page = log('list(limit=1)', await call('list', { limit: 1, offset: 0 }, { token: TOKEN }))
  assert.strictEqual(page.items.length, 1)
  assert.strictEqual(page.total, 1)
  assert.strictEqual(page.limit, 1)
  out.push(`分页: limit=1 -> ${page.items.length} 条 / total=${page.total} ✓`)

  // changelog 单读
  const clog = log('changelog', await call('changelog'))
  assert.strictEqual(clog.entries.length, 1)
  out.push(`changelog: ${clog.entries.length} 个版本 ✓`)

  // 完全替换模式：勾选后按表单整体覆盖，能删掉平台、清掉未提交的字段
  log('upsertVersion(v1.0.13 完全替换)', await call('upsertVersion', {
    mode: 'replace',
    item: {
      version: '1.0.13',
      channel: 'stable',
      published: true,
      summary: { zh: '仅保留 windows', en: 'windows only' },
      platforms: { 'windows-amd64': { installer: WIN_IN, upgrade: WIN_UP } },
    },
  }, { token: TOKEN }))
  const replaced = rows('ccnvr_versions').find(d => d._id === 'v1.0.13')
  assert.ok(!replaced.platforms['linux-amd64'], 'replace 模式应删掉 linux 平台')
  assert.ok(replaced.platforms['windows-amd64'], 'replace 模式应保留表单里的平台')
  assert.strictEqual((replaced.changelog || []).length, 0, 'replace 模式应清掉未提交的 changelog')
  assert.ok(replaced.createdAt > 0, 'replace 模式仍要保留 createdAt')
  out.push('完全替换模式: 删平台 / 清 changelog / 保住 createdAt ✓')

  // 删除 + 错误码
  log('deleteVersion(v1.0.14)', await call('deleteVersion', { version: '1.0.14' }, { token: TOKEN }))
  assert.ok(!rows('ccnvr_versions').some(d => d._id === 'v1.0.14'), '删除没生效')
  const badAction = await call('nope')
  assert.strictEqual(badAction.code, 404)
  const noItem = await call('upsertVersion', {}, { token: TOKEN })
  assert.strictEqual(noItem.code, 400)
  out.push('deleteVersion / 未知 action / 缺 item 的错误码正确 ✓')

  console.log(out.join('\n'))
  console.log(`\n全部通过：${out.length} 项检查`)
}

run().catch((e) => {
  console.error('\n失败:', e && e.message)
  console.error(e && e.stack)
  process.exit(1)
})
