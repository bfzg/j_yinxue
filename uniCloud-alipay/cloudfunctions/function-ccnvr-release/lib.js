'use strict'

/**
 * 数据层公共件：集合名、平台表、版本号比较、字段规范化。
 *
 * CCNVR 官网发布后台：一条文档 = 一个版本（_id = "v" + 版本号），
 * 平台下再分「安装包 installer」与「升级包 upgrade」两类产物：
 *   installer 首次部署、全量、无签名链，官网下载页指向它；
 *   upgrade   设备端 OTA 增量、签名 zip，字段对齐 ccnvr_server 的 Index 契约。
 * 集合名统一 ccnvr_ 前缀，和本服务空间里的 jy_* 项目区分开。
 */

const COLLECTIONS = { versions: 'ccnvr_versions' }

/**
 * 平台表：platformId 对齐 ccnvr_server OTA 命名（windows-amd64 / linux-amd64 / linux-arm64），
 * web 是官网下载页用的 key（windows / linux / docker）。
 */
const PLATFORMS = {
  'windows-amd64': { label: { zh: 'Windows', en: 'Windows' }, web: 'windows' },
  'linux-amd64': { label: { zh: 'Linux', en: 'Linux' }, web: 'linux' },
  'linux-arm64': { label: { zh: 'Linux (ARM64)', en: 'Linux (ARM64)' }, web: 'linux' },
  docker: { label: { zh: 'Docker', en: 'Docker' }, web: 'docker' },
}

const DEFAULT_CHANNEL = 'stable'
const CHANNELS = ['stable', 'beta']

/** 升级说明的分类标签在官网下载页要转成圆点颜色，这里给一份「中文/英文关键词 → type」的兜底 */
const TYPE_HINTS = [
  ['new', ['新增', 'new', 'add', 'feature']],
  ['fix', ['修复', 'fix', 'bug']],
  ['improve', ['优化', 'improve', 'perf', 'enhance']],
]

function platformLabel(id) {
  const one = PLATFORMS[id]
  return (one && one.label) || { zh: String(id || ''), en: String(id || '') }
}

/** platformId → 官网下载页的 key；未知平台按前缀猜，猜不出就用原值 */
function webKeyOf(id) {
  const key = String(id || '')
  if (PLATFORMS[key]) {
    return PLATFORMS[key].web
  }
  if (/^windows/i.test(key)) {
    return 'windows'
  }
  if (/^linux/i.test(key)) {
    return 'linux'
  }
  if (key === 'docker') {
    return 'docker'
  }
  return key
}

/** 没写 type 时按 tag 里的关键词猜一个，官网配色不至于全灰 */
function inferType(tag) {
  const low = String(tag || '').toLowerCase()
  for (const pair of TYPE_HINTS) {
    if (pair[1].some(word => low.indexOf(word) >= 0)) {
      return pair[0]
    }
  }
  return 'other'
}

function db() {
  return uniCloud.database()
}

function databaseCmd() {
  return uniCloud.database().command
}

