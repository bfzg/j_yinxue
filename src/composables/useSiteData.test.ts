import type { SiteManifest } from '@/api/content'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const fetchManifest = vi.fn()
const fetchAll = vi.fn()
const fetchColumns = vi.fn()
const fetchColumn = vi.fn()
const showToast = vi.fn()

vi.mock('@/api/content', () => ({
  fetchManifest: (...args: any[]) => fetchManifest(...args),
  fetchAll: (...args: any[]) => fetchAll(...args),
  fetchColumns: (...args: any[]) => fetchColumns(...args),
  fetchColumn: (...args: any[]) => fetchColumn(...args),
}))

function manifest(dataVersion: number): SiteManifest {
  return {
    app: { name: '九哥隐学', author: '', cover: '', description: '' },
    settings: { autoplayNext: true, playMode: 'sequence' },
    dataVersion,
    updatedAt: Date.now(),
    counts: { episodes: 1, columns: 1 },
  }
}

/** 节流时间戳和分集缓存都是模块级单例状态，每个用例都重新加载一遍模块 */
async function loadModule() {
  vi.resetModules()
  return import('./useSiteData')
}

beforeEach(() => {
  const store = new Map<string, any>()
  ;(globalThis as any).uni = {
    getStorageSync: (key: string) => store.get(key) ?? '',
    setStorageSync: (key: string, value: unknown) => store.set(key, value),
    showToast: (options: any) => showToast(options),
  }
  fetchManifest.mockReset()
  fetchAll.mockReset()
  fetchColumns.mockReset()
  fetchColumn.mockReset()
  showToast.mockReset()
  fetchAll.mockImplementation(async (action: string) =>
    action === 'articles'
      ? [{ id: 'ep1', enabled: true }]
      : [{ id: 'ep1', audioUrl: 'a.m4a', enabled: true }],
  )
  fetchColumns.mockResolvedValue({ items: [{ id: 'col-a', name: '大风歌', sort: 1 }] })
  fetchColumn.mockResolvedValue({ item: { id: 'col-a', episodes: [{ awemeId: 'ep1' }] } })
})

describe('站点数据取数节流', () => {
  it('5 分钟内重复进入页面只问一次 manifest', async () => {
    fetchManifest.mockResolvedValue(manifest(1))
    const { ensureSiteData, site } = await loadModule()

    await ensureSiteData()
    await ensureSiteData()

    expect(site.dataVersion).toBe(1)
    expect(fetchManifest).toHaveBeenCalledTimes(1)
    expect(fetchAll).toHaveBeenCalledTimes(2)
  })

  it('refreshSiteData 无视节流，强制回云端重取一遍', async () => {
    fetchManifest.mockResolvedValueOnce(manifest(1)).mockResolvedValueOnce(manifest(2))
    const { ensureSiteData, refreshSiteData, site } = await loadModule()

    await ensureSiteData()
    await refreshSiteData()

    expect(fetchManifest).toHaveBeenCalledTimes(2)
    expect(fetchAll).toHaveBeenCalledTimes(4)
    expect(site.dataVersion).toBe(2)
    expect(site.error).toBe('')
  })

  it('刷新撞上在途请求时串行执行，不会并发写同一份缓存', async () => {
    let release: (value: SiteManifest) => void = () => {}
    fetchManifest
      .mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
      .mockResolvedValueOnce(manifest(3))
    const { ensureSiteData, refreshSiteData, site } = await loadModule()

    const first = ensureSiteData()
    const second = refreshSiteData()
    release(manifest(1))
    await Promise.all([first, second])

    expect(fetchManifest).toHaveBeenCalledTimes(2)
    expect(fetchAll).toHaveBeenCalledTimes(4)
    expect(site.dataVersion).toBe(3)
  })

  it('冷启动哪怕 manifest 版本号没变，也要把列表取回来', async () => {
    // 上次退出时缓存过 manifest，这一轮内存里列表还是空的：
    // 曾经因为「版本号一样就当数据已在手上」，首次进入只有下拉刷新才出内容
    ;(globalThis as any).uni.setStorageSync('jy:manifest', manifest(6))
    fetchManifest.mockResolvedValue(manifest(6))
    const { ensureSiteData, site } = await loadModule()

    await ensureSiteData()

    expect(fetchColumns).toHaveBeenCalledTimes(1)
    expect(site.columns.map(one => one.id)).toEqual(['col-a'])
    expect(site.playlist).toHaveLength(1)
  })

  it('云端取数失败不锁节流，下次进入页面会继续重试', async () => {
    fetchManifest.mockRejectedValueOnce(new Error('network timeout'))
    fetchManifest.mockResolvedValue(manifest(2))
    const { ensureSiteData, site } = await loadModule()

    await ensureSiteData()
    expect(site.error).toContain('network timeout')
    expect(site.columns).toHaveLength(0)

    await ensureSiteData()
    expect(site.columns).toHaveLength(1)
  })

  it('合集分集不缓存，每次都回云端取', async () => {
    fetchManifest.mockResolvedValue(manifest(1))
    const { ensureSiteData, loadColumnEpisodes } = await loadModule()
    await ensureSiteData()

    await loadColumnEpisodes('col-a')
    await loadColumnEpisodes('col-a')
    expect(fetchColumn).toHaveBeenCalledTimes(2)
  })
})

/** useSiteRefresh 和 useSiteData 必须来自同一次模块加载，否则 site 不是同一个实例 */
async function loadBoth() {
  vi.resetModules()
  const data = await import('./useSiteData')
  const refresh = await import('./useSiteRefresh')
  return { data, refresh }
}

describe('下拉刷新流程 useSiteRefresh', () => {
  it('拉到新版本提示已更新，云端失败提示重试，两种情况都能复位', async () => {
    fetchManifest.mockResolvedValueOnce(manifest(4))
    const { data, refresh } = await loadBoth()
    const { refreshing, runRefresh } = refresh.useSiteRefresh()
    await data.ensureSiteData()
    expect(refreshing.value).toBe(false)

    // 后台刚推了新内容，版本号往上走
    fetchManifest.mockResolvedValue(manifest(5))
    await runRefresh()
    expect(refreshing.value).toBe(false)
    expect(data.site.dataVersion).toBe(5)
    expect(showToast.mock.calls.at(-1)?.[0].title).toBe('已更新到最新内容')

    fetchManifest.mockRejectedValue(new Error('network timeout'))
    await runRefresh()
    expect(refreshing.value).toBe(false)
    expect(showToast.mock.calls.at(-1)?.[0].title).toBe('更新失败，请稍后重试')
  })

  it('刷新没跑完时再次下拉，不会并发发起第二轮请求', async () => {
    fetchManifest.mockResolvedValue(manifest(1))
    const { refresh } = await loadBoth()
    const { runRefresh } = refresh.useSiteRefresh()
    await runRefresh()
    const before = fetchManifest.mock.calls.length

    // 第二次下拉撞上第一轮还在途，直接被防重入挡掉
    const first = runRefresh()
    const second = runRefresh()
    await Promise.all([first, second])

    expect(fetchManifest.mock.calls.length).toBe(before + 1)
  })

  it('版本号没动时提示没有新内容，但本页取数回调照样执行', async () => {
    fetchManifest.mockResolvedValue(manifest(1))
    const { refresh } = await loadBoth()
    const onAfter = vi.fn(async () => {})
    const { runRefresh } = refresh.useSiteRefresh(onAfter)

    await runRefresh()
    await runRefresh()

    expect(onAfter).toHaveBeenCalledTimes(2)
    expect(showToast.mock.calls.at(-1)?.[0].title).toBe('暂时没有新内容')
  })
})
