<script lang="ts" setup>
import { computed } from 'vue'
import { formatDate, formatDuration } from '@/utils/format'
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
  (e: 'play', index: number): void
  (e: 'open', episode: ColumnEpisode): void
}>()

const orderLabel = computed(() => {
  const no = Number(props.episode.episodeNo || 0)
  return String(no || props.index + 1).padStart(2, '0')
})
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
        <text class="dot">·</text>
        <text>{{ formatDuration(episode.duration) }}</text>
      </view>
    </view>

    <view class="ep-play" :class="{ active: isPlaying }" @tap.stop="emit('play', index)">
      <view class="play-icon" :class="isPlaying ? 'i-lucide-pause' : 'i-lucide-play'" />
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

.ep-play {
  display: flex;
  flex-shrink: 0;
  width: 64rpx;
  height: 64rpx;
  align-items: center;
  justify-content: center;
  border-radius: 999rpx;
  background: #eef4ef;
  color: #1f5146;
}

.ep-play.active {
  background: #1f5146;
  color: #ffffff;
}

.play-icon {
  width: 28rpx;
  height: 28rpx;
}
</style>
