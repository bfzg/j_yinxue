import type { Article } from '@/types/article'
import type { Column, ColumnEpisode } from '@/types/column'
import { CLOUD_HTTP_PATH_CONTENT } from '@/constant/http-cloud'
import { fetchText, request } from './request'

/** 云函数统一返回 { code, message, data }，code 0 为成功 */
interface CloudEnvelope<T> {
  code: number
  message: string
  data: T
}

export interface SiteApp {
  name: string
  author: string
  cover: string
  description: string
}

export interface SiteSettings {
  autoplayNext: boolean
  playMode: string
}

export interface SiteManifest {
  app: SiteApp
  settings: SiteSettings
  dataVersion: number
  updatedAt: number
  counts: { episodes: number, columns: number }
}

export async function callContent<T = unknown>(
  action: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  const res = await request<CloudEnvelope<T>>({
    url: CLOUD_HTTP_PATH_CONTENT,
    method: 'POST',
    data: { action, ...params },
    header: { 'content-type': 'application/json' },
  })
  if (!res || Number(res.code) !== 0) {
    throw new Error(res?.message || `云端返回异常(${res?.code})`)
  }
  return res.data
}

export function fetchManifest() {
  return callContent<SiteManifest>('manifest')
}

/** articles / playlist 都是分页接口，循环取到 total 为止 */
export async function fetchAll<T = any>(action: string, pageSize = 200): Promise<T[]> {
  const items: T[] = []
  let offset = 0
  // 40 页是硬保险：数据量真到了那个量级，应该先收紧字段而不是无限翻页
  for (let page = 0; page < 40; page++) {
    const res = await callContent<{ items: T[], total: number }>(action, { offset, limit: pageSize })
    const batch = res?.items || []
    items.push(...batch)
    offset += batch.length
    if (!batch.length || offset >= Number(res?.total || 0))
      break
  }
  return items
}

export function fetchColumns() {
  return callContent<{ items: Column[] }>('columns')
}

export function fetchColumn(id: string) {
  return callContent<{ item: Column & { episodes?: ColumnEpisode[] } }>('column', { id })
}

export function fetchArticle(id: string) {
  return callContent<{ item: Article }>('article', { id })
}

export function fetchEpisodes(ids: string[]) {
  return callContent<{ items: Article[] }>('episodes', { ids: ids.join(',') })
}

/** 云函数服务端回源取正文：小程序直连拿不到时走这条通道 */
export async function fetchArticleTextByCloud(id: string): Promise<string> {
  const res = await callContent<{ text?: string }>('articleText', { id })
  const text = res?.text || ''
  if (!text.trim())
    throw new Error('云端返回的正文为空')
  return text
}

/**
 * 取正文文本：直链 → 换缓存键重试 → 云函数回源兜底。
 *
 * 三条路全断才抛错。任何一条通道拿到的都必须是真的正文，
 * 否则就会出现「把 CDN 的 403 错误 XML 当文章渲染」那种满屏乱码。
 */
export async function loadArticleText(url: string, id = ''): Promise<string> {
  const attempts: Array<() => Promise<string>> = [
    () => fetchText(url),
    () => fetchText(url, { cacheBust: true }),
  ]
  if (id)
    attempts.push(() => fetchArticleTextByCloud(id))

  let lastError: unknown = null
  for (const attempt of attempts) {
    try {
      return await attempt()
    }
    catch (err) {
      lastError = err
    }
  }
  throw lastError instanceof Error ? lastError : new Error('正文加载失败')
}
