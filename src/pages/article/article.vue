<script lang="ts" setup>
import { computed, ref } from 'vue'
import { dropDuplicateLead, parseArticleMarkdown, splitSourceFooter } from './markdown'
import ArticleActions from './components/ArticleActions.vue'
import ArticleBody from './components/ArticleBody.vue'
import ArticleHeader from './components/ArticleHeader.vue'
import ArticleTopbar from './components/ArticleTopbar.vue'
import { loadArticleText } from '@/api/content'
import { setPlaylist, useAudioPlayerWithPlaylist } from '@/composables/useAudioPlayer'
import { useCapsuleInset, useTopInset } from '@/composables/useSafeArea'
import { ensureSiteData, useSiteData } from '@/composables/useSiteData'
import type { Article, ArticleBlock } from '@/types/article'
import type { PlaylistItem } from '@/composables/useAudioPlayer'

defineOptions({
  name: 'ArticleDetail',
})

definePage({
  style: {
    navigationStyle: 'custom',
    navigationBarTitleText: '',
    navigationBarBackgroundColor: '#ffffff',
    navigationBarTextStyle: 'black',
  },
})

const { site } = useSiteData()

const articleId = ref('')
const blocks = ref<ArticleBlock[]>([])
const isLoading = ref(false)
const loadError = ref('')

const topInset = useTopInset(12)
const capsuleInset = useCapsuleInset()

const article = computed<Article | undefined>(() => {
  return site.articles.find(item => item.id === articleId.value) || site.articles[0]
})

const { state: audioState, toggle, playByIndex } = useAudioPlayerWithPlaylist()

interface PlaylistRecord {
  id: string
  title: string
  audioUrl: string
  duration?: number
  sort?: number
  enabled?: boolean
  columnId?: string
}

const allPlaylistItems = site.playlist as PlaylistRecord[]

function toPlaylistItem(item: { id: string, title: string, audioUrl: string, duration?: number }): PlaylistItem {
  return { id: item.id, title: item.title, audioUrl: item.audioUrl, duration: item.duration }
}

function bySort(a: { sort?: number }, b: { sort?: number }) {
  return (a.sort || 0) - (b.sort || 0)
}

/**
 * 连播列表默认就是本篇所在的合集，按合集顺序播到最后一集为止。
 * 文章不在 playlist 里（刚发布、字段缺失）时补上自己，避免点了没反应。
 */
const playlistItems = computed<PlaylistItem[]>(() => {
  const current = article.value
  const withAudio = allPlaylistItems.filter(item => item.enabled !== false && !!item.audioUrl)
  const columnId = current?.columnId
  const scoped: PlaylistRecord[] = columnId
    ? withAudio.filter(item => (item.columnId || '') === columnId)
    : withAudio

  const list = [...scoped]
  if (current?.audioUrl && !list.some(item => item.id === current.id)) {
    list.push({
      id: current.id,
      title: current.title,
      audioUrl: current.audioUrl,
      duration: current.duration,
      sort: current.sort,
      columnId: current.columnId,
    })
  }
  return list.sort(bySort).map(toPlaylistItem)
})

function startPlay() {
  if (!article.value?.audioUrl)
    return

  const index = playlistItems.value.findIndex(item => item.id === article.value!.id)
  if (index === -1) {
    uni.showToast({ title: '这一集暂时没有音频', icon: 'none' })
    return
  }

  // 正在播放当前文章 → 暂停/恢复
  if (audioState.started && audioState.src === article.value.audioUrl) {
    toggle()
    return
  }

  // 设置播放列表并播放所选文章（自动处理切换场景）
  setPlaylist(playlistItems.value, index)
  playByIndex(index)
}

function goBack() {
  uni.navigateBack()
}

function openColumn() {
  const columnId = article.value?.columnId
  if (!columnId) {
    return
  }
  uni.navigateTo({ url: `/pages/column/column?id=${encodeURIComponent(columnId)}` })
}

