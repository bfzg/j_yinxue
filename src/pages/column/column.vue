<script lang="ts" setup>
import { computed, ref } from 'vue'
import EpisodeRow from './components/EpisodeRow.vue'
import { useAudioPlayerState } from '@/composables/useAudioPlayer'
import { useCapsuleInset, useTopInset } from '@/composables/useSafeArea'
import { ensureSiteData, findColumn, loadColumnEpisodes, useSiteData } from '@/composables/useSiteData'
import type { Column, ColumnEpisode } from '@/types/column'

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

const { site } = useSiteData()

const topInset = useTopInset()
const capsuleInset = useCapsuleInset()

// 标题栏内联样式：让出状态栏高度，同时避开右上角胶囊按钮
const topbarStyle = computed(() => ({
  paddingTop: topInset.value,
  paddingRight: `calc(12rpx + ${capsuleInset.value})`,
}))

const columnId = ref('')
const coverFailed = ref(false)
const episodes = ref<ColumnEpisode[]>([])
const episodesLoading = ref(false)

const column = computed<Column | undefined>(() => {
  return findColumn(columnId.value) || site.columns[0]
})

const collected = computed(() => column.value?.nEpisodes ?? episodes.value.length)
const missing = computed(() => Math.max(0, (column.value?.episodeTotal || 0) - collected.value))

const audioState = useAudioPlayerState()

// 列表页只负责选集，高亮当前正在听的一集
const playingEpisodeId = computed(() => {
  if (!audioState.playing && !audioState.started) {
    return ''
  }
  return episodes.value.find(ep => ep.audioUrl === audioState.src)?.awemeId || ''
})

function openEpisode(episode: ColumnEpisode) {
  uni.navigateTo({ url: `/pages/article/article?id=${encodeURIComponent(episode.awemeId)}` })
}

function goBack() {
  const pages = getCurrentPages()
  if (pages.length > 1) {
    uni.navigateBack()
    return
  }
  uni.reLaunch({ url: '/pages/index/index' })
}

async function loadEpisodes() {
  if (!column.value?.id) {
    episodes.value = []
    return
  }
  episodesLoading.value = true
  try {
    episodes.value = await loadColumnEpisodes(column.value.id)
  }
  finally {
    episodesLoading.value = false
  }
}

onLoad(async (options) => {
  columnId.value = options?.id ? decodeURIComponent(String(options.id)) : ''
  await ensureSiteData()
  await loadEpisodes()
})
</script>

<template>
  <view v-if="column" class="page">
    <view class="topbar" :style="topbarStyle">
      <view class="back-btn" @tap="goBack">
        <view class="back-icon i-lucide-chevron-left" />
      </view>
      <text class="topbar-title">{{ column.name }}</text>
    </view>

    <scroll-view class="content-scroll" scroll-y :show-scrollbar="false">
      <view class="hero px-4">
        <view class="cover-box">
          <image
            v-if="column.cover && !coverFailed" class="cover-img" :src="column.cover" mode="aspectFill"
            @error="coverFailed = true"
          />
          <view v-else class="cover-fallback">
            <view class="fallback-icon i-lucide-library" />
          </view>
        </view>

        <view class="hero-body">
          <text class="hero-name">{{ column.name }}</text>
          <text class="hero-account">{{ column.accountName }}</text>
        </view>
      </view>

      <view class="list-area px-4">
        <view class="list-head">
          <text class="list-title">文章列表</text>
        </view>

        <EpisodeRow
          v-for="(episode, index) in episodes" :key="episode.awemeId" :episode="episode" :index="index"
          :is-playing="episode.awemeId === playingEpisodeId" @open="openEpisode"
        />

        <view v-if="!episodes.length" class="empty">
          <text class="empty-desc">{{ episodesLoading ? '剧集加载中…' : '这个栏目还没有已生成正文的集。' }}</text>
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
  box-sizing: content-box;
  flex-shrink: 0;
  align-items: center;
  gap: 14rpx;
  padding: 0 24rpx;
  background: #f3f5f2;
  margin-bottom: 20rpx;
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

.progress-note {
  display: flex;
  align-items: center;
  gap: 12rpx;
  margin-top: 28rpx;
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

.empty {
  padding: 60rpx 0;
  text-align: center;
}

.empty-desc {
  color: #8b958f;
  font-size: 25rpx;
}
</style>
