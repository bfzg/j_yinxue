import { reactive } from 'vue'
import seedArticles from '@/static/data/articles.json'
import seedColumns from '@/static/data/columns.json'
import seedPlaylist from '@/static/data/playlist.json'
import { fetchAll, fetchColumn, fetchColumns, fetchManifest } from '@/api/content'
import type { SiteApp, SiteManifest, SiteSettings } from '@/api/content'
import type { Article } from '@/types/article'
import type { Column, ColumnEpisode } from '@/types/column'

/**
 * 站点数据的唯一来源。
 *
 * 三级回落：云端集合 > 本地缓存 > 打包在小程序里的 static/data/*.json。
 * 打包 json 只作为首次启动和云端不可达时的保底，日常更新全部由
 * 后台面板推数 + dataVersion 比对驱动，用户不需要再手工替换 json。
 */

/** 播放列表条目：playlist 接口的返回形状，比 Article 少字段但带 columnId */
export interface PlaylistEntry {
  id: string
  title: string
  description?: string
  cover?: string
  audioUrl: string
  duration?: number
  sort?: number
  enabled?: boolean
  publishedAt?: string
  columnId?: string
  columnName?: string
  episodeNo?: number
  episodeTotal?: number
  accountId?: string
}

type SourceKind = 'seed' | 'cache' | 'cloud'

interface SiteState {
  ready: boolean
  syncing: boolean
  error: string
  source: SourceKind
  dataVersion: number
  updatedAt: number
  counts: { episodes: number, columns: number }
  app: SiteApp
  settings: SiteSettings
  articles: Article[]
  playlist: PlaylistEntry[]
  columns: Column[]
}

const K_MANIFEST = 'jy:manifest'
const K_ARTICLES = 'jy:articles'
const K_PLAYLIST = 'jy:playlist'
const K_COLUMNS = 'jy:columns'
const K_COLUMN_PREFIX = 'jy:column:'

/** 微信单条缓存上限 1MB，留出余量，超了就先截断，反正云端拉一次就能补全 */
const MAX_STORAGE_CHARS = 900 * 1024
/** 同一份数据 5 分钟内不重复问 manifest，避免页面 onShow 打爆云函数 */
const MANIFEST_TTL = 5 * 60 * 1000

const SEED_APP: SiteApp = (seedArticles as any).app
const SEED_SETTINGS: SiteSettings = (seedPlaylist as any).settings || { autoplayNext: true, playMode: 'sequence' }

const site = reactive<SiteState>({
  ready: false,
  syncing: false,
  error: '',
  source: 'seed',
  dataVersion: 0,
  updatedAt: 0,
  counts: { episodes: 0, columns: 0 },
  app: SEED_APP,
  settings: SEED_SETTINGS,
  articles: [],
  playlist: [],
  columns: [],
})

let hydrated = false
let inflight: Promise<void> | null = null
let lastCheckedAt = 0

function readCache<T>(key: string): T | null {
  try {
    const value = uni.getStorageSync(key)
    return value ? (value as T) : null
  }
  catch {
    return null
  }
}

function writeCache(key: string, value: unknown) {
  try {
    const raw = JSON.stringify(value)
    if (raw && raw.length > MAX_STORAGE_CHARS && Array.isArray(value)) {
      // 按累积长度砍尾部，保留前面的条目（列表已按 sort 排好，头部就是最新）
      const kept: unknown[] = []
      let size = 2
      for (const item of value) {
        const one = JSON.stringify(item)
        size += one.length + 1
        if (size > MAX_STORAGE_CHARS)
          break
        kept.push(item)
      }
      uni.setStorageSync(key, kept)
      return
    }
    uni.setStorageSync(key, value)
  }
  catch {
    // 缓存写失败不影响使用，下次冷启会退回 seed 或直接走云端
  }
}

