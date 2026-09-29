<script lang="ts" setup>
import { computed, ref } from 'vue'
import articles from '@/static/data/articles.json'
import ArticleActions from './components/ArticleActions.vue'
import ArticleAudioPlayer from './components/ArticleAudioPlayer.vue'
import ArticleBody from './components/ArticleBody.vue'
import ArticleHeader from './components/ArticleHeader.vue'
import ArticleTopbar from './components/ArticleTopbar.vue'
import type { Article } from '@/types/article'

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

    <ArticleAudioPlayer
      v-if="article.audioUrl"
      :src="article.audioUrl"
      :title="article.title"
    />

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

.end-note {
  margin-top: 66rpx;
  color: #aab3ad;
  font-size: 23rpx;
  text-align: center;
}
</style>
