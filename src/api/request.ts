import { BASE_URL } from '@/constant/index'

const DEFAULT_TIMEOUT = 8000
const DEFAULT_RETRY = 2

const isAbsoluteUrl = (url = '') => /^https?:\/\//.test(url)

const wait = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

/** 换一次性 query 改缓存键，绕开 CDN 边缘节点上被缓存住的错误响应 */
export function withCacheBust(url: string) {
  return `${url}${url.includes('?') ? '&' : '?'}_jyrb=${Date.now()}`
}

/** CDN/OSS 的错误页长得和正文完全不同，这类 body 一律不能往下交给解析器 */
export function isErrorPageText(text: string) {
  const head = (text || '').replace(/^\s+/, '').slice(0, 200)
  return head.startsWith('<?xml')
    || head.startsWith('<Error>')
    || head.includes('<Code>SignatureDoesNotMatch')
}

export function buildUrl(url = '') {
  if (!url)
    return BASE_URL
  if (isAbsoluteUrl(url))
    return url
  if (url.startsWith('/'))
    return `${BASE_URL}${url}`
  return `${BASE_URL}/${url}`
}

/** uni.request 失败信息里带 timeout / network / fail，都值得再试一次 */
function shouldRetry(message: string) {
  const text = message.toLowerCase()
  return text.includes('timeout') || text.includes('network') || text.includes('fail')
}

export interface RequestOptions {
  url?: string
  method?: 'GET' | 'POST'
  data?: Record<string, unknown>
  header?: Record<string, string>
  timeout?: number
  retry?: number
}

/**
 * 统一请求出口：绝对地址与相对地址都支持，statusCode >= 400 抛错。
 * 用回调式 uni.request 包 Promise，避免不同端 Promise 返回值形态不一致。
 */
export function request<T = unknown>(options: RequestOptions = {}): Promise<T> {
  const {
    url = '',
    method = 'GET',
    data = {},
    header = {},
    timeout = DEFAULT_TIMEOUT,
    retry = DEFAULT_RETRY,
  } = options

  const requestUrl = buildUrl(url)

  const run = (remainRetry: number): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      uni.request({
        url: requestUrl,
        method,
        data,
        header,
        timeout,
        success: (res: any) => {
          const statusCode = Number(res?.statusCode || 0)
          if (statusCode >= 400) {
            reject(new Error(`请求失败(${statusCode})`))
            return
          }
          resolve(res.data as T)
        },
        fail: (err: any) => reject(new Error(err?.errMsg || '网络请求失败')),
      })
    }).catch(async (err: Error) => {
      if (remainRetry > 0 && shouldRetry(err.message)) {
        await wait(1200)
        return run(remainRetry - 1)
      }
      throw err
    })

  return run(retry)
}

/**
 * 取纯文本（正文 .txt 直链）。
 *
 * 微信对 4xx 也走 success 回调，所以 statusCode 必须自己判；
 * 错误页 XML、空 body 同样按失败抛出，交给上层换通道重试。
 */
export function fetchText(url: string, options: { cacheBust?: boolean, timeout?: number } = {}): Promise<string> {
  const { cacheBust = false, timeout = DEFAULT_TIMEOUT } = options
  const requestUrl = cacheBust ? withCacheBust(buildUrl(url)) : buildUrl(url)

  return new Promise<string>((resolve, reject) => {
    uni.request({
      url: requestUrl,
      method: 'GET',
      timeout,
      dataType: 'text',
      responseType: 'text',
      success: (res: any) => {
        const statusCode = Number(res?.statusCode || 0)
        const raw = typeof res?.data === 'string' ? res.data : String(res?.data ?? '')
        if (statusCode !== 200) {
          reject(new Error(`正文请求失败(${statusCode})`))
        }
        else if (isErrorPageText(raw)) {
          reject(new Error('正文地址返回了错误页'))
        }
        else if (!raw.trim()) {
          reject(new Error('正文为空'))
        }
        else {
          resolve(raw)
        }
      },
      fail: (err: any) => reject(new Error(err?.errMsg || '网络请求失败')),
    })
  })
}
