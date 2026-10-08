<script lang="ts" setup>
import { computed } from 'vue'
import { site } from '@/composables/useSiteData'
import { useAudioPlayerWithPlaylist } from '@/composables/useAudioPlayer'

defineOptions({
  name: 'FloatingAudioPlayer',
})

const {
  state,
  hasNext,
  hasPrev,
  currentPlaylistItem,
  toggle,
  playNext,
  playPrev,
  seek,
  destroy,
} = useAudioPlayerWithPlaylist()

/**
 * 正在听的这篇对应的文章 id。
 * 有播放列表时直接取当前条目；单篇直链起播就按音频地址回查一次。
 */
const playingArticleId = computed(() => {
  const item = currentPlaylistItem.value as any
  if (item?.id) {
    return String(item.id)
  }
  const src = state.src
  if (!src) {
    return ''
  }
  const hit = site.playlist.find(one => one.audioUrl === src)
    || site.articles.find(one => one.audioUrl === src)
  return hit ? String(hit.id) : ''
})

/** 点标题去看正文，已经在那篇里就不重复压栈 */
function openArticle() {
  const id = playingArticleId.value
  if (!id) {
    return
  }
  const pages = getCurrentPages() as any[]
  const top = pages[pages.length - 1]
  const route = String(top?.route || '')
  const currentId = decodeURIComponent(String(top?.options?.id || ''))
  if (route === 'pages/article/article' && currentId === id) {
    return
  }
  uni.navigateTo({ url: `/pages/article/article?id=${encodeURIComponent(id)}` })
}

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

function onClose() {
  destroy()
}
</script>

<template>
  <view v-if="state.started" class="floating-player">
    <!-- 拖拽手柄 -->
    <view class="drag-handle" />

    <view class="player-body">
      <!-- 左侧：标题 + 时间 -->
      <view class="player-info">
        <view class="player-title-row" @tap.stop="openArticle">
          <text class="player-title">{{ currentPlaylistItem?.title || '听文章' }}</text>
          <text v-if="state.error" class="player-error">{{ state.error }}</text>
          <text v-else-if="state.hint" class="player-error">{{ state.hint }}</text>
        </view>
        <text class="player-time">
          {{ formatTime(state.currentTime) }} / {{ formatTime(state.duration) }}
        </text>
      </view>

      <!-- 右侧：控制按钮 -->
      <view class="player-controls">
        <view class="ctrl-btn" :class="{ disabled: !hasPrev || state.loading }" @tap="playPrev">
          <view class="ctrl-icon i-lucide-skip-back" />
        </view>

        <view class="play-btn" :class="{ disabled: state.loading }" @tap="toggle">
          <view
            class="play-icon" :class="state.loading
              ? 'i-lucide-loader-circle animate-spin'
              : state.playing ? 'i-lucide-pause' : 'i-lucide-play'"
          />
        </view>

        <view class="ctrl-btn" :class="{ disabled: !hasNext || state.loading }" @tap="playNext">
          <view class="ctrl-icon i-lucide-skip-forward" />
        </view>
      </view>
    </view>

    <!-- 进度条 -->
    <view class="player-progress">
      <slider
        class="audio-slider" :min="0" :max="state.duration || 0" :value="state.currentTime" :step="1"
        :disabled="!state.duration || state.loading" active-color="#1f5146" background-color="#dfe6e1" :block-size="24"
        @change="onSliderChange"
      />
    </view>
  </view>
</template>

<style lang="scss" scoped>
.floating-player {
  position: fixed;
  right: 24rpx;
  bottom: 24rpx;
  left: 24rpx;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 0;
  box-sizing: border-box;
  padding: 36rpx 32rpx 24rpx;
  border: 1rpx solid #dfe6e1;
  border-radius: 24rpx;
  background: rgba(255, 255, 255, 0.97);
  box-shadow: 0 16rpx 44rpx rgba(43, 67, 57, 0.14);
  backdrop-filter: blur(12px);
}

.drag-handle {
  display: none;
}

.player-body {
  display: flex;
  align-items: center;
  gap: 24rpx;
}

.player-info {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 10rpx;
}

.player-title-row {
  display: flex;
  gap: 16rpx;
  align-items: center;
}

.player-title-row:active {
  opacity: 0.6;
}

.title-arrow {
  flex-shrink: 0;
  width: 26rpx;
  height: 26rpx;
  color: #a8b1aa;
}

.player-title {
  overflow: hidden;
  color: #18221e;
  font-size: 36rpx;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.player-error {
  flex-shrink: 0;
  color: #c44b34;
  font-size: 28rpx;
}

.player-time {
  color: #758179;
  font-size: 28rpx;
}

.player-controls {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 0;
}

.ctrl-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 92rpx;
  height: 92rpx;
  border-radius: 50%;
  color: #1f5146;
}

.ctrl-btn.disabled {
  opacity: 0.3;
  pointer-events: none;
}

.ctrl-icon {
  width: 42rpx;
  height: 42rpx;
}

.play-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 108rpx;
  height: 108rpx;
  border-radius: 50%;
  background: #1f5146;
  color: #ffffff;
}

.play-btn.disabled {
  opacity: 0.6;
}

.play-icon {
  width: 48rpx;
  height: 48rpx;
}

.player-progress {
  margin-top: 16rpx;
}

.audio-slider {
  margin: 0;
}
</style>
