/**
 * uniCloud（支付宝云）HTTP 入口。
 *
 * 数据全部走云函数 URL 化，正文/音频走云存储永久直链，
 * 小程序不再打包 static/data/*.json，也不再手工替换 json。
 */

/** 云函数 URL 化域名（服务空间 env-00jxu1ytdn0v） */
export const CLOUD_BASE_URL_PROD = 'https://env-00jxu1ytdn0v.dev-hz.cloudbasefunction.cn'

/** H5 开发态用 vite 代理绕开 CORS，其它端直连 */
export const CLOUD_DEV_PROXY = '/unicloud-api'

function resolveBaseUrl(): string {
  const direct = import.meta.env.VITE_UNICLOUD_BASEURL || CLOUD_BASE_URL_PROD
  const isH5 = typeof window !== 'undefined' && typeof document !== 'undefined'
  if (isH5 && import.meta.env.DEV) {
    return import.meta.env.VITE_UNICLOUD_PROXY || CLOUD_DEV_PROXY
  }
  return direct
}

export const BASE_URL = resolveBaseUrl()

export { CLOUD_HTTP_PATH_CONTENT } from './http-cloud'
