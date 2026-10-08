'use strict'

/**
 * /jy-content 入口：读接口公开，写接口必须带管理令牌。
 *
 * 服务空间与「问题百科」项目共用，函数名和 URL 路径统一加 jy- 前缀，避免撞车。
 *
 * URL 化之后 POST 请求体顶层字段是 action，其余是该 action 需要的参数；
 * GET 时同样这些参数走 querystring。返回统一 code / message / data，code 为 0 表示成功。
 */

const read = require('./read.js')
const write = require('./write.js')
const admin = require('./admin.js')
const lib = require('./lib.js')
const pkg = require('./package.json')

const READ_ONLY = ['ping', 'version', 'manifest', 'columns', 'column', 'episodes', 'articles', 'playlist', 'article', 'articleText']

const WRITE_ACTIONS = {
  initSite: write.initSite,
  upsertEpisodes: write.upsertEpisodes,
  upsertColumns: write.upsertColumns,
  upsertAccounts: write.upsertAccounts,
  deleteEpisodes: write.deleteEpisodes,
  pushRelease: write.pushRelease,
  remoteStats: write.remoteStats,
  updateSettings: write.updateSettings,
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
  const action = parsed.action
  const body = parsed.body
  const query = parsed.query

  if (!action) {
    return { code: 400, message: '缺少 action', data: null }
  }

  // 探活不碰数据库，部署完第一时间就能验联通
  if (action === 'ping') {
    return ok({
      pong: Date.now(),
      nodeVersion: process.version,
      adminConfigured: Boolean(admin.expectedToken()),
    })
  }
  if (action === 'version') {
    // inMode 说明这台运行时认哪种 in 写法，线上出问题时先 curl 这个看它走哪条路
    return ok({ fn: pkg.name, version: pkg.version, inMode: lib.inMode() })
  }
  if (action === 'manifest') {
    return wrap(await read.manifest())
  }
  if (action === 'columns') {
    return wrap(await read.columns())
  }
  if (action === 'column') {
    return wrap(await read.column(body.id || query.id))
  }
  if (action === 'episodes') {
    return wrap(await read.episodes(body.ids || query.ids))
  }
  if (action === 'articles') {
    return wrap(await read.articles(query, body))
  }
  if (action === 'playlist') {
    return wrap(await read.playlist(query, body))
  }
  if (action === 'article') {
    return wrap(await read.article(body.id || query.id))
  }
  if (action === 'articleText') {
    return wrap(await read.articleText(body.id || query.id, body.url || query.url))
  }

  const handler = WRITE_ACTIONS[action]
  if (!handler) {
    const known = READ_ONLY.concat(Object.keys(WRITE_ACTIONS))
    return { code: 404, message: `未知 action，可用: ${known.join(',')}`, data: null }
  }
  const denied = admin.requireAdmin(body, query, parsed.headers)
  if (denied) {
    return denied
  }
  return wrap(await handler(body))
}

exports.main = async (event) => {
  const parsed = parseEvent(event)
  try {
    return await dispatch(parsed)
  }
  catch (err) {
    console.error('[content] 执行失败:', parsed.action, err)
    return { code: 500, message: String((err && err.message) || err), data: null }
  }
}
