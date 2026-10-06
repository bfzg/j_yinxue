<script lang="ts" setup>
import { computed, ref } from 'vue'
import articles from '@/static/data/articles.json'
import HomeArticleList from './components/HomeArticleList.vue'
import HomeCategoryTabs from './components/HomeCategoryTabs.vue'
import HomeEmptyState from './components/HomeEmptyState.vue'
import HomeSearchBar from './components/HomeSearchBar.vue'
import { useAudioPlayerState } from '@/composables/useAudioPlayer'
import type { Article } from '@/types/article'

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

const searchText = ref('')
const isSearchFocused = ref(false)
const activeCategory = ref('全部')

const audioState = useAudioPlayerState()

const playingArticleId = computed(() => {
  if (!audioState.playing || !audioState.src) {
    return ''
  }

  return articles.items.find(item => item.audioUrl === audioState.src)?.id || ''
})

const articleItems = computed<Article[]>(() => {
  const keyword = searchText.value.trim().toLowerCase()
  return [...articles.items]
    .filter(item => item.enabled)
    .filter(item => activeCategory.value === '全部' || item.category === activeCategory.value)
    .filter((item) => {
      if (!keyword) {
        return true
      }
      return `${item.title}${item.summary}`.toLowerCase().includes(keyword)
    })
    .sort((a, b) => a.sort - b.sort)
})

const categories = computed(() => [
  '全部',
  ...Array.from(new Set(articles.items.filter(item => item.enabled).map(item => item.category))),
])

// 有音频播放时增加底部间距，防止被浮层遮挡
const listPaddingClass = computed(() => {
  return audioState.started ? 'pb-48' : 'pb-36'
})

function openArticle(article: Article) {
  uni.navigateTo({
    url: `/pages/article/article?id=${article.id}`,
  })
}

function openColumns() {
  uni.navigateTo({
    url: '/pages/columns/columns',
  })
}
</script>

<template>
  <view class="page">
    <view class="header">
      <view class="top-space" />

      <view class="search-row px-2">
        <HomeSearchBar
          v-model="searchText"
          :focused="isSearchFocused"
          @focus="isSearchFocused = true"
          @blur="isSearchFocused = false"
        />
        <view class="column-entry" @tap="openColumns">
          <view class="column-entry-icon i-lucide-library" />
          <text class="column-entry-text">栏目</text>
        </view>
      </view>

      <HomeCategoryTabs v-model:active-category="activeCategory" :categories="categories" />
    </view>

    <scroll-view class="content-scroll" scroll-y :show-scrollbar="false">
      <view v-if="articleItems.length" class="list-area px-4" :class="listPaddingClass">
        <view class="section-head">
          <view>
            <text class="section-title">共 {{ articleItems.length }} 篇</text>
          </view>
        </view>

        <HomeArticleList
          :articles="articleItems"
          :playing-id="playingArticleId"
          @open="openArticle"
        />
      </view>

      <HomeEmptyState v-else />
    </scroll-view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  box-sizing: border-box;

  background: #f3f5f2;
  color: #18221e;
}

.header {
  flex-shrink: 0;
  background: #f3f5f2;
}

.content-scroll {
  flex: 1;
  min-height: 0;
  height: 0;
}

.top-space {
  height: 92rpx;
}

.search-row {
  display: flex;
  align-items: center;
}

.column-entry {
  display: flex;
  height: 72rpx;
  margin-left: auto;
  align-items: center;
  gap: 8rpx;
  padding: 0 22rpx;
  border: 1rpx solid #dfe6e1;
  border-radius: 999rpx;
  background: #ffffff;
  box-shadow: 0 12rpx 30rpx rgba(43, 67, 57, 0.05);
  color: #1f5146;
}

.column-entry:active {
  background: #eef4ef;
}

.column-entry-icon {
  width: 30rpx;
  height: 30rpx;
}

.column-entry-text {
  font-size: 24rpx;
  font-weight: 700;
}

.list-area {
  min-height: 100%;
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
  font-size: 22rpx;
  font-weight: 800;
}
</style>
