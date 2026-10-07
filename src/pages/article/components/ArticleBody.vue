<script lang="ts" setup>
import type { ArticleBlock } from '@/types/article'

defineOptions({
  name: 'ArticleBody',
})

defineProps<{
  articleId: string
  summary: string
  blocks: ArticleBlock[]
  isLoading: boolean
  loadError: string
}>()
</script>

<template>
  <view class="article-body">
    <text v-if="summary" class="summary">
      {{ summary }}
    </text>
    <text v-if="isLoading" class="empty-content">
      正文加载中……
    </text>
    <text v-else-if="loadError" class="empty-content">
      {{ loadError }}
    </text>
    <text v-else-if="!blocks.length" class="empty-content">
      正文内容正在整理，后续将持续更新。
    </text>

    <view v-else>
      <block v-for="(block, index) in blocks" :key="`${articleId}-${index}`">
        <view v-if="block.type === 'heading'" class="heading">
          <text class="heading-text">{{ block.text }}</text>
        </view>
        <text v-else-if="block.type === 'paragraph'" class="paragraph">
          {{ block.text }}
        </text>
        <view v-else-if="block.type === 'quote'" class="quote">
          <text class="quote-text">{{ block.text }}</text>
        </view>
        <view v-else-if="block.type === 'list'" class="list">
          <view v-for="(item, i) in block.items" :key="i" class="list-item">
            <text class="bullet">·</text>
            <text class="list-text">{{ item }}</text>
          </view>
        </view>
        <view v-else-if="block.type === 'divider'" class="divider" />
      </block>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.article-body {
  padding: 48rpx 0 20rpx;
}

.summary,
.empty-content,
.paragraph {
  display: block;
  color: #4c5951;
  font-size: 31rpx;
  line-height: 2;
  white-space: pre-wrap;
}

.summary {
  margin-bottom: 34rpx;
  color: #69766e;
  font-size: 28rpx;
}

.empty-content {
  color: #9aa59e;
}

.paragraph + .paragraph,
.heading + .paragraph {
  margin-top: 30rpx;
}

.heading {
  display: flex;
  align-items: center;
  gap: 16rpx;
  margin: 62rpx 0 24rpx;
}

.heading-text {
  color: #18221e;
  font-size: 34rpx;
  font-weight: 700;
  line-height: 1.5;
}

.heading::before {
  width: 6rpx;
  height: 30rpx;
  background: #d35d42;
  border-radius: 3rpx;
  content: '';
}

.quote {
  margin: 36rpx 0;
  padding: 26rpx 30rpx;
  background: #f6faf7;
  border-left: 5rpx solid #1f5146;
  border-radius: 0 10rpx 10rpx 0;
}

.quote-text {
  color: #33413a;
  font-size: 29rpx;
  line-height: 1.9;
}

.list {
  margin: 32rpx 0;
}

.list-item {
  display: flex;
  align-items: flex-start;
  gap: 16rpx;
  margin-top: 18rpx;
}

.bullet {
  flex-shrink: 0;
  color: #1f5146;
  font-size: 32rpx;
  line-height: 1.9;
}

.list-text {
  color: #4c5951;
  font-size: 30rpx;
  line-height: 1.9;
}

.divider {
  height: 1rpx;
  margin: 56rpx 0;
  background: #e5eae6;
}
</style>
