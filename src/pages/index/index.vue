<script lang="ts" setup>
import { computed, nextTick, ref } from 'vue'
import playlist from '@/static/data/playlist.json'

defineOptions({
  name: 'Home',
})

definePage({
  type: 'home',
  style: {
    navigationStyle: 'custom',
    navigationBarTitleText: '',
    navigationBarBackgroundColor: '#f7f6f1',
    navigationBarTextStyle: 'black',
  },
})

interface PlaylistItem {
  id: string
  title: string
  description: string
  cover: string
  audioUrl: string
  duration: number
  sort: number
  publishedAt: string
  enabled: boolean
}

interface PlaybackState {
  currentId: string
  currentTime: number
  updatedAt: number
}

const PLAYBACK_STATE_KEY = 'j_yinxue_playback_state'
const DEFAULT_COVER = '/static/images/logo.jpg'

const appInfo = playlist.app
const playableItems = computed<PlaylistItem[]>(() => {
  return [...playlist.items]
    .filter(item => item.enabled)
    .sort((a, b) => a.sort - b.sort)
})

const currentId = ref('')
const currentTime = ref(0)
const duration = ref(0)
const isPlaying = ref(false)
const isWaiting = ref(false)
const isPlayerOpen = ref(false)
const pageTopPadding = ref(40)
const panelDragY = ref(0)
const isPanelDragging = ref(false)
const discRotateDeg = ref(0)

let audio: any
let lastSavedAt = 0
let panelTouchStartY = 0
let discRotateTimer: ReturnType<typeof setInterval> | undefined

const currentItem = computed<PlaylistItem | undefined>(() => {
  return playableItems.value.find(item => item.id === currentId.value) || playableItems.value[0]
})

const currentIndex = computed(() => {
  return playableItems.value.findIndex(item => item.id === currentItem.value?.id)
})

const hasPrevious = computed(() => currentIndex.value > 0)
const hasNext = computed(() => currentIndex.value >= 0 && currentIndex.value < playableItems.value.length - 1)
const progressPercent = computed(() => {
  const total = duration.value || currentItem.value?.duration || 0
  if (!total) {
    return 0
  }
  return Math.min(100, Math.max(0, (currentTime.value / total) * 100))
})

function createAudioManager() {
  if (audio) {
    return audio
  }

  audio = (uni as any).getBackgroundAudioManager?.() || (uni as any).createInnerAudioContext?.()

  if (!audio) {
    return null
  }

  audio.onPlay?.(() => {
    isPlaying.value = true
    isWaiting.value = false
    startDiscRotate()
  })

  audio.onPause?.(() => {
    isPlaying.value = false
    stopDiscRotate()
    savePlaybackState()
  })

  audio.onStop?.(() => {
    isPlaying.value = false
    stopDiscRotate()
    savePlaybackState()
  })

  audio.onWaiting?.(() => {
    isWaiting.value = true
  })

  audio.onCanplay?.(() => {
    isWaiting.value = false
    duration.value = Number(audio.duration || currentItem.value?.duration || 0)
  })

  audio.onTimeUpdate?.(() => {
    currentTime.value = Number(audio.currentTime || 0)
    duration.value = Number(audio.duration || currentItem.value?.duration || 0)
    savePlaybackStateThrottled()
  })

  audio.onEnded?.(() => {
    stopDiscRotate()
    playNext(true)
  })

  audio.onError?.(() => {
    isPlaying.value = false
    isWaiting.value = false
    stopDiscRotate()
    uni.showToast({
      title: '音频加载失败',
      icon: 'none',
    })
  })

  audio.onNext?.(() => {
    playNext(false)
  })

  audio.onPrev?.(() => {
    playPrevious()
  })

  return audio
}

