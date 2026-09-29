<script lang="ts" setup>
defineOptions({
  name: 'ArticleBody',
})

defineProps<{
  articleId: string
  summary: string
  paragraphs: string[]
  isLoading: boolean
  loadError: string
}>()
</script>

<template>
  <view class="article-body">
    <text v-if="summary" class="summary">{{ summary }}</text>
    <text v-if="isLoading" class="empty-content">
      正文加载中……
    </text>
    <text v-else-if="loadError" class="empty-content">
      {{ loadError }}
    </text>
    <text v-else-if="!paragraphs.length" class="empty-content">
      正文内容正在整理，后续将持续更新。
    </text>
    <text
      v-for="(paragraph, index) in paragraphs"
      :key="`${articleId}-${index}`"
      class="paragraph"
    >
      {{ paragraph }}
    </text>
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

.paragraph + .paragraph {
  margin-top: 34rpx;
}
</style>
