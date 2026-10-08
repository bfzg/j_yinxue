import { describe, expect, it } from 'vitest'
import { compareEpisodeOrder, episodeDisplayNo, sortByEpisodeOrder } from './episodeOrder'

describe('合集内集序', () => {
  it('按云端位次排，不受 sort（负时间戳，最新在前）影响', () => {
    const list = [
      { id: 'e3', rank: 2, sort: -100 },
      { id: 'e1', rank: 0, sort: -300 },
      { id: 'e2', rank: 1, sort: -200 },
    ]
    expect(sortByEpisodeOrder(list).map(one => one.id)).toEqual(['e1', 'e2', 'e3'])
  })

  it('缺 rank（旧版云函数）时退回集号', () => {
    const list = [{ id: 'b', episodeNo: 9 }, { id: 'a', episodeNo: 3 }]
    expect(compareEpisodeOrder(list[0], list[1])).toBeGreaterThan(0)
    expect(sortByEpisodeOrder(list).map(one => one.id)).toEqual(['a', 'b'])
  })

  it('单篇合集集号全是 0，最后还能按 sort 稳定排出一个顺序', () => {
    const list = [{ id: 'x', episodeNo: 0, sort: 5 }, { id: 'y', episodeNo: 0, sort: 1 }]
    expect(sortByEpisodeOrder(list).map(one => one.id)).toEqual(['y', 'x'])
  })

  it('没被任何栏目引用的集排到合集最后', () => {
    const ranked = { id: 'a', rank: 3 }
    const unranked = { id: 'b', rank: undefined }
    expect(sortByEpisodeOrder([unranked, ranked]).map(one => one.id)).toEqual(['a', 'b'])
  })
})

describe('给用户看的第几集', () => {
  it('用合集内位次，不用采集时的原始编号', () => {
    expect(episodeDisplayNo({ rank: 0, episodeNo: 275 })).toBe(1)
    expect(episodeDisplayNo({ rank: 2, episodeNo: 357 })).toBe(3)
  })

  it('云函数还没返回 rank 时退回集号，都没有就是 0', () => {
    expect(episodeDisplayNo({ episodeNo: 12 })).toBe(12)
    expect(episodeDisplayNo({})).toBe(0)
  })
})
