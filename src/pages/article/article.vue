<script lang="ts" setup>
import { computed, ref } from 'vue'
import articles from '@/static/data/articles.json'
import playlist from '@/static/data/playlist.json'
import ArticleActions from './components/ArticleActions.vue'
import ArticleBody from './components/ArticleBody.vue'
import ArticleHeader from './components/ArticleHeader.vue'
import ArticleTopbar from './components/ArticleTopbar.vue'
import { useAudioPlayerState, setPlaylist, useAudioPlayerWithPlaylist } from '@/composables/useAudioPlayer'
import type { Article } from '@/types/article'
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
const paragraphs = ref<string[]>([])
const isLoading = ref(false)
const loadError = ref('')
const pageTopPadding = ref(0)

const article = computed<Article | undefined>(() => {
  return articles.items.find(item => item.id === articleId.value) || articles.items[0]
})

const { state: audioState, toggle, playByIndex } = useAudioPlayerWithPlaylist()

// 构建播放列表（按 sort 排序）
const playlistItems = computed<PlaylistItem[]>(() => {
  return playlist.items
    .filter(item => item.enabled !== false)
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

function setPageTopPadding() {
  const windowInfo = (uni as any).getWindowInfo?.() || uni.getSystemInfoSync()
  pageTopPadding.value = Number(windowInfo.statusBarHeight || 0)
}

function loadArticle() {
  paragraphs.value = []
  loadError.value = ''

  if (!article.value?.articleUrl) {
    return
  }

  isLoading.value = true
  uni.request({
    url: article.value.articleUrl,
    success: (response: any) => {
      const text = typeof response.data === 'string' ? response.data : ''
      paragraphs.value = text
        .replace(/\r\n/g, '\n')
        .split(/\n\s*\n/)
        .map(paragraph => paragraph.trim())
        .filter(Boolean)
    },
    fail: () => {
      loadError.value = '正文加载失败，请稍后再试。'
    },
    complete: () => {
      isLoading.value = false
    },
  })
}

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
      :paragraphs="paragraphs"
      :is-loading="isLoading"
      :load-error="loadError"
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

.end-note {
  margin-top: 66rpx;
  color: #aab3ad;
  font-size: 23rpx;
  text-align: center;
}
</style>
