<script lang="ts" setup>
import { computed, ref } from 'vue'
import articles from '@/static/data/articles.json'
import { dropDuplicateLead, parseArticleMarkdown, splitSourceFooter } from './markdown'
import playlist from '@/static/data/playlist.json'
import ArticleActions from './components/ArticleActions.vue'
import ArticleBody from './components/ArticleBody.vue'
import ArticleHeader from './components/ArticleHeader.vue'
import ArticleTopbar from './components/ArticleTopbar.vue'
import { useAudioPlayerState, setPlaylist, useAudioPlayerWithPlaylist } from '@/composables/useAudioPlayer'
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

const articleId = ref('')
const blocks = ref<ArticleBlock[]>([])
const footer = ref<ArticleBlock[]>([])
const isLoading = ref(false)
const loadError = ref('')
const pageTopPadding = ref(0)

const article = computed<Article | undefined>(() => {
  return articles.items.find(item => item.id === articleId.value) || articles.items[0]
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

const allPlaylistItems = (playlist as unknown as { items: PlaylistRecord[] }).items

// 连播只在同一栏目里跳转，否则「下一集」会串到别的系列
const playlistItems = computed<PlaylistItem[]>(() => {
  const enabled = allPlaylistItems.filter(item => item.enabled !== false)
  const columnId = article.value?.columnId
  const scoped = columnId
    ? enabled.filter(item => (item.columnId || '') === columnId)
    : []
  const list = scoped.length > 1 ? scoped : enabled

  return [...list]
    .sort((a, b) => (a.sort || 0) - (b.sort || 0))
    .map(item => ({
      id: item.id,
      title: item.title,
      audioUrl: item.audioUrl,
      duration: item.duration,
    }))
})

function startPlay() {
  if (!article.value?.audioUrl) return

  const index = playlistItems.value.findIndex(item => item.id === article.value!.id)
  if (index === -1) return

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

function setPageTopPadding() {
  const windowInfo = (uni as any).getWindowInfo?.() || uni.getSystemInfoSync()
  pageTopPadding.value = Number(windowInfo.statusBarHeight || 0)
}

function loadArticle() {
  blocks.value = []
  footer.value = []
  loadError.value = ''

  if (!article.value?.articleUrl) {
    return
  }

  isLoading.value = true
  uni.request({
    url: article.value.articleUrl,
    // 正文是 .txt，部分端会按二进制返回，兜底成字符串再解析
    dataType: 'text',
    responseType: 'text',
    success: (response: any) => {
      const raw = typeof response.data === 'string'
        ? response.data
        : String(response.data ?? '')
      const parsed = dropDuplicateLead(parseArticleMarkdown(raw), {
        title: article.value?.title,
        summary: article.value?.summary,
      })
      const split = splitSourceFooter(parsed)
      blocks.value = split.body
      footer.value = split.footer
      if (!split.body.length) {
        loadError.value = '正文内容正在整理，请稍后再试。'
      }
    },
    fail: () => {
      loadError.value = '正文加载失败，请稍后再试。'
    },
    complete: () => {
      isLoading.value = false
    },
  })
}

// 栏目标题优先展示，例如「王立群读汉武帝 · 第 35 集」
const columnLabel = computed(() => {
  const a = article.value
  if (!a?.columnName) {
    return a?.category || ''
  }
  return a.episodeNo ? `${a.columnName} · 第 ${a.episodeNo} 集` : a.columnName
})

onLoad((options) => {
  setPageTopPadding()
  articleId.value = options?.id || articles.items[0]?.id || ''
  loadArticle()
})
</script>

<template>
  <view v-if="article" class="page" :style="{ paddingTop: `${pageTopPadding}px` }">
    <ArticleTopbar :title="article.title" @back="goBack" />
    <ArticleHeader
      :category="article.category"
      :title="article.title"
      :published-at="article.publishedAt"
      :column="columnLabel"
      :source="article.accountName"
    />

    <view class="rule" />

    <!-- 听文章入口按钮 -->
    <view
      v-if="article.audioUrl"
      class="listen-entry flex items-center justify-center gap-2"
      @tap="startPlay"
    >
      <view
        class="listen-icon"
        :class="
          audioState.playing && audioState.src === article.audioUrl
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
      :article-id="article.id"
      :summary="article.summary"
      :blocks="blocks"
      :footer="footer"
      :is-loading="isLoading"
      :load-error="loadError"
    />

    <view v-if="article.columnId && article.columnName" class="column-link" @tap="openColumn">
      <view class="column-link-icon i-lucide-library" />
      <text class="column-link-text">{{ article.columnName }} · 全部剧集</text>
      <view class="column-link-arrow i-lucide-chevron-right" />
    </view>

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
