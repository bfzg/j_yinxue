<script lang="ts" setup>
import { computed, ref } from 'vue'
import columnsDoc from '@/static/data/columns.json'
import EpisodeRow from './components/EpisodeRow.vue'
import { formatDate, formatDuration } from '@/utils/format'
import { playByIndex, setPlaylist, useAudioPlayerWithPlaylist } from '@/composables/useAudioPlayer'
import type { Column, ColumnDoc, ColumnEpisode } from '@/types/column'
import type { PlaylistItem } from '@/composables/useAudioPlayer'

defineOptions({
  name: 'ColumnDetail',
})

definePage({
  style: {
    navigationStyle: 'custom',
    navigationBarTitleText: '',
    navigationBarBackgroundColor: '#f3f5f2',
    navigationBarTextStyle: 'black',
  },
})

const columnId = ref('')
const coverFailed = ref(false)

const column = computed<Column | undefined>(() => {
  const items = (columnsDoc as unknown as ColumnDoc).items || []
  return items.find(item => item.id === columnId.value) || items[0]
})

const episodes = computed<ColumnEpisode[]>(() => column.value?.episodes || [])

const totalDuration = computed(() =>
  episodes.value.reduce((acc, ep) => acc + (ep.duration || 0), 0),
)

const collected = computed(() => column.value?.nEpisodes ?? episodes.value.length)
const missing = computed(() => Math.max(0, (column.value?.episodeTotal || 0) - collected.value))

const lastUpdated = computed(() =>
  episodes.value.map(ep => ep.publishedAt).filter(Boolean).sort().reverse()[0] || '',
)

const { state: audioState, toggle } = useAudioPlayerWithPlaylist()

// 播放列表只装本栏目，连播不会跳到别的系列
const playlistItems = computed<PlaylistItem[]>(() =>
  episodes.value.map(ep => ({
    id: ep.awemeId,
    title: ep.title,
    audioUrl: ep.audioUrl,
    duration: ep.duration,
  })),
)

const playingEpisodeId = computed(() => {
  if (!audioState.playing && !audioState.started) {
    return ''
  }
  return episodes.value.find(ep => ep.audioUrl === audioState.src)?.awemeId || ''
})

function playEpisode(index: number) {
  const ep = episodes.value[index]
  if (!ep) {
    return
  }
  if (ep.audioUrl === audioState.src) {
    toggle()
    return
  }
  setPlaylist(playlistItems.value, index)
  playByIndex(index)
}

function playFromStart() {
  const first = playlistItems.value.findIndex(item => !!item.audioUrl)
  if (first !== -1) {
    playEpisode(first)
  }
}

function playLatest() {
  for (let i = playlistItems.value.length - 1; i >= 0; i--) {
    if (playlistItems.value[i].audioUrl) {
      playEpisode(i)
      return
    }
  }
}

function openEpisode(episode: ColumnEpisode) {
  uni.navigateTo({ url: `/pages/article/article?id=${encodeURIComponent(episode.awemeId)}` })
}

function goBack() {
  const pages = getCurrentPages()
  if (pages.length > 1) {
    uni.navigateBack()
    return
  }
  uni.reLaunch({ url: '/pages/columns/columns' })
}

onLoad((options) => {
  columnId.value = options?.id ? decodeURIComponent(String(options.id)) : ''
})
</script>

