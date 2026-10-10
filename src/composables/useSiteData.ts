import { computed, reactive } from 'vue'
import { fetchAll, fetchColumn, fetchColumns, fetchManifest } from '@/api/content'
import type { SiteApp, SiteManifest, SiteSettings } from '@/api/content'
import type { Article } from '@/types/article'
import type { Column, ColumnEpisode } from '@/types/column'

/**
 * 站点数据的唯一来源。一切以线上为主，不缓存列表数据。
 * 启动时先空列表，等云端拉回来再填充。
 * 只缓存 manifest（含 dataVersion）用于版本变更检测。
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
  /** 本合集内的位次，来自云端 column.episodeIds 的下标 */
  rank?: number
  enabled?: boolean
  publishedAt?: string
  columnId?: string
  columnName?: string
  episodeNo?: number
  episodeTotal?: number
  accountId?: string
}

type SourceKind = 'cloud'

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
/** 同一份数据 5 分钟内不重复问 manifest，避免页面 onShow 打爆云函数 */
const MANIFEST_TTL = 5 * 60 * 1000

const SEED_APP: SiteApp = { name: '', author: '', cover: '', description: '' }
const SEED_SETTINGS: SiteSettings = { autoplayNext: true, playMode: 'sequence', showAudio: false }

/** 云端可能返回老版 settings（缺字段），一律和默认值合并，音频开关默认关 */
function normalizeSettings(raw?: SiteSettings): SiteSettings {
  return { ...SEED_SETTINGS, ...(raw || {}) }
}

const site = reactive<SiteState>({
  ready: false,
  syncing: false,
  error: '',
  source: 'cloud',
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
/**
 * 内存里到底有没有列表。
 * 冷启动时列表是空的，manifest 却可能从缓存里读出一个和线上一样的版本号，
 * 拿版本号当「数据已经在手上」的凭证就会让首次进入永远白屏，只能靠下拉刷新救。
 */
let loaded = false

/** 只缓存 manifest（含 dataVersion），用于版本变更检测和灰阶展示 */
function readManifestCache(): SiteManifest | null {
  try {
    return (uni.getStorageSync(K_MANIFEST) as SiteManifest) || null
  }
  catch {
    return null
  }
}

function writeManifestCache(manifest: SiteManifest) {
  try {
    uni.setStorageSync(K_MANIFEST, manifest)
  }
  catch {
    /* 写失败不影响使用 */
  }
}

/** 从缓存恢复 manifest，列表数据不缓存，等云端 */
function hydrate() {
  if (hydrated) {
    return
  }
  hydrated = true

  const manifest = readManifestCache()
  if (manifest) {
    site.app = manifest.app || SEED_APP
    site.settings = normalizeSettings(manifest.settings)
    site.dataVersion = Number(manifest.dataVersion || 0)
    site.updatedAt = Number(manifest.updatedAt || 0)
    site.counts = manifest.counts || site.counts
  }

  site.ready = true
}

function applyManifest(manifest: SiteManifest) {
  site.app = manifest.app || SEED_APP
  site.settings = normalizeSettings(manifest.settings)
  site.dataVersion = Number(manifest.dataVersion || 0)
  site.updatedAt = Number(manifest.updatedAt || 0)
  site.counts = manifest.counts || site.counts
  writeManifestCache(manifest)
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

  // 一切以线上为准，云端返回什么就展示什么
  site.articles = liveArticles
  site.playlist = livePlaylist
  site.columns = columns
  site.source = 'cloud'
  loaded = true
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
  if (!force && loaded && fresh)
    return Promise.resolve()

  inflight = (async () => {
    site.syncing = true
    site.error = ''
    try {
      const prevVersion = site.dataVersion
      const manifest = await fetchManifest()
      applyManifest(manifest)
      lastCheckedAt = Date.now()
      // 手上还没有列表就必须取；版本号没变才省掉这一趟
      if (force || !loaded || Number(manifest.dataVersion || 0) !== prevVersion) {
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
 * 下拉刷新专用：无视 5 分钟内不重复取数的保护，强制回云端取一遍。
 * 先等手上的请求结束，避免两条链路并发写同一份数据。
 */
export async function refreshSiteData(): Promise<void> {
  hydrate()
  if (inflight) {
    await inflight.catch(() => {})
  }
  lastCheckedAt = 0
  await ensureSiteData(true)
}

/**
 * 取单个栏目的分集。列表接口不再内联 episodes，所以按需拉取。
 * 不走缓存，每次都从云端拿，下拉刷新直接再调一次即可。
 */
export async function loadColumnEpisodes(id: string): Promise<ColumnEpisode[]> {
  try {
    const res = await fetchColumn(id)
    const list = res?.item?.episodes || []
    // 云函数改版前可能还带下架子集，客户端再挡一道
    return list.filter(one => (one as any).enabled !== false)
  }
  catch {
    return []
  }
}

/**
 * 「听文章」入口是否显示，唯一来源是线上 jy_meta 的 settings.showAudio。
 * 模块级 computed，任何页面 import 进来都是同一个响应式开关，
 * 后台手改数据库后小程序下次拉 manifest 即生效，不用重新发版。
 */
export const audioEnabled = computed(() => site.settings?.showAudio === true)

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
    refreshSiteData,
    loadColumnEpisodes,
    findColumn,
    findArticle,
    audioEnabled,
  }
}

export { site }
