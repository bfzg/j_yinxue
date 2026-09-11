<script lang="ts" setup>
import { computed, ref } from 'vue'
import articles from '@/static/data/articles.json'

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

interface Article {
  id: string
  title: string
  summary: string
  category: string
  publishedAt: string
  content: string[]
  enabled: boolean
  sort: number
}

const isFavorite = ref(false)

const article = computed<Article | undefined>(() => {
  const pages = getCurrentPages()
  const route = pages[pages.length - 1] as any
  const id = route?.options?.id || route?.$page?.options?.id
  return articles.items.find(item => item.id === id) || articles.items[0]
})

function formatDate(value: string) {
  return value ? value.replace(/-/g, '.') : '待更新'
}

function goBack() {
  uni.navigateBack()
}

function toggleFavorite() {
  isFavorite.value = !isFavorite.value
  uni.showToast({
    title: isFavorite.value ? '已收藏' : '已取消收藏',
    icon: 'none',
  })
}

function shareArticle() {
  uni.showToast({
    title: '请使用右上角菜单分享',
    icon: 'none',
  })
}
</script>

<template>
  <view v-if="article" class="page">
    <view class="topbar">
      <view class="back-button" @tap="goBack">
        <text>‹</text>
      </view>
      <text class="topbar-title">{{ article.title }}</text>
      <view class="topbar-space" />
    </view>

    <view class="article-head">
      <text class="category">{{ article.category }}</text>
      <text class="title">{{ article.title }}</text>
      <view class="meta">
        <text>{{ formatDate(article.publishedAt) }}</text>
        <text>九哥隐学</text>
      </view>
    </view>

    <view class="rule" />

    <view class="article-body">
      <text v-if="article.summary" class="summary">{{ article.summary }}</text>
      <text v-if="!article.content.length" class="empty-content">
        正文内容正在整理，后续将持续更新。
      </text>
      <text v-for="(paragraph, index) in article.content" :key="`${article.id}-${index}`" class="paragraph">
        {{ paragraph }}
      </text>
    </view>

    <view class="actions">
      <view class="action-button" @tap="toggleFavorite">
        <text class="action-icon">{{ isFavorite ? '♥' : '♡' }}</text>
        <text>{{ isFavorite ? '已收藏' : '收藏' }}</text>
      </view>
      <view class="action-button" @tap="shareArticle">
        <text class="action-icon">↗</text>
        <text>分享</text>
      </view>
    </view>

    <view class="end-note">
      — 全文完 —
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  box-sizing: border-box;
  padding: 0 42rpx 80rpx;
  background: #ffffff;
  color: #202622;
}

.topbar {
  display: flex;
  height: 104rpx;
  align-items: center;
  justify-content: space-between;
}

.back-button {
  display: flex;
  width: 64rpx;
  height: 64rpx;
  align-items: center;
  color: #1f5146;
  font-size: 64rpx;
  line-height: 1;
}

.topbar-title {
  max-width: 480rpx;
  overflow: hidden;
  color: #66726a;
  font-size: 25rpx;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-space {
  width: 64rpx;
}

.article-head {
  padding: 54rpx 0 38rpx;
}

.category {
  color: #d35d42;
  font-size: 24rpx;
  font-weight: 700;
  letter-spacing: 2rpx;
}

.title {
  display: block;
  margin-top: 22rpx;
  color: #18221e;
  font-size: 48rpx;
  font-weight: 800;
  line-height: 1.35;
}

.meta {
  display: flex;
  gap: 24rpx;
  margin-top: 24rpx;
  color: #99a39d;
  font-size: 23rpx;
}

.rule {
  height: 1rpx;
  background: #e5eae6;
}

.article-body {
  padding: 48rpx 0 20rpx;
}

.summary,
.empty-content,
.paragraph {
  display: block;
  color: #4c5951;
  font-size: 31rpx;
  line-height: 2;
  white-space: pre-wrap;
}

.summary {
  margin-bottom: 34rpx;
  color: #69766e;
  font-size: 28rpx;
}

.empty-content {
  color: #9aa59e;
}

.paragraph + .paragraph {
  margin-top: 34rpx;
}

.actions {
  display: flex;
  justify-content: center;
  gap: 22rpx;
  margin-top: 70rpx;
}

.action-button {
  display: flex;
  min-width: 196rpx;
  height: 76rpx;
  box-sizing: border-box;
  align-items: center;
  justify-content: center;
  gap: 10rpx;
  border: 1rpx solid #dfe7e1;
  border-radius: 38rpx;
  color: #426258;
  font-size: 25rpx;
}

.action-icon {
  font-size: 34rpx;
  line-height: 1;
}

.end-note {
  margin-top: 66rpx;
  color: #aab3ad;
  font-size: 23rpx;
  text-align: center;
}
</style>