<template>
  <view v-if="column" class="page">
    <view class="topbar">
      <view class="back-btn" @tap="goBack">
        <view class="back-icon i-lucide-chevron-left" />
      </view>
      <text class="topbar-title">{{ column.name }}</text>
    </view>

    <scroll-view class="content-scroll" scroll-y :show-scrollbar="false">
      <view class="hero px-4">
        <view class="cover-box">
          <image
            v-if="column.cover && !coverFailed"
            class="cover-img"
            :src="column.cover"
            mode="aspectFill"
            @error="coverFailed = true"
          />
          <view v-else class="cover-fallback">
            <view class="fallback-icon i-lucide-library" />
          </view>
        </view>

        <view class="hero-body">
          <text class="hero-name">{{ column.name }}</text>
          <text class="hero-account">{{ column.accountName }}</text>
          <view class="hero-stats">
            <text class="stat">{{ collected }} 集</text>
            <text class="stat">{{ formatDuration(totalDuration) }}</text>
            <text v-if="lastUpdated" class="stat">{{ formatDate(lastUpdated) }}</text>
          </view>
        </view>
      </view>

      <view class="actions px-4">
        <view class="action primary" @tap="playFromStart">
          <view class="action-icon i-lucide-play" />
          <text>连续播放</text>
        </view>
        <view class="action" @tap="playLatest">
          <view class="action-icon i-lucide-rewind" />
          <text>最新一集</text>
        </view>
      </view>

      <view v-if="missing > 0" class="progress-note px-4">
        <view class="note-icon i-lucide-loader-circle" />
        <text>本系列共 {{ column.episodeTotal }} 集，已采集 {{ collected }} 集，其余在流水线排队中。</text>
      </view>

      <view class="list-area px-4">
        <view class="list-head">
          <text class="list-title">剧集列表</text>
          <text class="list-sub">点标题阅读，点右侧按钮收听</text>
        </view>

        <EpisodeRow
          v-for="(episode, index) in episodes"
          :key="episode.awemeId"
          :episode="episode"
          :index="index"
          :is-playing="episode.awemeId === playingEpisodeId"
          @play="playEpisode"
          @open="openEpisode"
        />

        <view v-if="!episodes.length" class="empty">
          <text class="empty-desc">这个栏目还没有已生成正文的集。</text>
        </view>
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

.topbar {
  display: flex;
  height: 92rpx;
  flex-shrink: 0;
  align-items: center;
  gap: 14rpx;
  padding: 0 24rpx;
  background: #f3f5f2;
}

.back-btn {
  display: flex;
  width: 62rpx;
  height: 62rpx;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  border: 1rpx solid #e0e6e1;
  border-radius: 999rpx;
  background: #ffffff;
}

.back-icon {
  width: 34rpx;
  height: 34rpx;
  color: #1f5146;
}

.topbar-title {
  overflow: hidden;
  color: #18221e;
  font-size: 30rpx;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content-scroll {
  flex: 1;
  min-height: 0;
  height: 0;
}

.hero {
  display: flex;
  gap: 24rpx;
  margin-top: 20rpx;
  padding-bottom: 30rpx;
  border-bottom: 1rpx solid #e4e9e4;
}

.cover-box {
  flex-shrink: 0;
  width: 168rpx;
  height: 224rpx;
  overflow: hidden;
  border-radius: 14rpx;
  background: #eef2ee;
}

.cover-img {
  width: 168rpx;
  height: 224rpx;
}

.cover-fallback {
  display: flex;
  width: 168rpx;
  height: 224rpx;
  align-items: center;
  justify-content: center;
  background: #e7ede8;
}

.fallback-icon {
  width: 56rpx;
  height: 56rpx;
  color: #8b978f;
}

.hero-body {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.hero-name {
  color: #18221e;
  font-size: 36rpx;
  font-weight: 800;
  line-height: 1.35;
}

.hero-account {
  margin-top: 10rpx;
  color: #8b958f;
  font-size: 24rpx;
}

.hero-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
  margin-top: auto;
  padding-top: 16rpx;
}

.stat {
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
  background: #e9efe9;
  color: #4c5a52;
  font-size: 21rpx;
}

.actions {
  display: flex;
  gap: 16rpx;
  margin-top: 28rpx;
}

.action {
  display: flex;
  height: 78rpx;
  flex: 1;
  align-items: center;
  justify-content: center;
  gap: 10rpx;
  border: 1rpx solid #dfe6e1;
  border-radius: 999rpx;
  background: #ffffff;
  color: #1f5146;
  font-size: 26rpx;
  font-weight: 700;
}

.action.primary {
  border-color: #1f5146;
  background: #1f5146;
  color: #ffffff;
}

.action:active {
  opacity: 0.85;
}

.action-icon {
  width: 30rpx;
  height: 30rpx;
}

.progress-note {
  display: flex;
  align-items: center;
  gap: 12rpx;
  margin-top: 22rpx;
  color: #8b958f;
  font-size: 22rpx;
  line-height: 1.5;
}

.note-icon {
  flex-shrink: 0;
  width: 26rpx;
  height: 26rpx;
}

.list-area {
  margin-top: 34rpx;
  padding-bottom: 200rpx;
}

.list-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 8rpx;
}

.list-title {
  color: #18221e;
  font-size: 26rpx;
  font-weight: 800;
}

.list-sub {
  color: #9aa39d;
  font-size: 21rpx;
}

.empty {
  padding: 60rpx 0;
  text-align: center;
}

.empty-desc {
  color: #8b958f;
  font-size: 25rpx;
}
</style>
