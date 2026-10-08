/** 一集在合集里的可排序字段：rank 由云端按栏目 episodeIds 算出，就是列表页从上到下的位次 */
export interface EpisodeOrderFields {
  rank?: number
  episodeNo?: number
  sort?: number
}

/** 没被任何栏目 episodeIds 引用的集排到本合集最后 */
export const UNRANKED = 1e9

function rankValue(item: EpisodeOrderFields) {
  const rank = Number(item.rank)
  return Number.isFinite(rank) && rank >= 0 ? rank : UNRANKED
}

/**
 * 合集内连播的顺序。
 *
 * sort 存的是「-视频发布时间」，单靠它排序会把整季倒过来播，
 * 所以顺序一律以云端给的 rank 为准，缺 rank（旧版云函数）再退回集号。
 */
export function compareEpisodeOrder(a: EpisodeOrderFields, b: EpisodeOrderFields) {
  const byRank = rankValue(a) - rankValue(b)
  if (byRank !== 0) {
    return byRank
  }

  const byNo = (Number(a.episodeNo) || 0) - (Number(b.episodeNo) || 0)
  if (byNo !== 0) {
    return byNo
  }

  return (Number(a.sort) || 0) - (Number(b.sort) || 0)
}

/** 返回排好序的新数组，不改原数组 */
export function sortByEpisodeOrder<T extends EpisodeOrderFields>(list: T[]): T[] {
  return [...list].sort(compareEpisodeOrder)
}

/**
 * 给用户看的「第几集」：永远是本合集内的第 1、2、3 集。
 *
 * episode_no 在库里是采集/AI 归类时的原始编号，单篇合集甚至直接沿用账号作品
 * 列表的位置（275、331 这种），拿它当集号会莫名其妙，所以优先用云端位次。
 */
export function episodeDisplayNo(item: EpisodeOrderFields): number {
  const rank = Number(item.rank)
  if (Number.isFinite(rank) && rank >= 0 && rank < UNRANKED) {
    return rank + 1
  }
  return Number(item.episodeNo) || 0
}
