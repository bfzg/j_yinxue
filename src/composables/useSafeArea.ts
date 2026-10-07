import { computed } from 'vue'

/**
 * 顶部安全距离。
 *
 * 三个页面都用了自定义导航栏（navigationStyle: custom），内容会顶到状态栏底下，
 * 所以标题栏必须自己让出状态栏的高度。env(safe-area-inset-top) 在安卓小程序上
 * 不稳，--status-bar-height 也不保证是真实值，这里直接读系统信息。
 */

let cachedStatusBar = -1
let cachedCapsule = -1

function readWindowInfo(): any {
  try {
    return (uni as any).getWindowInfo?.() || uni.getSystemInfoSync()
  }
  catch {
    return {}
  }
}

/** 状态栏高度（px），H5 拿不到就是 0 */
export function statusBarHeight(): number {
  if (cachedStatusBar < 0) {
    cachedStatusBar = Number(readWindowInfo().statusBarHeight || 0) || 0
  }
  return cachedStatusBar
}

/**
 * 标题栏上方需要让出的高度（px）。
 * H5 没有状态栏，用 fallback 兜底，保证预览时内容不会贴着浏览器顶部。
 */
export function topInset(extra = 10, fallback = 16): number {
  const height = statusBarHeight()
  return height > 0 ? height + extra : fallback
}

/**
 * 右上角胶囊按钮需要预留的宽度（px），避免标题文字被胶囊盖住。
 * H5 上没有胶囊，返回 0。
 */
export function capsuleInset(): number {
  if (cachedCapsule < 0) {
    let value = 0
    const wxApi = (typeof wx !== 'undefined' ? wx : null) as any
    const windowWidth = Number(readWindowInfo().windowWidth || 0)
    try {
      const rect = wxApi?.getMenuButtonBoundingClientRect?.()
      if (rect?.left && windowWidth) {
        value = Math.max(0, Math.round(windowWidth - rect.left))
      }
    }
    catch {
      value = 0
    }
    cachedCapsule = value
  }
  return cachedCapsule
}

export function useTopInset(extra = 10, fallback = 16) {
  return computed(() => `${topInset(extra, fallback)}px`)
}

export function useCapsuleInset() {
  return computed(() => `${capsuleInset()}px`)
}
