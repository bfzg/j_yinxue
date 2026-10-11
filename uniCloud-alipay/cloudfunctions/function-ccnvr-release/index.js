'use strict'

/**
 * /ccnvr-release 入口：读接口公开（官网下载页、设备更新源），
 * 写接口必须带管理令牌（发布脚本、后台）。
 *
 * 服务空间与「九哥隐学」共用，函数名与 URL 路径统一加 ccnvr- 前缀，避免撞车。
 *
 * URL 化之后 POST 请求体顶层字段是 action，其余是该 action 需要的参数；
 * 返回统一 code / message / data，code 为 0 表示成功。
 */

const read = require('./read.js')
const write = require('./write.js')
const admin = require('./admin.js')
const pkg = require('./package.json')

const READ_ACTIONS = {
  // 探活不碰数据库，部署完第一时间就能验联通
  ping: () => ({ pong: Date.now(), nodeVersion: process.version, adminConfigured: Boolean(admin.expectedToken()) }),
  version: () => ({ fn: pkg.name, version: pkg.version }),
  list: (body, query, headers) => read.list(body, query, headers),
  latest: (body, query) => read.latest(body, query),
  manifest: (body, query) => read.manifest(body, query),
  changelog: (body, query) => read.changelog(body, query),
  index: (body, query) => read.index(body, query),
  indexes: (body, query) => read.indexes(body, query),
}

const WRITE_ACTIONS = {
  initDb: write.initDb,
  upsertVersion: write.upsertVersion,
  deleteVersion: write.deleteVersion,
}

/** 支付宝云 URL 化把原始 body 塞在 event.body，可能是字符串，也可能是 base64 */
function parseEvent(event) {
  const e = event || {}
  let body = e.body
  if (typeof body === 'string' && body.length) {
    const raw = e.isBase64Encoded ? Buffer.from(body, 'base64').toString('utf8') : body
    try {
      body = JSON.parse(raw)
    }
    catch (err) {
      body = {}
    }
  }
  if (!body || typeof body !== 'object') {
    body = {}
  }
  const query = e.queryStringParameters || e.query || {}
  const headers = e.headers || {}
  const action = String(body.action || query.action || e.action || '')
  return { action, body, query, headers }
}

function ok(data) {
  return { code: 0, message: 'ok', data }
}

/** 业务函数既可能返回 code/message 错误，也可能直接返回数据对象 */
function wrap(res) {
  if (res && typeof res === 'object' && res.code && res.code !== 0) {
    return { code: res.code, message: res.message || '请求失败', data: null }
  }
  return ok(res === undefined ? null : res)
}

async function dispatch(parsed) {
  const { action, body, query, headers } = parsed

  if (!action) {
    return { code: 400, message: '缺少 action', data: null }
  }

  if (READ_ACTIONS[action]) {
    return wrap(await READ_ACTIONS[action](body, query, headers))
  }

  const handler = WRITE_ACTIONS[action]
  if (!handler) {
    const known = Object.keys(READ_ACTIONS).concat(Object.keys(WRITE_ACTIONS))
    return { code: 404, message: `未知 action，可用: ${known.join(',')}`, data: null }
  }
  const denied = admin.requireAdmin(body, query, headers)
  if (denied) {
    return denied
  }
  return wrap(await handler(body, query))
}

exports.main = async (event) => {
  const parsed = parseEvent(event)
  try {
    return await dispatch(parsed)
  }
  catch (err) {
    console.error('[ccnvr-release] 执行失败:', parsed.action, err)
    return { code: 500, message: String((err && err.message) || err), data: null }
  }
}
