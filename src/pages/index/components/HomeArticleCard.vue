<script lang="ts" setup>
import type { Article } from '@/types/article'

defineOptions({
  name: 'HomeArticleCard',
})

defineProps<{
  article: Article
  index: number
  isPlaying?: boolean
}>()

const emit = defineEmits<{
  (e: 'open', article: Article): void
}>()

function formatDate(value: string) {
  return value ? value.replace(/-/g, '.') : '待更新'
}
</script>

<template>
  <view class="article-card" :class="{ playing: isPlaying }" @tap="emit('open', article)">
    <view class="article-number">
      {{ String(index + 1).padStart(2, '0') }}
    </view>
    <view class="article-content">
      <view class="article-meta">
        <text>{{ article.category }}</text>
        <text>{{ formatDate(article.publishedAt) }}</text>
      </view>
      <text class="article-title">{{ article.title }}</text>
      <text class="article-summary">{{ article.summary || '打开文章，开始阅读。' }}</text>
      <view class="flex items-center justify-end gap-2 pt-3">
        <view class="pt-4">
          <view v-if="isPlaying" class="playing-wave" aria-hidden="true">
            <view class="wave-line" />
            <view class="wave-line" />
            <view class="wave-line" />
          </view>
        </view>
        <view class="read-link">
          <view>{{ isPlaying ? '正在播放' : '阅读全文' }}</view>
          <view class="read-arrow i-lucide-arrow-right" />
        </view>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.article-card {
  position: relative;
  overflow: hidden;
  display: flex;
  gap: 22rpx;
  min-height: 238rpx;
  box-sizing: border-box;
  padding: 28rpx 24rpx 26rpx;
  border: 1rpx solid #e0e6e1;
  border-radius: 18rpx;
  background: #ffffff;
}

.playing-wave {
  display: flex;
  gap: 5rpx;
  align-items: center;
  transform: translateY(-50%);
}

.wave-line {
  width: 5rpx;
  height: 36rpx;
  border-radius: 999rpx;
  background: #1f5146;
  animation: playing-wave 1s ease-in-out infinite;
  transform-origin: center;
}

.wave-line:nth-child(2) {
  animation-delay: 0.18s;
}

.wave-line:nth-child(3) {
  animation-delay: 0.36s;
}

@keyframes playing-wave {
  0%,
  100% {
    opacity: 0.45;
    transform: scaleY(0.28);
  }

  50% {
    opacity: 1;
    transform: scaleY(1);
  }
}

.article-card:active {
  background: #f8faf8;
}

.article-number {
  flex-shrink: 0;
  border-radius: 50%;
  color: #1f5146;
  font-size: 22rpx;
  font-weight: 700;
  text-align: center;
}

.article-content {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.article-meta {
  display: flex;
  justify-content: space-between;
  color: #8b958f;
  font-size: 22rpx;
}

.article-title {
  margin-top: 18rpx;
  color: #18221e;
  font-size: 32rpx;
  font-weight: 800;
  line-height: 1.4;
}

.article-summary {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 12rpx;
  color: #758179;
  font-size: 26rpx;
  line-height: 1.55;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.read-link {
  display: flex;
  justify-content: end;
  align-items: center;
  gap: 8rpx;
  color: #d35d42;
  font-size: 28rpx;
  font-weight: 700;
}

.read-arrow {
  width: 28rpx;
  height: 28rpx;
}
</style>