function restorePlaybackState() {
  const cached = uni.getStorageSync(PLAYBACK_STATE_KEY) as PlaybackState | ''
  const firstItem = playableItems.value[0]

  if (cached && playableItems.value.some(item => item.id === cached.currentId)) {
    currentId.value = cached.currentId
    currentTime.value = Math.max(0, Number(cached.currentTime || 0))
  }
  else if (firstItem) {
    currentId.value = firstItem.id
    currentTime.value = 0
  }

  duration.value = currentItem.value?.duration || 0
}

function savePlaybackState() {
  if (!currentItem.value) {
    return
  }

  const state: PlaybackState = {
    currentId: currentItem.value.id,
    currentTime: Math.floor(currentTime.value),
    updatedAt: Date.now(),
  }

  uni.setStorageSync(PLAYBACK_STATE_KEY, state)
}

function savePlaybackStateThrottled() {
  const now = Date.now()
  if (now - lastSavedAt < 3000) {
    return
  }

  lastSavedAt = now
  savePlaybackState()
}

function setAudioMetadata(item: PlaylistItem, startAt = 0) {
  const manager = createAudioManager()
  if (!manager) {
    return null
  }

  manager.title = item.title
  manager.singer = appInfo.author
  manager.epname = appInfo.name
  manager.coverImgUrl = item.cover || appInfo.cover || DEFAULT_COVER
  manager.webUrl = ''
  manager.startTime = Math.max(0, Math.floor(startAt))

  return manager
}

function playItem(item: PlaylistItem, startAt = 0) {
  if (!item.audioUrl) {
    uni.showToast({
      title: '请先配置音频地址',
      icon: 'none',
    })
    return
  }

  const manager = setAudioMetadata(item, startAt)
  if (!manager) {
    return
  }

  currentId.value = item.id
  currentTime.value = Math.max(0, Math.floor(startAt))
  duration.value = item.duration
  isWaiting.value = true
  if (startAt === 0) {
    discRotateDeg.value = 0
  }

  if (manager.src === item.audioUrl) {
    if (startAt > 0) {
      manager.seek?.(startAt)
    }
    manager.play?.()
  }
  else {
    manager.src = item.audioUrl
    manager.play?.()
  }

  savePlaybackState()
}

function togglePlay() {
  const item = currentItem.value
  const manager = createAudioManager()
  if (!item || !manager) {
    return
  }

  if (isPlaying.value) {
    manager.pause?.()
    return
  }

  playItem(item, currentTime.value)
}

function playFromList(item: PlaylistItem) {
  const startAt = item.id === currentId.value ? currentTime.value : 0
  playItem(item, startAt)
}

function playNext(fromEnded = false) {
  if (!hasNext.value) {
    isPlaying.value = false
    if (fromEnded) {
      currentTime.value = 0
      savePlaybackState()
    }
    return
  }

  const nextItem = playableItems.value[currentIndex.value + 1]
  playItem(nextItem, 0)
}

function playPrevious() {
  if (!hasPrevious.value) {
    return
  }

  const previousItem = playableItems.value[currentIndex.value - 1]
  playItem(previousItem, 0)
}

function onSeekChange(event: any) {
  const nextTime = Number(event.detail?.value || 0)
  currentTime.value = nextTime
  createAudioManager()?.seek?.(nextTime)
  savePlaybackState()
}

function openPlayer() {
  panelDragY.value = 0
  isPlayerOpen.value = true
}

function closePlayer() {
  panelDragY.value = 0
  isPanelDragging.value = false
  isPlayerOpen.value = false
}

function startDiscRotate() {
  if (discRotateTimer) {
    return
  }

  discRotateTimer = setInterval(() => {
    discRotateDeg.value = (discRotateDeg.value + 3) % 360
  }, 50)
}

function stopDiscRotate() {
  if (!discRotateTimer) {
    return
  }

  clearInterval(discRotateTimer)
  discRotateTimer = undefined
}

function onPanelTouchStart(event: TouchEvent) {
  panelTouchStartY = event.touches?.[0]?.clientY || 0
  isPanelDragging.value = true
}

