<script lang="ts" setup>
import { computed, ref } from 'vue'
import ColumnFolder from './components/ColumnFolder.vue'
import HomeSearchBar from './components/HomeSearchBar.vue'
import { useAudioPlayerState } from '@/composables/useAudioPlayer'
import { useTopInset } from '@/composables/useSafeArea'
import { ensureSiteData, useSiteData } from '@/composables/useSiteData'
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

/** 单篇桶没有系列感，排在所有合集后面 */
function isLoose(column: Column) {
  return (column.name || '').includes('单篇')
}

const columns = computed<Column[]>(() => {
  const keyword = searchText.value.trim().toLowerCase()
  return site.columns
    .filter((item) => {
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

    <scroll-view class="content-scroll" scroll-y :show-scrollbar="false">
      <view v-if="columns.length" class="folder-grid px-4" :class="gridPaddingClass">
        <view v-for="column in columns" :key="column.id" class="grid-item">
          <ColumnFolder :column="column" @open="openColumn" />
        </view>
      </view>

      <view v-else class="empty">
        <view class="empty-icon i-lucide-folder-open" />
        <text class="empty-title">{{ searchText ? '没有匹配的合集' : '还没有上架的合集' }}</text>
        <text class="empty-desc">{{ searchText ? '换个关键词试试。' : '后台发布内容后，这里会出现按系列归好的合集。' }}</text>
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
  display: flex;
  flex-wrap: wrap;
  margin-top: 36rpx;
}

/* 两列网格用 margin 撑间距，小程序里 gap 兼容性不稳 */
.grid-item {
  width: calc(50% - 12rpx);
  margin-right: 24rpx;
  margin-bottom: 34rpx;
}

.grid-item:nth-child(2n) {
  margin-right: 0;
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
