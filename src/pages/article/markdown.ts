import type { ArticleBlock } from '@/types/article'

/**
 * 采集流水线产出的是受限 Markdown（见 scripts/douyin/article_formatter.py）：
 *   # 标题 / ## 小标题 / 正文段落 / > 导语或金句 / - 要点 / --- 分隔线
 * uni-app 里用 rich-text 渲染整段 HTML 在各端表现不一致，所以这里只把
 * 行解析成结构化块，交给 ArticleBody 用原生 view/text 排版。
 */

const HEADING_RE = /^#{1,3}\s+/
const QUOTE_RE = /^>\s?/
const LIST_RE = /^[-*]\s+/
const DIVIDER_RE = /^(?:-{3,}|\*{3,})$/

/** 行内 **加粗** 在小程序 text 里没法局部加粗，统一去掉标记保留文字 */
function inline(text: string) {
  return text
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .trim()
}

export function parseArticleMarkdown(source: string): ArticleBlock[] {
  const blocks: ArticleBlock[] = []
  let list: string[] = []

  const flushList = () => {
    if (list.length) {
      blocks.push({ type: 'list', items: list })
      list = []
    }
  }

  for (const rawLine of source.replace(/\r\n/g, '\n').split('\n')) {
    const line = rawLine.trim()

    if (!line) {
      flushList()
      continue
    }
    if (DIVIDER_RE.test(line)) {
      flushList()
      blocks.push({ type: 'divider' })
      continue
    }
    if (line.startsWith('- ') || line.startsWith('* ')) {
      list.push(inline(line.replace(LIST_RE, '')))
      continue
    }
    flushList()

    if (QUOTE_RE.test(line)) {
      blocks.push({ type: 'quote', text: inline(line.replace(QUOTE_RE, '')) })
    }
    else if (HEADING_RE.test(line)) {
      blocks.push({ type: 'heading', text: inline(line.replace(HEADING_RE, '')) })
    }
    else {
      blocks.push({ type: 'paragraph', text: inline(line) })
    }
  }

  flushList()
  return blocks
}

/** 归一化：去掉空白与中文标点，只做「是不是同一句话」的判断 */
function normalize(text: string) {
  return text
    .replace(/[\s，。：；、！？“”‘’"'（）()【】《》…—～·,.:;!?\-]/g, '')
    .toLowerCase()
}

/**
 * 去掉正文开头与页头重复的内容。
 * 导出时 title 取自正文首个一级标题、summary 取自首个引用（见 scripts/douyin/exporter.py），
 * 所以这两块在文章页会出现两次，这里在渲染前丢弃。
 * 只扫描开头几块，正文里同名的二级标题不动。
 */
export function dropDuplicateLead(
  blocks: ArticleBlock[],
  opts: { title?: string, summary?: string },
): ArticleBlock[] {
  const title = normalize(opts.title || '')
  const summary = normalize(opts.summary || '')
  if (!title && !summary) {
    return blocks
  }

  const out: ArticleBlock[] = []
  blocks.forEach((block, i) => {
    if (i < 4 && block.type === 'heading' && title && normalize(block.text) === title) {
      return
    }
    if (i < 4 && block.type === 'quote' && summary) {
      const text = normalize(block.text)
      const same = text === summary
        || (text.length > 12 && summary.startsWith(text))
        || (summary.length > 12 && text.startsWith(summary))
      if (same) {
        return
      }
    }
    out.push(block)
  })
  return out
}

/**
 * 末尾的「出处」块（栏目 / 时长 / 原视频）从正文里摘掉。
 * 新版文章已经不写这段了，留着是因为 CDN 上可能还缓存着改版前的旧文件。
 */
export function splitSourceFooter(blocks: ArticleBlock[]) {
  const lastDivider = blocks.reduce((acc, b, i) => (b.type === 'divider' ? i : acc), -1)
  if (lastDivider === -1) {
    return { body: blocks, footer: [] as ArticleBlock[] }
  }
  const tail = blocks.slice(lastDivider + 1)
  const isFooter = tail.some(b => b.type === 'list')
  if (!isFooter) {
    return { body: blocks, footer: [] as ArticleBlock[] }
  }
  return { body: blocks.slice(0, lastDivider), footer: tail }
}
