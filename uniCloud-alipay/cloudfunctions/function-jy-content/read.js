'use strict'

/**
 * 读接口：小程序运行时依赖的几个动作，全部公开、不需要 token。
 * 返回结构与改造前的 static/data/*.json 对齐，前端拿到就能直接渲染。
 */

const {
  COLLECTIONS,
  db,
  pick,
  numOr,
  pageParams,
  getMeta,
  docsByIds,
  rowsToMap,
  EPISODE_FIELDS,
  COLUMN_FIELDS,
  DEFAULT_APP,
  DEFAULT_SETTINGS,
} = require('./lib.js')

const https = require('node:https')

/** 列表按 sort 升序，同 sort 再按发布日期倒序，保证栏目内集序稳定 */
function orderEpisodes() {
  return db().collection(COLLECTIONS.episodes).orderBy('sort', 'asc').orderBy('publishedAt', 'desc')
}

/** 没被任何栏目 episodeIds 引用到的集，排到本栏目最后 */
const UNRANKED = 1e9

/**
 * 一次请求内算一遍线上可见状态：
 *   offline  后台下架的集（enabled=false）
 *   rank     每集在所属栏目里的第几位（面板按集号写好 episodeIds，线上集序以它为准）
 * 查询失败就退回「没下架、没排序」，宁可多展示几集，也不能让整个读接口 500。
 */
async function liveView() {
  const view = { offline: new Set(), rank: new Map(), degraded: false }
  try {
    const both = await Promise.all([
      db().collection(COLLECTIONS.columns).limit(500).get(),
      db().collection(COLLECTIONS.episodes).where({ enabled: false }).limit(5000).get(),
    ]);
    (both[0].data || []).forEach((col) => {
      const ids = col.episodeIds || []
      for (let i = 0; i < ids.length; i++) {
        const key = String(ids[i])
        if (!view.rank.has(key)) {
          view.rank.set(key, i)
        }
      }
    });
    (both[1].data || []).forEach((doc) => {
      view.offline.add(String(doc._id || doc.id))
    })
  }
  catch (err) {
    view.degraded = String((err && err.message) || err)
  }
  return view
}

function isLive(view, id) {
  return !view.offline.has(String(id))
}

function rankOf(view, id) {
  const one = view.rank.get(String(id))
  return one === undefined ? UNRANKED : one
}

/** 栏目里真正能看的集：episodeIds 的顺序，去掉下架的；episodeIds 空了再按集号兜 */
function liveIds(view, col) {
  const ids = (col.episodeIds || []).map(one => String(one)).filter(Boolean)
  const kept = ids.filter(one => isLive(view, one))
  return { ids: kept, hasRef: ids.length > 0 }
}

function toEpisode(doc) {
  const out = pick(doc, EPISODE_FIELDS)
  out.id = doc._id || doc.id
  out.enabled = doc.enabled !== false
  out.sort = numOr(doc.sort, 0)
  out.episodeNo = numOr(doc.episodeNo, 0)
  out.episodeTotal = numOr(doc.episodeTotal, 0)
  out.duration = numOr(doc.duration, 0)
  return out
}

async function manifest() {
  const meta = await getMeta()
  const both = await Promise.all([
    db().collection(COLLECTIONS.episodes).count(),
    db().collection(COLLECTIONS.columns).count(),
  ])
  return {
    app: (meta && meta.app) || DEFAULT_APP,
    settings: (meta && meta.settings) || DEFAULT_SETTINGS,
    dataVersion: numOr(meta && meta.dataVersion, 0),
    updatedAt: numOr(meta && meta.updatedAt, 0),
    counts: {
      episodes: numOr(both[0] && both[0].total, 0),
      columns: numOr(both[1] && both[1].total, 0),
    },
  }
}

async function columns() {
  const view = await liveView()
  const res = await db().collection(COLLECTIONS.columns).orderBy('sort', 'asc').orderBy('name', 'asc').limit(500).get()
  const items = [];
  (res.data || []).forEach((doc) => {
    const out = pick(doc, COLUMN_FIELDS)
    out.id = doc._id || doc.id
    out.sort = numOr(doc.sort, 0)
    out.episodeTotal = numOr(doc.episodeTotal, 0)
    const live = liveIds(view, doc)
    out.nEpisodes = live.hasRef ? live.ids.length : numOr(doc.nEpisodes, 0)
    // 一集都不剩的栏目不该再出现在首页，整个文件夹卡片一起消失
    if (out.nEpisodes > 0) {
      items.push(out)
    }
  })
  return { items, liveOnly: !view.degraded }
}

