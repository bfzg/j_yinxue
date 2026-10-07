<script lang="ts" setup>
import { computed, ref } from 'vue'
import type { Column } from '@/types/column'

defineOptions({
  name: 'ColumnFolder',
})

const props = defineProps<{
  column: Column
}>()

const emit = defineEmits<{
  (e: 'open', column: Column): void
}>()

const coverFailed = ref(false)

const collected = computed(() => props.column.nEpisodes ?? props.column.episodes?.length ?? 0)

// 采集进度没跑完的系列，封面角标写成「6/12 集」更直观
const progressLabel = computed(() => {
  const total = props.column.episodeTotal || 0
  return total > collected.value ? `${collected.value}/${total} 集` : `${collected.value} 集`
})
</script>

<template>
  <view class="folder" @tap="emit('open', column)">
    <view class="folder-body">
      <view class="cover-box">
        <image
          v-if="column.cover && !coverFailed" class="cover-img" :src="column.cover" mode="aspectFill"
          @error="coverFailed = true"
        />
        <view v-else class="cover-fallback">
          <view class="fallback-icon i-lucide-book-open" />
        </view>
        <view class="cover-shade" />
      </view>

      <text class="folder-name">{{ column.name }}</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.folder {
  position: relative;
  /* 卡片之间留出标签页的凸起高度 */
  padding-top: 22rpx;
}

.folder-tab {
  position: absolute;
  top: 0;
  left: 18rpx;
  z-index: 0;
  display: flex;
  max-width: 60%;
  height: 38rpx;
  align-items: center;
  gap: 8rpx;
  padding: 0 18rpx;
  border-radius: 14rpx 14rpx 4rpx 4rpx;
}

.tab-icon {
  flex-shrink: 0;
  width: 22rpx;
  height: 22rpx;
  color: #4c6357;
}

.tab-name {
  overflow: hidden;
  color: #4c6357;
  font-size: 19rpx;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.folder-body {
  position: relative;
  z-index: 1;
  display: flex;
  box-sizing: border-box;
  height: 100%;
  flex-direction: column;
  padding: 14rpx;
}

.folder:active .folder-body {
  background: #f7faf7;
}

.cover-box {
  position: relative;
  width: 100%;
  height: 396rpx;
  overflow: hidden;
  border-radius: 14rpx;
  background: #eef2ee;
}

.cover-img {
  width: 100%;
  height: 396rpx;
}

.cover-fallback {
  display: flex;
  width: 100%;
  height: 396rpx;
  align-items: center;
  justify-content: center;
  background: #e7ede8;
}

.fallback-icon {
  width: 64rpx;
  height: 64rpx;
  color: #93a098;
}

.cover-shade {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  height: 110rpx;
  background: linear-gradient(180deg, rgba(24, 34, 30, 0) 0%, rgba(24, 34, 30, 0.45) 100%);
}

.folder-name {
  display: -webkit-box;
  overflow: hidden;
  width: 100%;
  height: 78rpx;
  margin-top: 14rpx;
  color: #18221e;
  font-size: 26rpx;
  font-weight: 700;
  line-height: 39rpx;
  text-align: center;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
</style>