/** 拿到文本才解析：走不到这里的内容一律不进 blocks */
function applyRawText(raw: string) {
  const parsed = dropDuplicateLead(parseArticleMarkdown(raw), {
    title: article.value?.title,
    summary: article.value?.summary,
  })
  // 尾注（栏目 / 时长 / 原视频）不再展示：正文生成时已去掉，
  // 这里再挡一道，防 CDN 上还挂着改版前的旧文件
  blocks.value = splitSourceFooter(parsed).body
  if (!blocks.value.length) {
    loadError.value = '正文内容正在整理，请稍后再试。'
  }
}

async function loadArticle() {
  blocks.value = []
  loadError.value = ''

  const current = article.value
  if (!current?.articleUrl) {
    return
  }

  isLoading.value = true
  try {
    applyRawText(await loadArticleText(current.articleUrl, current.id))
  }
  catch {
    // 直链、换键重试、云函数代取三条路都不通，只显示提示，绝不把错误页当正文渲染
    loadError.value = '正文加载失败，点此重试。'
  }
  finally {
    isLoading.value = false
  }
}

/** 只在失败态生效：点正文区域重新走一遍三级通道 */
function retryLoad() {
  if (loadError.value && !isLoading.value) {
    void loadArticle()
  }
}

// 栏目标题优先展示，例如「王立群读汉武帝 · 第 35 集」
const columnLabel = computed(() => {
  const a = article.value
  if (!a?.columnName) {
    return a?.category || ''
  }
  return a.episodeNo ? `${a.columnName} · 第 ${a.episodeNo} 集` : a.columnName
})

onLoad(async (options) => {
  // 冷启直接进详情页时本地可能还是兜底数据，先把云端元数据对齐再取正文
  await ensureSiteData()
  articleId.value = options?.id || site.articles[0]?.id || ''
  void loadArticle()
})
</script>

<template>
  <view v-if="article" class="page" :style="{ paddingTop: topInset }">
    <ArticleTopbar :title="article.title" :right-inset="capsuleInset" @back="goBack" />
    <ArticleHeader
      :category="article.category" :title="article.title" :published-at="article.publishedAt"
      :column="columnLabel" :source="article.accountName"
    />

    <view class="rule" />

    <!-- 听文章入口按钮 -->
    <view v-if="article.audioUrl" class="listen-entry flex items-center justify-center gap-2" @tap="startPlay">
      <view
        class="listen-icon" :class="audioState.playing && audioState.src === article.audioUrl
          ? 'i-lucide-pause'
          : 'i-lucide-volume-2'"
      />
      <view class="text-base">
        {{
          audioState.playing && audioState.src === article.audioUrl
            ? '暂停听文章'
            : '听文章'
        }}
      </view>
    </view>

    <ArticleBody
      :article-id="article.id" :summary="article.summary" :blocks="blocks"
      :is-loading="isLoading" :load-error="loadError" @tap="retryLoad"
    />

    <ArticleActions />

    <view class="end-note">
      — 全文完 —
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  box-sizing: border-box;
  padding: 0 42rpx 220rpx;
  background: #ffffff;
  color: #202622;
}

.rule {
  height: 1rpx;
  background: #e5eae6;
}

.listen-entry {
  width: 100%;
  height: 78rpx;
  box-sizing: border-box;
  margin-top: 38rpx;
  border-radius: 999rpx;
  background: #f6faf7;
  color: #1f5146;
}

.listen-icon {
  width: 38rpx;
  height: 38rpx;
}

.listen-hint {
  color: #7d8a82;
  font-size: 21rpx;
}

.column-link {
  display: flex;
  align-items: center;
  gap: 14rpx;
  margin-top: 54rpx;
  padding: 26rpx 28rpx;
  border: 1rpx solid #e4e9e4;
  border-radius: 16rpx;
  background: #f7f9f7;
}

.column-link:active {
  background: #eef4ef;
}

.column-link-icon,
.column-link-arrow {
  flex-shrink: 0;
  width: 30rpx;
  height: 30rpx;
  color: #1f5146;
}

.column-link-text {
  overflow: hidden;
  flex: 1;
  color: #1f5146;
  font-size: 26rpx;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.end-note {
  margin-top: 66rpx;
  color: #aab3ad;
  font-size: 23rpx;
  text-align: center;
}
</style>
