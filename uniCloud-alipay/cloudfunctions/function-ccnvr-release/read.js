'use strict'

/**
 * 读接口：官网下载页与设备更新源读取，全部公开、不需要令牌。
 * 唯一例外是「看未发布草稿」——list 带上 published=all/false 时要过管理令牌，
 * 避免半成品版本提前泄露到公网。
 *
 * 返回结构对齐官网 website/src/constant/release.ts 的 ReleaseManifest，
 * 以及 ccnvr_server internal/integration/ota 的 Index 契约。
 */

const {
  COLLECTIONS,
  DEFAULT_CHANNEL,
  CHANNELS,
  db,
  numOr,
  pageParams,
  platformLabel,
  webKeyOf,
} = require('./lib.js')

const admin = require('./admin.js')

const PRODUCT = 'ccnvr'
const WEB_KEYS = ['windows', 'linux', 'docker']

function normChannel(value) {
  const one = String(value || '').trim()
  return CHANNELS.indexOf(one) >= 0 ? one : DEFAULT_CHANNEL
}

/** 给平台补上中英文标签；写接口一般已经填好，这里兜底老数据 */
function withLabels(platforms) {
  const out = {}
  Object.keys(platforms || {}).forEach((id) => {
    const one = Object.assign({}, platforms[id])
    if (!one.label) {
      one.label = platformLabel(id)
    }
    out[id] = one
  })
  return out
}

function publicItem(doc) {
  const one = Object.assign({}, doc)
  one.platforms = withLabels(one.platforms)
  return one
}

/** 每次都拿一个新的查询句柄，避免 where/orderBy 的状态在两次查询间串味 */
function versionQuery(where) {
  const coll = db().collection(COLLECTIONS.versions)
  return Object.keys(where || {}).length ? coll.where(where) : coll
}

/** 只认 published=true 的文档对外可见 */
function publishedWhere(channel) {
  const where = { published: true }
  if (channel) {
    where.channel = channel
  }
  return where
}

/** published 参数归一：undefined=默认只看已发布；'all'=全部；true/false=精确过滤 */
function parsePublished(value) {
  if (value === undefined || value === null || value === '') {
    return undefined
  }
  const s = String(value).trim().toLowerCase()
  if (s === 'all') {
    return 'all'
  }
  if (s === 'true' || s === '1') {
    return true
  }
  if (s === 'false' || s === '0' || s === 'draft') {
    return false
  }
  return undefined
}

/** 一条更新日志 → 官网要的两行文本数组（zh 用中文标签，en 用英文标签） */
function changelogOf(docs) {
  return (docs || []).map((doc) => {
    const zh = []
    const en = []
    ;(doc.changelog || []).forEach((entry) => {
      const text = entry.text || {}
      const tzh = typeof text === 'string' ? text : text.zh
      const ten = typeof text === 'string' ? '' : text.en
      const tagZh = entry.tag || entry.tagEn || ''
      const tagEn = entry.tagEn || entry.tag || ''
      if (tzh) {
        zh.push(`${tagZh ? `[${tagZh}] ` : ''}${tzh}`)
      }
      if (ten) {
        en.push(`${tagEn ? `[${tagEn}] ` : ''}${ten}`)
      }
    })
    return { version: String(doc.version || ''), notes: { zh, en } }
  }).filter(one => one.notes.zh.length || one.notes.en.length)
}

/** list：分页列出发布记录，官网后台与设备端都用得上 */
async function list(body = {}, query = {}, headers = {}) {
  const page = pageParams(query, body, 50)
  const channel = String(body.channel || query.channel || '').trim()
  const want = parsePublished(body.published !== undefined ? body.published : query.published)
  let published = true
  if (want === 'all' || want === false) {
    const denied = admin.requireAdmin(body, query, headers || {})
    if (denied) {
      return denied
    }
    published = want === 'all' ? 'all' : false
  }
  const where = {}
  if (channel) {
    where.channel = channel
  }
  if (published !== 'all') {
    where.published = published
  }
  const res = await versionQuery(where).orderBy('releasedAt', 'desc').skip(page.offset).limit(page.limit).get()
  const countRes = await versionQuery(where).count()
  return {
    items: (res.data || []).map(publicItem),
    total: numOr(countRes && countRes.total, 0),
    limit: page.limit,
    offset: page.offset,
    channel: channel || null,
  }
}

/** latest：某通道最新一条已发布版本；带 platform 时要求该版本含此平台产物 */
async function latest(body = {}, query = {}) {
  const channel = normChannel(body.channel || query.channel || DEFAULT_CHANNEL)
  const platform = String(body.platform || query.platform || '').trim()
  const res = await versionQuery(publishedWhere(channel)).orderBy('releasedAt', 'desc').limit(50).get()
  let hit = null
  for (const doc of (res.data || [])) {
    if (platform && !(doc.platforms && doc.platforms[platform])) {
      continue
    }
    hit = doc
    break
  }
  return { item: hit ? publicItem(hit) : null, channel, platform: platform || null }
}

