<script lang="ts" setup>
import { computed, ref } from 'vue'
import articles from '@/static/data/articles.json'

defineOptions({
  name: 'Home',
})

definePage({
  type: 'home',
  style: {
    navigationStyle: 'custom',
    navigationBarTitleText: '',
    navigationBarBackgroundColor: '#f3f5f2',
    navigationBarTextStyle: 'black',
  },
})

interface Article {
  id: string
  title: string
  summary: string
  category: string
  cover: string
  publishedAt: string
  content: string[]
  enabled: boolean
  sort: number
}

const searchText = ref('')
const isSearchFocused = ref(false)
const activeCategory = ref('全部')

const articleItems = computed<Article[]>(() => {
  const keyword = searchText.value.trim().toLowerCase()
  return [...articles.items]
    .filter(item => item.enabled)
    .filter(item => activeCategory.value === '全部' || item.category === activeCategory.value)
    .filter((item) => {
      if (!keyword) {
        return true
      }
      return `${item.title}${item.summary}${item.content.join(' ')}`.toLowerCase().includes(keyword)
    })
    .sort((a, b) => a.sort - b.sort)
})

const categories = computed(() => [
  '全部',
  ...Array.from(new Set(articles.items.filter(item => item.enabled).map(item => item.category))),
])

function openArticle(article: Article) {
  uni.navigateTo({
    url: `/pages/article/article?id=${article.id}`,
  })
}

function formatDate(value: string) {
  return value ? value.replace(/-/g, '.') : '待更新'
}
</script>

<template>
  <view class="page">
    <view class="top-space" />

    <view class="search-row">
      <view class="search-box" :class="{ focused: isSearchFocused }">
        <text class="search-icon">⌕</text>
        <input
          v-model="searchText" class="search-input" confirm-type="search" placeholder="搜索文章"
          placeholder-class="search-placeholder" @focus="isSearchFocused = true" @blur="isSearchFocused = false"
        >
        <text v-if="searchText" class="clear-button" @tap="searchText = ''">×</text>
      </view>
    </view>

    <view class="intro">
      <text class="headline">九哥隐学</text>
      <text class="intro-copy">用一段安静的阅读时间，整理认知，也整理自己。</text>
    </view>

    <scroll-view class="category-scroll" scroll-x :show-scrollbar="false">
      <view class="category-list">
        <text
          v-for="category in categories" :key="category" class="category-item"
          :class="{ selected: activeCategory === category }" @tap="activeCategory = category"
        >
          {{ category }}
        </text>
      </view>
    </scroll-view>

    <view class="section-head">
      <view>
        <text class="section-title">共 {{ articleItems.length }} 篇</text>
      </view>
      <text class="section-note">按时间更新</text>
    </view>

    <view v-if="articleItems.length" class="article-list">
      <view v-for="(article, index) in articleItems" :key="article.id" class="article-card" @tap="openArticle(article)">
        <view class="article-number">
          {{ String(index + 1).padStart(2, '0') }}
        </view>
        <view class="article-content">
          <view class="article-meta">
            <text>{{ article.category }}</text>
            <text>{{ formatDate(article.publishedAt) }}</text>
          </view>
          <text class="article-title">{{ article.title }}</text>
          <text class="article-summary">{{ article.summary || '打开文章，开始阅读。' }}</text>
          <view class="read-link">
            <text>阅读全文</text>
            <text class="read-arrow">→</text>
          </view>
        </view>
      </view>
    </view>

    <view v-else class="empty-state">
      <text class="empty-title">没有找到相关文章</text>
      <text class="empty-copy">换个关键词试试。</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  box-sizing: border-box;
  padding: 0 32rpx 72rpx;
  background: #f3f5f2;
  color: #18221e;
}

.top-space {
  height: 92rpx;
}

.search-row {
  display: flex;
}

.intro {
  display: flex;
  flex-direction: column;
  padding: 30rpx 0 0rpx;
}

