'use strict'

/**
 * 写接口：只给发布脚本和后台用，进 index.js 前必过 requireAdmin。
 *
 * 三条纪律：
 *   1. 一律 doc(id).set() 幂等覆盖，重跑同一批不会写出重复数据；
 *   2. 部分更新走深合并：推送脚本只带一个平台的产物时，不会把该版本里
 *      其它平台、更新日志、摘要抹掉，后台二次修改也走同一条路；
 *   3. 单次调用最多 100 条，脚本负责切片。
 */

const {
  COLLECTIONS,
  DEFAULT_CHANNEL,
  CHANNELS,
  db,
  getDoc,
  putDoc,
  mergeDeep,
  normalizeItem,
  ensureCollection,
  numOr,
  platformLabel,
} = require('./lib.js')

const MAX_ITEMS_PER_CALL = 100

async function initDb() {
  const versions = await ensureCollection(COLLECTIONS.versions)
  return { collections: { ccnvr_versions: versions } }
}

/** 平台条目补齐中英文标签，官网按平台渲染时不用再猜 */
function fillLabels(platforms) {
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

/**
 * 把「线上已有文档 + 本次传入字段」合成最终文档。
 * 未发布默认不成立：只有显式 published=false 才是草稿，脚本推送默认可见。
 */
function buildDoc(existing, raw, now, replace) {
  const normalized = normalizeItem(raw)
  if (!normalized) {
    return null
  }
  const merged = replace ? Object.assign({}, normalized) : mergeDeep(existing || {}, normalized)
  const doc = Object.assign({}, merged)
  doc.version = String(normalized.version)
  doc.channel = CHANNELS.indexOf(String(doc.channel)) >= 0 ? String(doc.channel) : DEFAULT_CHANNEL
  doc.published = doc.published === undefined ? true : doc.published === true
  doc.releasedAt = numOr(doc.releasedAt, 0) || now
  if (doc.platforms) {
    doc.platforms = fillLabels(doc.platforms)
  }
  doc.createdAt = numOr(existing && existing.createdAt, 0) || now
  doc.updatedAt = now
  return doc
}

/** upsertVersion：创建或部分更新一条版本记录（_id = "v" + 版本号） */
async function upsertVersion(payload = {}) {
  const input = Array.isArray(payload.items) && payload.items.length
    ? payload.items
    : (payload.item ? [payload.item] : [])
  if (!input.length) {
    return { code: 400, message: '缺少 item 或 items' }
  }
  if (input.length > MAX_ITEMS_PER_CALL) {
    return { code: 400, message: `单次最多 ${MAX_ITEMS_PER_CALL} 条，请分批` }
  }
  const now = Date.now()
  const replace = payload.mode === 'replace'
  const written = []
  const skipped = []
  let last = null
  for (const raw of input) {
    const probe = normalizeItem(raw)
    if (!probe) {
      skipped.push('缺少 version')
      continue
    }
    const id = `v${probe.version}`
    const existing = await getDoc(COLLECTIONS.versions, id)
    const doc = buildDoc(existing, raw, now, replace)
    if (!doc) {
      skipped.push(id)
      continue
    }
    await putDoc(COLLECTIONS.versions, id, doc)
    written.push(id)
    last = Object.assign({ _id: id }, doc)
  }
  if (!written.length) {
    return { code: 400, message: `没有可写入的版本（${skipped.join(',') || '参数不合法'}）` }
  }
  return { written: written.length, ids: written, item: last, skipped }
}

/** deleteVersion：按 _id 或版本号删掉一条记录 */
async function deleteVersion(payload = {}) {
  let id = String(payload.id || '').trim()
  if (!id && payload.version) {
    id = `v${String(payload.version).replace(/^v/i, '')}`
  }
  if (!id) {
    return { code: 400, message: '缺少 id 或 version' }
  }
  try {
    await db().collection(COLLECTIONS.versions).doc(id).remove()
  }
  catch (err) {
    return { code: 500, message: String((err && err.message) || err) }
  }
  return { deleted: id }
}

module.exports = { MAX_ITEMS_PER_CALL, initDb, upsertVersion, deleteVersion }
