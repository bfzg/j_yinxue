<script lang="ts" setup>
import { ref } from 'vue'

defineOptions({
  name: 'ArticleActions',
})

const isFavorite = ref(false)

function toggleFavorite() {
  isFavorite.value = !isFavorite.value
  uni.showToast({
    title: isFavorite.value ? '已收藏' : '已取消收藏',
    icon: 'none',
  })
}

function shareArticle() {
  uni.showToast({
    title: '请使用右上角菜单分享',
    icon: 'none',
  })
}
</script>

<template>
  <view class="actions">
    <view
      class="action-button center"
      :aria-label="isFavorite ? '取消收藏' : '收藏'"
      @tap="toggleFavorite"
    >
      <view class="action-icon i-lucide-heart" :class="{ selected: isFavorite }" />
    </view>
    <view class="action-button center" aria-label="分享" @tap="shareArticle">
      <view class="action-icon i-lucide-share-2" />
    </view>
  </view>
</template>

<style lang="scss" scoped>
.actions {
  display: flex;
  justify-content: center;
  gap: 22rpx;
  margin-top: 70rpx;
}

.action-button {
  width: 76rpx;
  height: 76rpx;
  box-sizing: border-box;
  border: 1rpx solid #dfe7e1;
  border-radius: 38rpx;
  color: #426258;
}

.action-icon {
  width: 34rpx;
  height: 34rpx;
}

.action-icon.selected {
  color: #d35d42;
}
</style>
