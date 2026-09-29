<script lang="ts" setup>
import { useAudioPlayer } from '@/composables/useAudioPlayer'

defineOptions({
  name: 'ArticleAudioPlayer',
})

const props = defineProps<{
  src: string
  title: string
}>()

const { state, toggle, seek } = useAudioPlayer(() => props.src, () => props.title)

function formatTime(value: number) {
  if (!Number.isFinite(value) || value < 0) {
    return '00:00'
  }

  const totalSeconds = Math.floor(value)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function onSliderChange(event: any) {
  const value = event?.detail?.value
  if (typeof value === 'number') {
    seek(value)
  }
}
</script>

<template>
  <view class="listen-entry flex items-center justify-center gap-2" @tap="toggle">
    <view class="listen-icon" :class="state.playing ? 'i-lucide-pause' : 'i-lucide-volume-2'" />
    <view class="text-base">
      {{ state.playing ? '暂停听文章' : '听文章' }}
    </view>
  </view>

  <view v-if="state.started" class="audio-dock">
    <view class="flex items-center justify-between pb-3">
      <view class="audio-info">
        <view class="audio-title-row">
          <text class="audio-title">{{ title }}</text>
          <text v-if="state.error" class="audio-error">{{ state.error }}</text>
        </view>
        <text class="audio-time">
          {{ formatTime(state.currentTime) }} / {{ formatTime(state.duration) }}
        </text>
      </view>

      <view class="audio-controls">
        <view class="play-button center" :class="{ disabled: state.loading }" @tap="toggle">
          <view
            class="play-icon" :class="state.loading
              ? 'i-lucide-loader-circle animate-spin'
              : state.playing ? 'i-lucide-pause' : 'i-lucide-play'"
          />
        </view>
      </view>
    </view>
    <slider
      class="audio-slider" :min="0" :max="state.duration || 0" :value="state.currentTime" :step="1"
      :disabled="!state.duration || state.loading" active-color="#1f5146" background-color="#dfe6e1" :block-size="18"
      @change="onSliderChange"
    />
  </view>
</template>

<style lang="scss" scoped>
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

.audio-dock {
  position: fixed;
  right: 24rpx;
  bottom: 24rpx;
  left: 24rpx;
  z-index: 20;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12rpx 22rpx;
  box-sizing: border-box;
  margin-bottom: 20rpx;
  padding: 24rpx 26rpx;
  border: 1rpx solid #dfe6e1;
  border-radius: 24rpx;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 16rpx 44rpx rgba(43, 67, 57, 0.12);
}

.audio-info {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8rpx;
}

.audio-title-row {
  display: flex;
  gap: 16rpx;
}

.audio-title {
  overflow: hidden;
  color: #18221e;
  font-size: 26rpx;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audio-error {
  flex-shrink: 0;
  color: #c44b34;
  font-size: 22rpx;
}

.audio-time {
  color: #758179;
  font-size: 22rpx;
}

.audio-slider {
  grid-column: 1 / -1;
  margin: 0;
}

.audio-controls {
  display: flex;
  align-items: center;
}

.play-button {
  width: 72rpx;
  height: 72rpx;
  border-radius: 50%;
  background: #1f5146;
  color: #ffffff;
}

.play-icon {
  width: 34rpx;
  height: 34rpx;
}

.play-button.disabled {
  opacity: 0.6;
}
</style>