/** 同步读本地状态，页面首次渲染就能有内容 */
function hydrate() {
  if (hydrated)
    return
  hydrated = true

  const manifest = readCache<SiteManifest>(K_MANIFEST)
  const articles = readCache<Article[]>(K_ARTICLES)
  const playlist = readCache<PlaylistEntry[]>(K_PLAYLIST)
  const columns = readCache<Column[]>(K_COLUMNS)

  if (articles?.length) {
    site.articles = articles.filter(one => one.enabled !== false)
    site.source = 'cache'
  }
  else {
    site.articles = (seedArticles as any).items || []
  }

  if (playlist?.length) {
    site.playlist = playlist.filter(one => one.enabled !== false)
  }
  else {
    site.playlist = (seedPlaylist as any).items || []
  }

  if (columns?.length) {
    site.columns = columns
    site.source = 'cache'
  }
  else {
    site.columns = (seedColumns as any).items || []
  }

  if (manifest) {
    site.app = manifest.app || SEED_APP
    site.settings = manifest.settings || SEED_SETTINGS
    site.dataVersion = Number(manifest.dataVersion || 0)
    site.updatedAt = Number(manifest.updatedAt || 0)
    site.counts = manifest.counts || site.counts
  }

  site.ready = true
}

function applyManifest(manifest: SiteManifest) {
  site.app = manifest.app || SEED_APP
  site.settings = manifest.settings || SEED_SETTINGS
  site.dataVersion = Number(manifest.dataVersion || 0)
  site.updatedAt = Number(manifest.updatedAt || 0)
  site.counts = manifest.counts || site.counts
  writeCache(K_MANIFEST, manifest)
}

async function pullAll() {
  const [articles, playlist, columns] = await Promise.all([
    fetchAll<Article>('articles'),
    fetchAll<PlaylistEntry>('playlist'),
    fetchColumns().then(res => res.items || []),
  ])

  // 已下架 / 还没发布的占位条目不下发，客户端只留能读能播的
  const liveArticles = articles.filter(one => (one as any).enabled !== false)
  const livePlaylist = playlist.filter(one => one.enabled !== false)

  // 云端空集合时不要把兜底数据一起清掉，否则面板还没推数就会白屏
  if (liveArticles.length) {
    site.articles = liveArticles
    writeCache(K_ARTICLES, liveArticles)
  }
  if (livePlaylist.length) {
    site.playlist = livePlaylist
    writeCache(K_PLAYLIST, livePlaylist)
  }
  if (columns.length) {
    site.columns = columns
    writeCache(K_COLUMNS, columns)
  }
  site.source = 'cloud'
}

/**
 * 拉取线上数据。只有 manifest.dataVersion 变了才真正取数，
 * 所以后台每发布一次，用户下次进入即自动更新，无需重新发版小程序。
 */
export function ensureSiteData(force = false): Promise<void> {
  hydrate()

  const fresh = Date.now() - lastCheckedAt < MANIFEST_TTL
  if (inflight)
    return inflight
  if (!force && site.source === 'cloud' && fresh)
    return Promise.resolve()

  inflight = (async () => {
    site.syncing = true
    site.error = ''
    try {
      const prevVersion = site.dataVersion
      const manifest = await fetchManifest()
      applyManifest(manifest)
      lastCheckedAt = Date.now()
      // 版本号没变说明后台没推新数据，列表就不用再取一遍
      if (force || Number(manifest.dataVersion || 0) !== prevVersion || site.source !== 'cloud') {
        await pullAll()
      }
    }
    catch (err: any) {
      site.error = String(err?.message || err || '云端数据加载失败')
      lastCheckedAt = Date.now()
    }
    finally {
      site.syncing = false
      inflight = null
    }
  })()

  return inflight
}

/**
 * 取单个栏目的分集。列表接口不再内联 episodes，所以按需拉取并缓存，
 * 兜底用打包 json 里的那份，保证推数之前点进去也有内容。
 */
export async function loadColumnEpisodes(id: string): Promise<ColumnEpisode[]> {
  const key = `${K_COLUMN_PREFIX}${id}`
  const cached = readCache<{ version: number, episodes: ColumnEpisode[] }>(key)
  if (cached && Number(cached.version) === site.dataVersion) {
    return cached.episodes || []
  }

  const seedItem = ((seedColumns as any).items || []).find((one: Column) => one.id === id)
  try {
    const res = await fetchColumn(id)
    const episodes = res?.item?.episodes || []
    if (episodes.length) {
      writeCache(key, { version: site.dataVersion, episodes })
      return episodes
    }
  }
  catch {
    // 云端没数据/断网，走兜底
  }
  return seedItem?.episodes || []
}

export function findColumn(id: string): Column | undefined {
  return site.columns.find(item => item.id === id)
}

export function findArticle(id: string): Article | undefined {
  return site.articles.find(item => item.id === id)
}

export function useSiteData() {
  hydrate()
  return {
    site,
    ensureSiteData,
    loadColumnEpisodes,
    findColumn,
    findArticle,
  }
}

export { site }
