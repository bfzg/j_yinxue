<script lang="ts" setup>
import { computed } from 'vue'
import { formatDate } from '@/utils/format'
import type { ColumnEpisode } from '@/types/column'

defineOptions({
  name: 'EpisodeRow',
})

const props = defineProps<{
  episode: ColumnEpisode
  index: number
  isPlaying?: boolean
}>()

const emit = defineEmits<{
  (e: 'open', episode: ColumnEpisode): void
}>()

// 云端已经按合集集序排好，序号就是这一行的位次：一个合集从 01 开始连号
const orderLabel = computed(() => String(props.index + 1).padStart(2, '0'))
</script>

<template>
  <view class="ep-row" :class="{ playing: isPlaying }" @tap="emit('open', episode)">
    <view class="ep-order">
      <text class="ep-order-num">{{ orderLabel }}</text>
    </view>

    <view class="ep-main">
      <text class="ep-title">{{ episode.title }}</text>
      <view class="ep-meta">
        <text>{{ formatDate(episode.publishedAt) || '日期待补' }}</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.ep-row {
  display: flex;
  gap: 18rpx;
  align-items: center;
  padding: 22rpx 4rpx;
  border-bottom: 1rpx solid #eceee9;
}

.ep-row:last-child {
  border-bottom: none;
}

.ep-row:active {
  background: #f7faf7;
}

.ep-order {
  flex-shrink: 0;
  width: 54rpx;
  color: #9aa39d;
  font-size: 24rpx;
  font-weight: 700;
}

.playing .ep-order {
  color: #1f5146;
}

.ep-main {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 8rpx;
}

.ep-title {
  color: #202622;
  font-size: 28rpx;
  font-weight: 600;
  line-height: 1.45;
}

.ep-meta {
  display: flex;
  align-items: center;
  gap: 8rpx;
  color: #97a09a;
  font-size: 21rpx;
}

.dot {
  color: #c3ccc5;
}
</style>