/** 单栏目 + 整串分集：episodeIds 由面板写入，决定线上集序 */
async function column(id) {
  if (!id) {
    return { code: 400, message: '缺少参数 id' }
  }
  const view = await liveView()
  const found = await db().collection(COLLECTIONS.columns).doc(id).get()
  const doc = found.data && found.data[0]
  if (!doc) {
    return { code: 404, message: `栏目不存在: ${id}` }
  }
  const live = liveIds(view, doc)
  let ids = live.ids
  if (!live.hasRef) {
    // 老数据没写 episodeIds：直接按 columnId 捞未下架的，按集号排
    const more = await db().collection(COLLECTIONS.episodes).where({ columnId: String(id) }).limit(2000).get()
    ids = (more.data || [])
      .filter(one => isLive(view, one._id || one.id))
      .sort((a, b) => numOr(a.episodeNo, 0) - numOr(b.episodeNo, 0)
        || numOr(a.sort, 0) - numOr(b.sort, 0))
      .map(one => String(one._id || one.id))
  }
  const fetched = ids.length
    ? await docsByIds(COLLECTIONS.episodes, ids)
    : { rows: [], fetchMode: 'empty' }
  // 库里缺哪集就跳过哪集，顺序始终按 episodeIds
  const byId = rowsToMap(fetched.rows)
  Object.keys(byId).forEach((key) => {
    byId[key] = toEpisode(byId[key])
  })
  const episodes = ids
    .map(one => byId[String(one)])
    .filter(one => one && isLive(view, one.id))
  const out = pick(doc, COLUMN_FIELDS)
  out.id = doc._id || doc.id
  // 首页卡片和详情页必须同一个数
  out.nEpisodes = episodes.length
  out.episodes = episodes.map(one => ({
    awemeId: one.id,
    title: one.title,
    episodeNo: one.episodeNo,
    audioUrl: one.audioUrl,
    articleUrl: one.articleUrl,
    duration: one.duration,
    publishedAt: one.publishedAt,
    summary: one.summary,
    enabled: one.enabled,
  }))
  return { item: out, fetchMode: fetched.fetchMode, liveOnly: !view.degraded }
}

/** 按 id 批量取分集，用于播放列表补全 */
async function episodes(ids) {
  const all = String(ids || '').split(',').map(one => one.trim()).filter(Boolean)
  // 调用方手滑传重了也别吐出重复的集，播放列表会连着播两遍
  const list = Array.from(new Set(all))
  if (!list.length) {
    return { items: [] }
  }
  const fetched = await docsByIds(COLLECTIONS.episodes, list)
  const byId = rowsToMap(fetched.rows)
  Object.keys(byId).forEach((key) => {
    byId[key] = toEpisode(byId[key])
  })
  return {
    items: list.map(one => byId[String(one)]).filter(Boolean),
    fetchMode: fetched.fetchMode,
  }
}

async function articles(query, body) {
  const view = await liveView()
  const page = pageParams(query, body, 200)
  const both = await Promise.all([
    orderEpisodes().skip(page.offset).limit(page.limit).get(),
    db().collection(COLLECTIONS.episodes).count(),
  ])
  return {
    items: (both[0].data || []).map((doc) => {
      const one = toEpisode(doc)
      one.rank = rankOf(view, one.id)
      return one
    }),
    total: numOr(both[1] && both[1].total, 0),
    limit: page.limit,
    offset: page.offset,
  }
}

/** 播放列表只带播放要用的字段，省流量 */
async function playlist(query, body) {
  const view = await liveView()
  const page = pageParams(query, body, 300)
  const three = await Promise.all([
    orderEpisodes().skip(page.offset).limit(page.limit).get(),
    db().collection(COLLECTIONS.episodes).count(),
    getMeta(),
  ])
  const items = (three[0].data || []).map((doc) => {
    const one = toEpisode(doc)
    return {
      id: one.id,
      title: one.title,
      description: one.summary,
      cover: one.cover,
      audioUrl: one.audioUrl,
      duration: one.duration,
      sort: one.sort,
      rank: rankOf(view, one.id),
      publishedAt: one.publishedAt,
      enabled: one.enabled,
      columnName: one.columnName,
      columnId: one.columnId,
      episodeNo: one.episodeNo,
      episodeTotal: one.episodeTotal,
      accountId: one.accountId,
    }
  })
  return {
    items,
    total: numOr(three[1] && three[1].total, 0),
    limit: page.limit,
    offset: page.offset,
    settings: (three[2] && three[2].settings) || DEFAULT_SETTINGS,
  }
}

async function article(id) {
  if (!id) {
    return { code: 400, message: '缺少参数 id' }
  }
  const res = await db().collection(COLLECTIONS.episodes).doc(id).get()
  const doc = res.data && res.data[0]
  if (!doc) {
    return { code: 404, message: `文章不存在: ${id}` }
  }
  return { item: toEpisode(doc) }
}

/**
 * 正文直链的服务端兜底通道。
 *
 * 云存储下载域名是 CDN，个别边缘节点会把「回源签名失败」的 403 错误页一起缓存住，
 * 小程序直连就会拿到一段 OSS XML。这里由云函数按四条路依次试：
 * CDN 原链 → CDN 换缓存键 → 源站域名 → 源站换缓存键。
 * 源站那两条完全绕开 CDN，节点被错误页毒化了也能取到正文。
 * 只允许取自家存储桶，避免这个 action 被人当开放代理刷外链。
 */