function onPanelTouchMove(event: TouchEvent) {
  if (!isPanelDragging.value) {
    return
  }

  const currentY = event.touches?.[0]?.clientY || 0
  panelDragY.value = Math.max(0, currentY - panelTouchStartY)
}

function onPanelTouchEnd() {
  if (panelDragY.value > 90) {
    closePlayer()
    return
  }

  panelDragY.value = 0
  isPanelDragging.value = false
}

function formatTime(seconds: number) {
  const safeSeconds = Math.max(0, Math.floor(seconds || 0))
  const minutes = Math.floor(safeSeconds / 60)
  const rest = safeSeconds % 60
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

function setPageTopPadding() {
  const windowInfo = (uni as any).getWindowInfo?.() || uni.getSystemInfoSync()
  const statusBarHeight = Number(windowInfo.statusBarHeight || 0)
  const menuButton = (uni as any).getMenuButtonBoundingClientRect?.()

  pageTopPadding.value = Math.ceil((menuButton?.bottom || statusBarHeight + 44) + 24)
}

onLoad(() => {
  setPageTopPadding()
  createAudioManager()
  restorePlaybackState()
})

onShow(() => {
  nextTick(() => {
    duration.value = currentItem.value?.duration || duration.value
  })
})

onHide(() => {
  savePlaybackState()
})

onUnload(() => {
  stopDiscRotate()
  savePlaybackState()
})
</script>

<template>
  <view class="page" :style="{ paddingTop: `${pageTopPadding}px`, paddingBottom: `4rem` }">
    <view class="hero">
      <view class="hero-cover">
        <image :src="appInfo.cover || DEFAULT_COVER" mode="aspectFill" class="cover-image" />
      </view>

      <view class="hero-content">
        <text class="app-title">{{ appInfo.name }}</text>
        <text class="app-description">{{ appInfo.description }}</text>
      </view>
    </view>

    <view class="section-head">
      <text class="section-title">播放列表</text>
      <text class="section-count">{{ playableItems.length }} 期</text>
    </view>

    <view class="episode-list">
      <view
        v-for="(item, index) in playableItems"
        :key="item.id"
        class="episode"
        :class="{ active: item.id === currentItem?.id }"
        @tap="playFromList(item)"
      >
        <view class="episode-index">
          <text v-if="item.id === currentItem?.id && isPlaying">▶</text>
          <text v-else>{{ String(index + 1).padStart(2, '0') }}</text>
        </view>

        <view class="episode-main">
          <text class="episode-title">{{ item.title }}</text>
          <text class="episode-desc">{{ item.description }}</text>
        </view>

        <text class="episode-duration">{{ formatTime(item.duration) }}</text>
      </view>
    </view>

    <view class="mini-player-spacer" />

    <view v-if="currentItem" class="mini-player">
      <view class="mini-body" @tap="openPlayer">
        <view class="mini-disc" :style="{ transform: `rotate(${discRotateDeg}deg)` }">
          <image :src="currentItem.cover || appInfo.cover || DEFAULT_COVER" mode="aspectFill" class="disc-image" />
          <view class="disc-center" />
        </view>

        <view class="mini-info">
          <text class="mini-title">{{ currentItem.title }}</text>
          <text class="mini-time">{{ formatTime(currentTime) }} / {{ formatTime(duration || currentItem.duration) }}</text>
          <view class="mini-progress">
            <view class="mini-progress-inner" :style="{ width: `${progressPercent}%` }" />
          </view>
        </view>

        <view class="mini-button" @tap.stop="togglePlay">
          <text>{{ isPlaying ? 'Ⅱ' : '▶' }}</text>
        </view>
      </view>
    </view>

    <view v-if="isPlayerOpen && currentItem" class="player-mask" @tap="closePlayer">
      <view
        class="player-panel"
        :class="{ dragging: isPanelDragging }"
        :style="{ transform: `translateY(${panelDragY}px)` }"
        @tap.stop
        @touchstart="onPanelTouchStart"
        @touchmove.stop.prevent="onPanelTouchMove"
        @touchend="onPanelTouchEnd"
        @touchcancel="onPanelTouchEnd"
      >
        <view class="panel-handle" />

        <view class="player-cover">
          <image :src="currentItem.cover || appInfo.cover || DEFAULT_COVER" mode="aspectFill" class="cover-image" />
        </view>

        <text class="player-title">{{ currentItem.title }}</text>
        <text class="player-desc">{{ currentItem.description }}</text>

        <view class="time-row">
          <text>{{ formatTime(currentTime) }}</text>
          <text>{{ formatTime(duration || currentItem.duration) }}</text>
        </view>

        <slider
          class="seek-slider"
          :value="Math.floor(currentTime)"
          :max="Math.floor(duration || currentItem.duration)"
          :block-size="18"
          active-color="#23483f"
          background-color="#d6ddd9"
          @change="onSeekChange"
        />

        <view class="controls">
          <button class="control-button side" :disabled="!hasPrevious" @tap="playPrevious">
            ‹
          </button>
          <button class="control-button primary" @tap="togglePlay">
            {{ isWaiting ? '…' : isPlaying ? 'Ⅱ' : '▶' }}
          </button>
          <button class="control-button side" :disabled="!hasNext" @tap="playNext(false)">
            ›
          </button>
        </view>

        <view class="play-mode">
          <text>顺序自动连播</text>
          <text>{{ currentIndex + 1 }} / {{ playableItems.length }}</text>
        </view>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  box-sizing: border-box;
  padding-right: 28rpx;
  padding-left: 28rpx;
  background: #f7f6f1;
}