.headline {
  max-width: 600rpx;
  margin-top: 18rpx;
  font-size: 54rpx;
  font-weight: 800;
  line-height: 1.18;
}

.intro-copy {
  max-width: 600rpx;
  margin-top: 18rpx;
  color: #718078;
  font-size: 27rpx;
  line-height: 1.6;
}

.search-box {
  display: flex;
  align-items: center;
  width: 260rpx;
  height: 72rpx;
  box-sizing: border-box;
  padding: 0 24rpx;
  border: 1rpx solid #dfe6e1;
  border-radius: 999rpx;
  background: #ffffff;
  box-shadow: 0 12rpx 30rpx rgba(43, 67, 57, 0.05);
  transition: width 240ms ease;
}

.search-box.focused {
  width: 450rpx;
}

.search-icon {
  width: 38rpx;
  color: #66746c;
  font-size: 64rpx;
  line-height: 1;
  transform: rotate(-20deg);
  padding-bottom: 4rpx;
}

.search-input {
  flex: 1;
  height: 72rpx;
  margin-left: 12rpx;
  color: #18221e;
  font-size: 28rpx;
}

.search-placeholder {
  color: #a4ada8;
}

.clear-button {
  width: 44rpx;
  color: #8d9891;
  font-size: 40rpx;
  line-height: 1;
  text-align: center;
}

.category-scroll {
  margin: 38rpx -32rpx 0;
  white-space: nowrap;
}

.category-list {
  display: inline-flex;
  gap: 42rpx;
  padding: 0 32rpx 12rpx;
}

.category-item {
  position: relative;
  color: #89938d;
  font-size: 32rpx;
  font-weight: 500;
  line-height: 1.6;
}

.category-item.selected {
  color: #1f5146;
  font-weight: 700;
}

.category-item.selected::after {
  position: absolute;
  right: 0;
  bottom: -12rpx;
  left: 0;
  height: 6rpx;
  border-radius: 6rpx;
  background: #1f5146;
  content: '';
}

.section-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin: 52rpx 0 22rpx;
}

.section-head > view {
  display: flex;
  align-items: baseline;
  gap: 16rpx;
}

.section-title {
  font-size: 38rpx;
  font-weight: 800;
}

.section-note {
  color: #8a958e;
  font-size: 23rpx;
}

.article-list {
  display: flex;
  flex-direction: column;
  gap: 18rpx;
}

.article-card {
  display: flex;
  gap: 22rpx;
  min-height: 238rpx;
  box-sizing: border-box;
  padding: 28rpx 24rpx 26rpx;
  border: 1rpx solid #e0e6e1;
  border-radius: 18rpx;
  background: #ffffff;
}

.article-card:active {
  background: #f8faf8;
}

.article-number {
  flex-shrink: 0;
  border-radius: 50%;
  color: #1f5146;
  font-size: 22rpx;
  font-weight: 700;
  text-align: center;
}

.article-content {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.article-meta {
  display: flex;
  justify-content: space-between;
  color: #8b958f;
  font-size: 22rpx;
}

.article-title {
  margin-top: 18rpx;
  color: #18221e;
  font-size: 32rpx;
  font-weight: 800;
  line-height: 1.4;
}

.article-summary {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 12rpx;
  color: #758179;
  font-size: 26rpx;
  line-height: 1.55;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.read-link {
  display: flex;
  justify-content: end;
  align-items: center;
  gap: 8rpx;
  margin-top: 20rpx;
  color: #d35d42;
  font-size: 24rpx;
  font-weight: 700;
}

.read-arrow {
  font-size: 30rpx;
}

.empty-state {
  padding: 100rpx 0;
  color: #7d8981;
  text-align: center;
}

.empty-title,
.empty-copy {
  display: block;
}

.empty-title {
  color: #536158;
  font-size: 30rpx;
  font-weight: 700;
}

.empty-copy {
  margin-top: 12rpx;
  font-size: 25rpx;
}
</style>
