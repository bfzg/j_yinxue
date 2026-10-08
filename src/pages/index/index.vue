<script lang="ts" setup>
import { computed, ref } from 'vue'
import { BUILD_STAMP } from '@/utils/buildInfo'
import ColumnFolder from './components/ColumnFolder.vue'
import HomeSearchBar from './components/HomeSearchBar.vue'
import { useAudioPlayerState } from '@/composables/useAudioPlayer'
import { useTopInset } from '@/composables/useSafeArea'
import { ensureSiteData, useSiteData } from '@/composables/useSiteData'
import { useSiteRefresh } from '@/composables/useSiteRefresh'
import type { Column } from '@/types/column'

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

const { site } = useSiteData()
const audioState = useAudioPlayerState()
const topInset = useTopInset(2)

// 合集网格在 scroll-view 里滚动，页面本身不滚，原生页面级下拉刷新不会触发，
// 所以用 scroll-view 自带的 refresher

const { refreshing, runRefresh } = useSiteRefresh()

/** 单篇桶没有系列感，排在所有合集后面 */
function isLoose(column: Column) {
  return (column.name || '').includes('单篇')
}

/**
 * 合集里一集都不剩就别再挂卡片。
 * 云端 columns 接口已经过滤过，这里再挡一道，防的是旧版云函数还没重传。
 */
function isLive(column: Column) {
  const count = column.nEpisodes ?? column.episodes?.length
  if (count === undefined || count === null) {
    return true
  }
  return Number(count) > 0
}

const columns = computed<Column[]>(() => {
  const keyword = searchText.value.trim().toLowerCase()
  return site.columns
    .filter((item) => {
      if (!isLive(item)) {
        return false
      }
      if (!keyword) {
        return true
      }
      return `${item.name}${item.accountName}`.toLowerCase().includes(keyword)
    })
    .sort((a, b) => Number(isLoose(a)) - Number(isLoose(b)) || a.sort - b.sort)
})

const episodeCount = computed(() =>
  columns.value.reduce((acc, item) => acc + (item.nEpisodes ?? item.episodes?.length ?? 0), 0),
)

// 只在开发包里露出构建时间，用来确认真机跑的是不是最新一版
const buildStamp = import.meta.env.DEV ? BUILD_STAMP : ''

// 有音频播放时增加底部间距，防止最后一排被浮层遮挡
const gridPaddingClass = computed(() => {
  return audioState.started ? 'pb-48' : 'pb-36'
})

function openColumn(column: Column) {
  uni.navigateTo({
    url: `/pages/column/column?id=${encodeURIComponent(column.id)}`,
  })
}

onShow(() => {
  ensureSiteData()
})
</script>

<template>
  <view class="page">
    <view class="header">
      <view class="top-space" :style="{ height: topInset }" />

      <view class="search-row px-4">
        <HomeSearchBar
          v-model="searchText" :focused="isSearchFocused" @focus="isSearchFocused = true"
          @blur="isSearchFocused = false"
        />
      </view>
    </view>

    <scroll-view
      class="content-scroll" scroll-y :show-scrollbar="false" refresher-enabled
      :refresher-triggered="refreshing" refresher-default-style="black" refresher-background="#f3f5f2"
      :refresher-threshold="70" @refresherrefresh="runRefresh"
    >
      <view v-if="columns.length" class="folder-grid px-4" :class="gridPaddingClass">
        <view v-for="column in columns" :key="`${column.id}:${column.cover || ''}`">
          <ColumnFolder :column="column" @open="openColumn" />
        </view>
      </view>

      <view v-else-if="site.syncing" class="empty">
        <view class="empty-icon i-lucide-loader-circle animate-spin" />
        <text class="empty-title">正在取内容</text>
        <text class="empty-desc">第一次打开需要连一次云端，稍等一下。</text>
      </view>

      <view v-else class="empty">
        <view class="empty-icon i-lucide-folder-open" />
        <text class="empty-title">{{ searchText ? '没有匹配的合集' : '还没有上架的合集' }}</text>
        <text class="empty-desc">{{ searchText ? '换个关键词试试。' : '后台发布内容后，这里会出现按系列归好的合集。' }}</text>
      </view>

      <view v-if="buildStamp" class="build-stamp">
        <text>{{ buildStamp }}</text>
      </view>
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
  height: 32rpx;
}

.brand-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
}

.brand-box {
  display: flex;
  flex-direction: column;
}

.brand-title {
  color: #18221e;
  font-size: 40rpx;
  font-weight: 800;
  line-height: 1.3;
}

.brand-sub {
  margin-top: 6rpx;
  color: #8b958f;
  font-size: 22rpx;
}

.sync-dot {
  display: flex;
  width: 56rpx;
  height: 56rpx;
  align-items: center;
  justify-content: center;
  border: 1rpx solid #dfe6e1;
  border-radius: 999rpx;
  background: #ffffff;
}

.sync-icon {
  width: 28rpx;
  height: 28rpx;
  color: #1f5146;
}

.search-row {
  display: flex;
}

.folder-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24rpx;
  margin-top: 36rpx;
}

.build-stamp {
  padding: 10rpx 0 26rpx;
  text-align: center;
}

.build-stamp text {
  color: #b6beb8;
  font-size: 20rpx;
}

.empty {
  display: flex;
  min-height: 60vh;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14rpx;
}

.empty-icon {
  width: 74rpx;
  height: 74rpx;
  color: #c3ccc5;
}

.empty-title {
  color: #18221e;
  font-size: 30rpx;
  font-weight: 800;
}

.empty-desc {
  width: 480rpx;
  color: #8b958f;
  font-size: 24rpx;
  text-align: center;
}
</style>
