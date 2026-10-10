'use strict'

/**
 * 数据层公共件：集合名、字段白名单、分页参数
 *
 * 线上字段名直接沿用小程序已经在用的驼峰命名（就是 articles.json 里的 key），
 * 这样云函数返回值和旧的静态 JSON 结构一致，前端只换数据源，不动渲染逻辑。
 */

const COLLECTIONS = {
  accounts: 'jy_accounts',
  columns: 'jy_columns',
  episodes: 'jy_episodes',
  meta: 'jy_meta',
  releases: 'jy_releases',
}

const META_ID = 'site'

/** 正文不进库，只存文件 URL；库里只放列表页要用的元数据 */
const EPISODE_FIELDS = [
  'id',
  'awemeId',
  'title',
  'summary',
  'category',
  'cover',
  'publishedAt',
  'articleUrl',
  'audioUrl',
  'enabled',
  'sort',
  'accountId',
  'accountName',
  'columnId',
  'columnName',
  'episodeNo',
  'episodeTotal',
  'sourceUrl',
  'duration',
  'wordCount',
]

const EPISODE_EXTRA_FIELDS = [
  'audioFileId',
  'articleFileId',
  'syncedAt',
  'status',
  'channel',
]

const COLUMN_FIELDS = [
  'id',
  'name',
  'accountId',
  'accountName',
  'cover',
  'sort',
  'episodeTotal',
  'nEpisodes',
]

const ACCOUNT_FIELDS = [
  'id',
  'slug',
  'name',
  'style',
  'enabled',
  'maxItems',
  'awemeCount',
  'scannedItems',
  'updatedAt',
]

const DEFAULT_APP = {
  name: '九哥隐学',
  author: '九哥',
  cover: '',
  description: '九哥原创文章阅读',
}
/**
 * showAudio 是「听文章」入口的总开关，默认关闭。
 * 审核期线上不露任何音频入口，过审后手动把 jy_meta 文档的
 * settings.showAudio 改成 true 即可打开，不用重新发版小程序。
 */
const DEFAULT_SETTINGS = { autoplayNext: true, playMode: 'sequence', showAudio: false }

/** jy_meta 里的 settings 可能缺字段（老数据/手改漏写），一律和默认值合并后再出去 */
function readSettings(meta) {
  return Object.assign({}, DEFAULT_SETTINGS, (meta && meta.settings) || {})
}

function db() {
  return uniCloud.database()
}

function databaseCmd() {
  return uniCloud.database().command
}

/**
 * 按 _id 批量取文档。
 *
 * 支付宝云运行时 command 上到底挂的是 in 还是 in_（JQL 写法）各家不一致，
 * 这里先探测再缓存，探测不到就退回逐条 doc(id).get()，保证线上一绝不会
 * 因为 "in_ is not a function" 整个接口挂掉。
 */
let _inMode = null

function inMode() {
  if (_inMode === null) {
    const cmd = databaseCmd() || {}
    if (typeof cmd.in === 'function') {
      _inMode = 'in'
    }
    else if (typeof cmd.in_ === 'function') {
      _inMode = 'in_'
    }
    else {
      _inMode = 'none'
    }
  }
  return _inMode
}

function idInCmd(list) {
  const mode = inMode()
  if (mode === 'none') {
    return null
  }
  return databaseCmd()[mode](list)
}

const IN_CHUNK = 100
const DOC_CHUNK = 8

async function fetchRowsByIn(collName, ids) {
  const rows = []
  for (let i = 0; i < ids.length; i += IN_CHUNK) {
    const chunk = ids.slice(i, i + IN_CHUNK)
    const res = await db().collection(collName).where({ _id: idInCmd(chunk) }).limit(chunk.length).get();
    (res.data || []).forEach(doc => rows.push(doc))
  }
  return rows
}

/** 兜底路径：小并发逐条取，单集合最多几百个 id，不会顶到函数超时 */
async function fetchRowsByDoc(collName, ids) {
  const rows = []
  for (let i = 0; i < ids.length; i += DOC_CHUNK) {
    const group = ids.slice(i, i + DOC_CHUNK)
    const res = await Promise.all(group.map(id =>
      db().collection(collName).doc(id).get().catch(() => null)))
    res.forEach((one) => {
      const doc = one && one.data && one.data[0]
      if (doc) {
        rows.push(doc)
      }
    })
  }
  return rows
}