/** manifest：官网下载页直接消费的形状（ReleaseManifest） */
async function manifest(body = {}, query = {}) {
  const channel = normChannel(body.channel || query.channel || DEFAULT_CHANNEL)
  const limit = Math.min(numOr(body.limit !== undefined ? body.limit : query.limit, 30), 200)
  const res = await versionQuery(publishedWhere(channel)).orderBy('releasedAt', 'desc').limit(limit).get()
  const docs = res.data || []
  const latestVersion = docs.length ? String(docs[0].version || '0.0.0') : '0.0.0'

  const platforms = {}
  WEB_KEYS.forEach((key) => {
    platforms[key] = { version: '', url: '', label: { zh: key, en: key } }
  })
  const filled = {}
  // 版本按时间倒序，每个官网平台取「最近一个带该产物的版本」
  docs.forEach((doc) => {
    Object.keys(doc.platforms || {}).forEach((id) => {
      const web = webKeyOf(id)
      if (filled[web] || !platforms[web]) {
        return
      }
      const one = doc.platforms[id] || {}
      const url = (one.installer && one.installer.url)
        || (one.upgrade && one.upgrade.url)
        || (one.installer && one.installer.image)
        || ''
      platforms[web] = {
        version: String(doc.version || ''),
        url: String(url || ''),
        label: one.label || platformLabel(id),
      }
      filled[web] = true
    })
  })

  return {
    product: PRODUCT,
    channel,
    latestVersion,
    platforms,
    changelog: changelogOf(docs),
  }
}

/** changelog：只吐更新日志，给官网做单独的「更新历史」区块 */
async function changelog(body = {}, query = {}) {
  const channel = normChannel(body.channel || query.channel || DEFAULT_CHANNEL)
  const limit = Math.min(numOr(body.limit !== undefined ? body.limit : query.limit, 30), 200)
  const res = await versionQuery(publishedWhere(channel)).orderBy('releasedAt', 'desc').limit(limit).get()
  return { channel, entries: changelogOf(res.data || []) }
}

/** 一条文档 + 平台 → ccnvr_server 的 Index（更新源索引，released_at 用秒） */
function indexOf(doc, channel, platform) {
  const up = (doc.platforms[platform] || {}).upgrade || {}
  const version = String(doc.version || '')
  const notes = (doc.notes && (doc.notes.zh || doc.notes.en))
    || (doc.summary && (doc.summary.zh || doc.summary.en))
    || ''
  return {
    product: PRODUCT,
    channel,
    platform,
    latest_version: version,
    min_version: String(up.minVersion || ''),
    schema_version: numOr(up.schemaVersion, 0),
    released_at: Math.floor(numOr(doc.releasedAt, 0) / 1000),
    mandatory: up.mandatory === true,
    notes: String(notes || ''),
    package_url: String(up.url || ''),
    package_name: String(up.fileName || `${platform}_${version}.zip`),
    package_size: numOr(up.size, 0),
    package_sha256: String(up.sha256 || ''),
  }
}

/** index：单个通道 + 平台的更新源索引 */
async function index(body = {}, query = {}) {
  const channel = normChannel(body.channel || query.channel || DEFAULT_CHANNEL)
  const platform = String(body.platform || query.platform || '').trim()
  if (!platform) {
    return { code: 400, message: '缺少参数 platform' }
  }
  const res = await versionQuery(publishedWhere(channel)).orderBy('releasedAt', 'desc').limit(100).get()
  const doc = (res.data || []).find(one => one.platforms && one.platforms[platform] && one.platforms[platform].upgrade)
  if (!doc) {
    return { code: 404, message: `没有找到 ${channel}/${platform} 的升级包` }
  }
  return indexOf(doc, channel, platform)
}

/** indexes：一个通道下所有平台的索引集合，便于一次性同步到静态更新源 */
async function indexes(body = {}, query = {}) {
  const channel = normChannel(body.channel || query.channel || DEFAULT_CHANNEL)
  const res = await versionQuery(publishedWhere(channel)).orderBy('releasedAt', 'desc').limit(100).get()
  const out = {}
  ;(res.data || []).forEach((doc) => {
    Object.keys(doc.platforms || {}).forEach((id) => {
      if (out[id]) {
        return
      }
      if (doc.platforms[id] && doc.platforms[id].upgrade) {
        out[id] = indexOf(doc, channel, id)
      }
    })
  })
  return { channel, indexes: out }
}

module.exports = { list, latest, manifest, changelog, index, indexes }