const TEXT_HOSTS = [
  'env-00jxu1ytdn0v.normal.cloudstatic.cn',
  'env-00jxu1ytdn0v-hz.object.cloudrun.cloudbaseapp.cn',
]
/** 下载域名 → 源站域名：同一个桶，少一层 CDN */
const TEXT_HOST_MAP = {
  'env-00jxu1ytdn0v.normal.cloudstatic.cn': 'env-00jxu1ytdn0v-hz.object.cloudrun.cloudbaseapp.cn',
}
const TEXT_MAX_BYTES = 4 * 1024 * 1024
const TEXT_TIMEOUT_MS = 8000

function isAllowedTextUrl(urlStr) {
  let parsed = null
  try {
    parsed = new URL(String(urlStr))
  }
  catch (err) {
    return false
  }
  return parsed.protocol === 'https:' && TEXT_HOSTS.includes(parsed.hostname)
}

/** CDN 错误页长得和正文完全不同，必须当失败处理，不能往下塞给解析器 */
function isErrorPageText(text) {
  const head = String(text || '').replace(/^\s+/, '').slice(0, 200)
  return head.indexOf('<?xml') === 0 || head.indexOf('<Error>') === 0
}

function fetchOneText(urlStr, redirectsLeft) {
  return new Promise((resolve, reject) => {
    const req = https.get(urlStr, { timeout: TEXT_TIMEOUT_MS }, (res) => {
      const status = Number(res.statusCode || 0)
      const location = res.headers && res.headers.location
      if (status >= 300 && status < 400 && location && redirectsLeft > 0) {
        res.resume()
        let next = ''
        try {
          next = new URL(location, urlStr).toString()
        }
        catch (err) {
          reject(new Error('跳转地址无法解析'))
          return
        }
        if (!isAllowedTextUrl(next)) {
          reject(new Error('跳转目标不在白名单'))
          return
        }
        fetchOneText(next, redirectsLeft - 1).then(resolve, reject)
        return
      }
      const chunks = []
      let size = 0
      res.on('data', (piece) => {
        size += piece.length
        if (size > TEXT_MAX_BYTES) {
          req.destroy(new Error('正文超出大小上限'))
          return
        }
        chunks.push(piece)
      })
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        if (status !== 200) {
          reject(new Error(`HTTP ${status}`))
          return
        }
        if (isErrorPageText(text)) {
          reject(new Error('CDN 返回错误页'))
          return
        }
        if (!text.trim()) {
          reject(new Error('正文为空'))
          return
        }
        resolve(text)
      })
      res.on('error', reject)
    })
    req.on('error', reject)
    req.on('timeout', () => req.destroy(new Error('取正文超时')))
  })
}

async function articleText(id, url) {
  let target = String(url || '').trim()
  let via = 'url'
  if (!target) {
    if (!id) {
      return { code: 400, message: '缺少参数 id 或 url' }
    }
    const res = await db().collection(COLLECTIONS.episodes).doc(String(id)).get()
    const doc = res.data && res.data[0]
    if (!doc) {
      return { code: 404, message: `文章不存在: ${id}` }
    }
    target = String(doc.articleUrl || '').trim()
    via = 'id'
    if (!target) {
      return { code: 404, message: '该集还没有正文直链' }
    }
  }
  if (!isAllowedTextUrl(target)) {
    return { code: 400, message: '正文地址不在允许的域名内' }
  }
  const withBust = one => `${one + (one.includes('?') ? '&' : '?')}jyrb=${Date.now()}`
  const hosts = [target]
  try {
    const originHost = TEXT_HOST_MAP[new URL(target).hostname]
    if (originHost) {
      const originUrl = new URL(target)
      originUrl.hostname = originHost
      hosts.push(originUrl.toString())
    }
  }
  catch (err) {
    // 解析不了就少一条兜底路，不影响前面那条
  }
  const attempts = []
  hosts.forEach((one, hostIndex) => {
    const host = hostIndex === 0 ? 'cdn' : 'origin'
    attempts.push({ url: one, host, bust: false })
    attempts.push({ url: withBust(one), host, bust: true })
  })

  let lastErr = null
  for (let i = 0; i < attempts.length; i++) {
    try {
      const text = await fetchOneText(attempts[i].url, 2)
      return {
        text,
        chars: text.length,
        via,
        host: attempts[i].host,
        bust: attempts[i].bust,
        retries: i,
      }
    }
    catch (err) {
      lastErr = err
    }
  }
  return { code: 502, message: `云端取正文失败: ${String((lastErr && lastErr.message) || lastErr)}` }
}

module.exports = { manifest, columns, column, episodes, articles, playlist, article, articleText, isErrorPageText, isAllowedTextUrl }
