import { ref } from 'vue'
import { refreshSiteData, useSiteData } from './useSiteData'

/**
 * 三个页面共用的下拉刷新流程：防重入 → 强制回云端取数 → 补一次本页取数 → 给结果提示。
 *
 * onAfter 用来在站点元数据更新完之后，再走一遍本页自己的取数
 * （合集页要重取分集，阅读页要重取正文），这样刷新才算真的完成。
 *
 * ensureSiteData 内部把异常吞成了 site.error，这里得自己看一眼，
 * 否则刷新失败也会提示「暂时没有新内容」，后台刚上架的内容到底到没到就说不清了。
 */
export function useSiteRefresh(onAfter?: () => unknown) {
  const refreshing = ref(false)
  const { site } = useSiteData()

  async function runRefresh() {
    if (refreshing.value) {
      return
    }
    refreshing.value = true

    const before = Number(site.dataVersion || 0)
    try {
      await refreshSiteData()
      if (site.error) {
        throw new Error(site.error)
      }
      await onAfter?.()
      uni.showToast({
        title: Number(site.dataVersion || 0) > before ? '已更新到最新内容' : '暂时没有新内容',
        icon: 'none',
      })
    }
    catch {
      uni.showToast({ title: '更新失败，请稍后重试', icon: 'none' })
    }
    finally {
      refreshing.value = false
    }
  }

  return { refreshing, runRefresh }
}