/** 返回 { rows, fetchMode }，fetchMode 一路带到响应里，方便线上直接看走的是哪条路 */
async function docsByIds(collName, rawIds) {
  const ids = []
  const seen = {};
  (rawIds || []).forEach((one) => {
    const s = String(one || '')
    if (s && !seen[s]) {
      seen[s] = 1
      ids.push(s)
    }
  })
  if (!ids.length) {
    return { rows: [], fetchMode: 'empty' }
  }
  if (inMode() !== 'none') {
    try {
      return { rows: await fetchRowsByIn(collName, ids), fetchMode: inMode() }
    }
    catch (err) {
      console.warn(`[${collName}] in 查询失败，改逐条取:`, err && err.message)
    }
  }
  else {
    console.warn(`[${collName}] 运行时没有 in 命令，用逐条取`)
  }
  return { rows: await fetchRowsByDoc(collName, ids), fetchMode: 'doc' }
}

/** 库里缺哪条就跳过哪条，顺序永远按传进来的 ids */
function rowsToMap(rows) {
  const byId = {};
  (rows || []).forEach((row) => {
    byId[String(row._id || row.id)] = row
  })
  return byId
}

/** 以 _id 幂等覆盖整条文档 */
async function putDoc(name, id, data) {
  const clean = Object.assign({}, data)
  delete clean._id
  await db().collection(name).doc(id).set(clean)
  return id
}

async function putMany(name, rows) {
  const ids = rows.map(row => row.id || row._id || row.slug)
  for (const row of rows) {
    await putDoc(name, row.id || row._id || row.slug, row)
  }
  return ids.length
}

/** 去掉 undefined，避免 set 时把线上已有字段抹成 null */
function compact(obj) {
  const out = {}
  Object.keys(obj || {}).forEach((key) => {
    if (obj[key] !== undefined && obj[key] !== null) {
      out[key] = obj[key]
    }
  })
  return out
}

function pick(src, fields) {
  const out = {}
  fields.forEach((key) => {
    if (src && src[key] !== undefined) {
      out[key] = src[key]
    }
  })
  return out
}

function numOr(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/** 分页参数收口：最多 500 条一页，防止一次拉爆响应体 */
function pageParams(query, body, defaultLimit) {
  const limit = Math.min(numOr(body.limit, numOr(query.limit, defaultLimit || 200)), 500)
  const offset = Math.max(numOr(body.offset, numOr(query.offset, 0)), 0)
  return { limit, offset }
}

async function getMeta() {
  const res = await db().collection(COLLECTIONS.meta).doc(META_ID).get()
  return (res.data && res.data[0]) || null
}

async function ensureMeta() {
  let meta = await getMeta()
  if (!meta) {
    const doc = {
      _id: META_ID,
      app: DEFAULT_APP,
      settings: DEFAULT_SETTINGS,
      dataVersion: 0,
      updatedAt: Date.now(),
      counts: { episodes: 0, columns: 0 },
    }
    await db().collection(COLLECTIONS.meta).add(doc)
    meta = doc
  }
  return meta
}

async function countOf(name) {
  try {
    const res = await db().collection(name).where({ _id: uniCloud.database().command.exists(true) }).count()
    return numOr(res && res.total, 0)
  }
  catch (err) {
    return -1
  }
}

module.exports = {
  COLLECTIONS,
  META_ID,
  EPISODE_FIELDS,
  EPISODE_EXTRA_FIELDS,
  COLUMN_FIELDS,
  ACCOUNT_FIELDS,
  DEFAULT_APP,
  DEFAULT_SETTINGS,
  db,
  databaseCmd,
  inMode,
  docsByIds,
  rowsToMap,
  putDoc,
  putMany,
  compact,
  pick,
  numOr,
  pageParams,
  getMeta,
  ensureMeta,
  countOf,
  readSettings,
}