function numOr(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function toBool(value, fallback) {
  if (value === true || value === 'true') {
    return true
  }
  if (value === false || value === 'false') {
    return false
  }
  return fallback
}

/** 去掉 undefined / null，避免 set 时把线上已有字段抹空 */
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

/** 分页参数收口：最多 500 条一页 */
function pageParams(query, body, defaultLimit) {
  const limit = Math.min(numOr(body && body.limit, numOr(query && query.limit, defaultLimit || 100)), 500)
  const offset = Math.max(numOr(body && body.offset, numOr(query && query.offset, 0)), 0)
  return { limit, offset }
}

/** 以 _id 幂等覆盖整条文档 */
async function putDoc(name, id, data) {
  const clean = Object.assign({}, data)
  delete clean._id
  await db().collection(name).doc(id).set(clean)
  return id
}

/** 单条读取，集合/文档不存在时返回 null 而不是抛错 */
async function getDoc(name, id) {
  try {
    const res = await db().collection(name).doc(id).get()
    return (res.data && res.data[0]) || null
  }
  catch (err) {
    return null
  }
}

/** 建集合；已存在时支付宝云会抛错，当成正常 */
async function ensureCollection(name) {
  try {
    await db().createCollection(name)
    return 'created'
  }
  catch (err) {
    return 'exists'
  }
}

/** 语义版本拆成数字数组，v 前缀和 -/+ 后缀都忽略 */
function parseVersionParts(value) {
  return String(value || '').replace(/^v/i, '').split(/[.+-]/).map((one) => {
    const n = parseInt(one, 10)
    return Number.isFinite(n) ? n : 0
  })
}

/** a<b 返回 -1，a>b 返回 1，相等 0；缺段的按 0 补 */
function compareVersion(a, b) {
  const pa = parseVersionParts(a)
  const pb = parseVersionParts(b)
  const len = Math.max(pa.length, pb.length)
  for (let i = 0; i < len; i++) {
    const diff = (pa[i] || 0) - (pb[i] || 0)
    if (diff !== 0) {
      return diff < 0 ? -1 : 1
    }
  }
  return 0
}

/** {zh, en} 多语言文本：允许只给一边，非法值一律丢弃 */
function cleanText(value) {
  if (value == null) {
    return undefined
  }
  if (typeof value === 'string') {
    return value.trim() ? value : undefined
  }
  if (typeof value === 'object') {
    const out = {}
    if (typeof value.zh === 'string' && value.zh.trim()) {
      out.zh = value.zh
    }
    if (typeof value.en === 'string' && value.en.trim()) {
      out.en = value.en
    }
    return Object.keys(out).length ? out : undefined
  }
  return undefined
}

function normalizeChangelog(list) {
  if (!Array.isArray(list)) {
    return undefined
  }
  const out = []
  list.forEach((raw) => {
    if (!raw) {
      return
    }
    const entry = {}
    let tag = raw.tag == null ? '' : String(raw.tag)
    if (raw.tagEn != null) {
      entry.tagEn = String(raw.tagEn)
    }
    if (tag) {
      entry.tag = tag
    }
    entry.type = String(raw.type || '').trim() || inferType(tag || entry.tagEn)
    const text = (typeof raw.text === 'string')
      ? cleanText({ zh: raw.text })
      : cleanText(raw.text)
    if (text) {
      entry.text = text
    }
    out.push(entry)
  })
  return out
}

function cleanInstaller(src) {
  const one = src || {}
  const out = {}
  ;['url', 'fileName', 'image'].forEach((key) => {
    if (one[key] != null && one[key] !== '') {
      out[key] = String(one[key])
    }
  })
  if (one.size != null) {
    out.size = numOr(one.size, 0)
  }
  return out
}

function cleanUpgrade(src) {
  const one = src || {}
  const out = {}
  ;['url', 'fileName', 'minVersion', 'sha256'].forEach((key) => {
    if (one[key] != null && one[key] !== '') {
      out[key] = String(one[key])
    }
  })
  if (one.size != null) {
    out.size = numOr(one.size, 0)
  }
  if (one.schemaVersion != null) {
    out.schemaVersion = numOr(one.schemaVersion, 0)
  }
  if (one.mandatory != null) {
    out.mandatory = toBool(one.mandatory, false)
  }
  if (one.restartRequired != null) {
    out.restartRequired = toBool(one.restartRequired, true)
  }
  return out
}

function normalizePlatforms(map) {
  if (!map || typeof map !== 'object' || Array.isArray(map)) {
    return undefined
  }
  const out = {}
  Object.keys(map).forEach((id) => {
    const one = map[id] || {}
    const item = {}
    const label = cleanText(one.label)
    if (label) {
      item.label = label
    }
    if (one.installer) {
      const installer = cleanInstaller(one.installer)
      if (Object.keys(installer).length) {
        item.installer = installer
      }
    }
    if (one.upgrade) {
      const upgrade = cleanUpgrade(one.upgrade)
      if (Object.keys(upgrade).length) {
        item.upgrade = upgrade
      }
    }
    if (Object.keys(item).length) {
      out[id] = item
    }
  })
  return Object.keys(out).length ? out : undefined
}

/**
 * 把外部传来的版本条目规范化成「只含有效字段」的部分对象。
 * version 必填（_id 可兜底），其余字段缺席就不进结果，交给合并逻辑保留线上原值。
 */
function normalizeItem(raw) {
  const src = raw || {}
  let version = String(src.version || '').trim().replace(/^v/i, '')
  if (!version && src._id) {
    version = String(src._id).replace(/^v/i, '')
  }
  if (!version) {
    return null
  }
  const out = { version }
  if (src.channel) {
    out.channel = String(src.channel)
  }
  if (src.releasedAt != null) {
    out.releasedAt = numOr(src.releasedAt, 0)
  }
  if (src.published != null) {
    out.published = toBool(src.published, false)
  }
  const summary = cleanText(src.summary)
  if (summary) {
    out.summary = summary
  }
  const notes = cleanText(src.notes)
  if (notes) {
    out.notes = notes
  }
  const changelog = normalizeChangelog(src.changelog)
  if (changelog) {
    out.changelog = changelog
  }
  const platforms = normalizePlatforms(src.platforms)
  if (platforms) {
    out.platforms = platforms
  }
  return out
}

/**
 * 深合并：对象逐键合并，数组与标量整段替换。
 * 推送脚本只带一个平台的 upgrade 时，不会把版本里其它平台或 changelog 抹掉。
 */
function mergeDeep(base, patch) {
  if (patch === undefined) {
    return base
  }
  if (patch === null || Array.isArray(patch) || typeof patch !== 'object') {
    return patch
  }
  const out = (base && typeof base === 'object' && !Array.isArray(base)) ? Object.assign({}, base) : {}
  Object.keys(patch).forEach((key) => {
    const next = patch[key]
    const prior = out[key]
    if (next && typeof next === 'object' && !Array.isArray(next)
      && prior && typeof prior === 'object' && !Array.isArray(prior)) {
      out[key] = mergeDeep(prior, next)
    }
    else {
      out[key] = next
    }
  })
  return out
}

module.exports = {
  COLLECTIONS,
  PLATFORMS,
  DEFAULT_CHANNEL,
  CHANNELS,
  platformLabel,
  webKeyOf,
  inferType,
  db,
  databaseCmd,
  numOr,
  toBool,
  compact,
  pick,
  pageParams,
  putDoc,
  getDoc,
  ensureCollection,
  compareVersion,
  parseVersionParts,
  cleanText,
  normalizeChangelog,
  normalizePlatforms,
  normalizeItem,
  mergeDeep,
}