.hero {
  display: flex;
  gap: 24rpx;
  align-items: center;
  padding: 18rpx 0 30rpx;
}

.hero-cover,
.player-cover {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  overflow: hidden;
  background: #23483f;
}

.hero-cover {
  width: 120rpx;
  height: 120rpx;
  border-radius: 16rpx;
}

.cover-image {
  width: 100%;
  height: 100%;
}

.hero-content {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
}

.app-title {
  color: #171b18;
  font-size: 48rpx;
  font-weight: 800;
  line-height: 1.15;
}

.app-author {
  margin-top: 8rpx;
  color: #23483f;
  font-size: 26rpx;
}

.app-description {
  margin-top: 12rpx;
  color: #68716c;
  font-size: 26rpx;
  line-height: 1.5;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 18rpx 0 16rpx;
}

.section-title {
  color: #171b18;
  font-size: 34rpx;
  font-weight: 700;
}

.section-count {
  color: #7b837e;
  font-size: 24rpx;
}

.episode-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.episode {
  display: flex;
  gap: 20rpx;
  align-items: center;
  min-height: 128rpx;
  box-sizing: border-box;
  padding: 24rpx 22rpx;
  border: 1rpx solid #e1e5df;
  border-radius: 18rpx;
  background: #fffffb;
}

.episode.active {
  border-color: #23483f;
  background: #f1f5f2;
}

.episode.active .episode-index {
  background: #23483f;
  color: #ffffff;
}

.episode-index {
  display: flex;
  width: 54rpx;
  height: 54rpx;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 50%;
  background: #e9eee9;
  color: #23483f;
  font-size: 22rpx;
  font-weight: 700;
}

