import { describe, expect, it } from 'vitest'
import { dropDuplicateLead, parseArticleMarkdown, splitSourceFooter } from './markdown'

const MD = `# 大风歌：刘邦的一生

> 一个亭主如何在大时代里完成阶层跃迁。

## 起点

第一段正文。
带 **加粗** 和 \`代码\` 的行内标记。

## 转折

第二段正文。

- 要点一
- 要点二

## 值得记住的一句

> 大丈夫当如此也。

---

- 栏目：大风歌 · 第 3 集
- 时长：34 分钟
- 原视频：https://www.douyin.com/video/123
`

describe('parseArticleMarkdown', () => {
  it('把受限 Markdown 解析成排版块', () => {
    const blocks = parseArticleMarkdown(MD)
    expect(blocks[0]).toEqual({ type: 'heading', text: '大风歌：刘邦的一生' })
    expect(blocks[1]).toEqual({ type: 'quote', text: '一个亭主如何在大时代里完成阶层跃迁。' })
    expect(blocks.filter(b => b.type === 'heading').map(b => (b as any).text))
      .toEqual(['大风歌：刘邦的一生', '起点', '转折', '值得记住的一句'])
    expect(blocks).toContainEqual({ type: 'list', items: ['要点一', '要点二'] })
    expect(blocks.some(b => b.type === 'divider')).toBe(true)
  })

  it('去掉行内加粗与代码标记，小程序 text 里也能正常显示', () => {
    const para = parseArticleMarkdown(MD).find(b => (b as any).text?.includes('行内标记'))
    expect(para).toEqual({ type: 'paragraph', text: '带 加粗 和 代码 的行内标记。' })
  })

  it('空行与空文本不产生垃圾块', () => {
    expect(parseArticleMarkdown('')).toEqual([])
    expect(parseArticleMarkdown('\n\n   \n')).toEqual([])
  })
})

describe('splitSourceFooter', () => {
  it('把末尾「出处」列表从正文里摘出去', () => {
    const { body, footer } = splitSourceFooter(parseArticleMarkdown(MD))
    expect(footer).toHaveLength(1)
    expect(footer[0].type).toBe('list')
    expect((footer[0] as any).items[0]).toContain('大风歌')
    expect(body.some(b => b.type === 'divider')).toBe(false)
    expect(body.some(b => (b as any).text === '大风歌 · 第 3 集')).toBe(false)
  })

  it('没有分隔线时正文完整保留', () => {
    const { body, footer } = splitSourceFooter(parseArticleMarkdown('## 标题\n\n正文'))
    expect(footer).toEqual([])
    expect(body).toHaveLength(2)
  })
})

describe('dropDuplicateLead', () => {
  it('丢掉与页头标题重复的一级标题和与摘要重复的导语引用', () => {
    const blocks = parseArticleMarkdown(MD)
    const trimmed = dropDuplicateLead(blocks, {
      title: '大风歌：刘邦的一生',
      summary: '一个亭主如何在大时代里完成阶层跃迁。',
    })
    expect(trimmed[0]).toEqual({ type: 'heading', text: '起点' })
    expect(trimmed.some(b => b.type === 'quote' && (b as any).text.includes('阶层跃迁'))).toBe(false)
  })

  it('正文里的金句引用不会被误删', () => {
    const blocks = parseArticleMarkdown(MD)
    const trimmed = dropDuplicateLead(blocks, {
      title: '大风歌：刘邦的一生',
      summary: '一个亭主如何在大时代里完成阶层跃迁。',
    })
    expect(trimmed.some(b => b.type === 'quote' && (b as any).text.includes('大丈夫当如此也'))).toBe(true)
  })

  it('摘要被截断时也能对上整段导语', () => {
    const md = '> 寒门突围不是靠运气，而是闯过血脉囚笼、情爱泥潭、无用围城、绝境选择与自我破茧五道铁闸，每一道都要脱一层皮。\n\n## 第一道关\n\n正文。'
    const trimmed = dropDuplicateLead(parseArticleMarkdown(md), {
      title: '人生五道关：白手起家者的破局之路',
      summary: '寒门突围不是靠运气，而是闯过血脉囚笼、情爱泥潭、无用围城、绝境选择与自我破茧五道铁',
    })
    expect(trimmed[0]).toEqual({ type: 'heading', text: '第一道关' })
  })

  it('标题对不上时保持原样，短引用也不至于被误判', () => {
    const blocks = parseArticleMarkdown('> 天地有大美而不言。\n\n正文。')
    const trimmed = dropDuplicateLead(blocks, { title: '另一篇文章', summary: '完全不同的摘要' })
    expect(trimmed).toEqual(blocks)
  })

  it('缺少 title 与 summary 时直接返回原数组', () => {
    const blocks = parseArticleMarkdown(MD)
    expect(dropDuplicateLead(blocks, {})).toBe(blocks)
  })
})
