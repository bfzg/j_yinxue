<script lang="ts" setup>
defineOptions({
  name: 'HomeEmptyState',
})
</script>

<template>
  <view class="empty-state">
    <text class="empty-title">没有找到相关文章</text>
    <text class="empty-copy">换个关键词试试。</text>
  </view>
</template>

<style lang="scss" scoped>
.empty-state {
  padding: 100rpx 0;
  color: #7d8981;
  text-align: center;
}

.empty-title,
.empty-copy {
  display: block;
}

.empty-title {
  color: #536158;
  font-size: 30rpx;
  font-weight: 700;
}

.empty-copy {
  margin-top: 12rpx;
  font-size: 25rpx;
}
</style>
