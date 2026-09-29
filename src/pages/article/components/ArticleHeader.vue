<script lang="ts" setup>
defineOptions({
  name: 'ArticleHeader',
})

defineProps<{
  category: string
  title: string
  publishedAt: string
}>()

function formatDate(value: string) {
  return value ? value.replace(/-/g, '.') : '待更新'
}
</script>

<template>
  <view class="article-head">
    <text class="category">{{ category }}</text>
    <text class="title">{{ title }}</text>
    <view class="meta">
      <text>{{ formatDate(publishedAt) }}</text>
      <text>九哥隐学</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.article-head {
  padding: 54rpx 0 38rpx;
}

.category {
  color: #d35d42;
  font-size: 24rpx;
  font-weight: 700;
  letter-spacing: 2rpx;
}

.title {
  display: block;
  margin-top: 22rpx;
  color: #18221e;
  font-size: 48rpx;
  font-weight: 800;
  line-height: 1.35;
}

.meta {
  display: flex;
  align-items: center;
  gap: 24rpx;
  margin-top: 24rpx;
  color: #99a39d;
  font-size: 23rpx;
}
</style>