.episode-main {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.episode-title {
  overflow: hidden;
  color: #171b18;
  font-size: 30rpx;
  font-weight: 700;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.episode-desc {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 8rpx;
  color: #68716c;
  font-size: 25rpx;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.episode-duration {
  flex-shrink: 0;
  align-self: center;
  min-width: 74rpx;
  color: #68716c;
  font-size: 24rpx;
  font-weight: 700;
  line-height: 1;
  text-align: right;
}

.mini-player-spacer {
  height: 204rpx;
}

.mini-player {
  position: fixed;
  right: 22rpx;
  bottom: calc(24rpx + env(safe-area-inset-bottom));
  left: 22rpx;
  overflow: hidden;
  border: 1rpx solid #dfe5df;
  border-radius: 24rpx;
  background: #fffffb;
  box-shadow: 0 18rpx 46rpx rgba(35, 72, 63, 0.14);
}

.mini-progress {
  overflow: hidden;
  width: 100%;
  height: 10rpx;
  margin-top: 18rpx;
  border-radius: 999rpx;
  background: #dfe5df;
}

.mini-progress-inner {
  height: 100%;
  background: #23483f;
}

.mini-body {
  display: flex;
  align-items: center;
  gap: 24rpx;
  min-height: 152rpx;
  padding: 24rpx 24rpx;
}

.mini-disc {
  position: relative;
  width: 112rpx;
  height: 112rpx;
  flex-shrink: 0;
  overflow: hidden;
  border: 4rpx solid #23483f;
  border-radius: 50%;
  background: #23483f;
}

.disc-image {
  width: 100%;
  height: 100%;
  border-radius: 50%;
}

.disc-center {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 34rpx;
  height: 34rpx;
  box-sizing: border-box;
  border: 4rpx solid rgba(35, 72, 63, 0.55);
  border-radius: 50%;
  background: #ffffff;
  transform: translate(-50%, -50%);
}

.mini-info {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.mini-title {
  overflow: hidden;
  color: #171b18;
  font-size: 32rpx;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mini-time {
  margin-top: 10rpx;
  color: #68716c;
  font-size: 25rpx;
}

.mini-button {
  display: flex;
  width: 92rpx;
  height: 92rpx;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 50%;
  background: #23483f;
  color: #ffffff;
  font-size: 34rpx;
  font-weight: 700;
}

.player-mask {
  position: fixed;
  z-index: 20;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  display: flex;
  align-items: flex-end;
  background: rgba(23, 27, 24, 0.42);
}

.player-panel {
  width: 100%;
  box-sizing: border-box;
  padding: 18rpx 36rpx calc(42rpx + env(safe-area-inset-bottom));
  border-radius: 28rpx 28rpx 0 0;
  background: #fffffb;
  transition: transform 180ms ease-out;
  will-change: transform;
}

.player-panel.dragging {
  transition: none;
}

.panel-handle {
  width: 72rpx;
  height: 8rpx;
  margin: 0 auto 34rpx;
  border-radius: 99rpx;
  background: #d6ddd9;
}

.player-cover {
  width: 420rpx;
  height: 420rpx;
  margin: 0 auto 34rpx;
  border-radius: 24rpx;
  box-shadow: 0 22rpx 60rpx rgba(35, 72, 63, 0.18);
}

.player-title {
  display: block;
  color: #171b18;
  font-size: 38rpx;
  font-weight: 800;
  line-height: 1.35;
  text-align: center;
}

.player-desc {
  display: block;
  margin: 12rpx auto 28rpx;
  color: #68716c;
  font-size: 26rpx;
  line-height: 1.5;
  text-align: center;
}

.time-row {
  display: flex;
  justify-content: space-between;
  color: #68716c;
  font-size: 23rpx;
}

.seek-slider {
  margin: 6rpx -18rpx 20rpx;
}

.controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 34rpx;
  margin-top: 8rpx;
}

.control-button {
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
  padding: 0;
  border: none;
  border-radius: 50%;
  line-height: 1;
}

.control-button::after {
  border: none;
}

.control-button.side {
  width: 86rpx;
  height: 86rpx;
  background: #e9eee9;
  color: #23483f;
  font-size: 58rpx;
}

.control-button.primary {
  width: 116rpx;
  height: 116rpx;
  background: #23483f;
  color: #ffffff;
  font-size: 40rpx;
  font-weight: 700;
}

.control-button[disabled] {
  opacity: 0.35;
}

.play-mode {
  display: flex;
  justify-content: space-between;
  margin-top: 30rpx;
  color: #68716c;
  font-size: 24rpx;
}
</style>
