/** 秒 → 人话时长，列表里避免出现「2038 秒」这种数字 */
export function formatDuration(seconds?: number): string {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  if (!total) {
    return '时长待补'
  }
  if (total < 60) {
    return `${total} 秒`
  }

  const minutes = Math.round(total / 60)
  if (minutes < 60) {
    return `${minutes} 分钟`
  }

  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`
}

/** 2026-09-10 → 2026.09.10，与首页卡片保持一致 */
export function formatDate(value?: string): string {
  return value ? value.replace(/-/g, '.') : ''
}
