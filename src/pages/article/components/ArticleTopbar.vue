<script lang="ts" setup>
defineOptions({
  name: 'ArticleTopbar',
})

const props = defineProps<{
  title: string
  /** 右上角胶囊按钮宽度，避免标题被遮住 */
  rightInset?: string
}>()

const emit = defineEmits<{
  (e: 'back'): void
}>()
</script>

<template>
  <view class="topbar pt-2" :style="{ paddingRight: props.rightInset || '0px' }">
    <view class="back-button" @tap="emit('back')">
      <view class="back-icon i-lucide-chevron-left" />
    </view>
    <text class="topbar-title">{{ title }}</text>
    <view class="topbar-space" />
  </view>
</template>

<style lang="scss" scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.back-button {
  color: #1f5146;
}

.back-icon {
  width: 46rpx;
  height: 46rpx;
}

.topbar-title {
  max-width: 480rpx;
  overflow: hidden;
  color: #66726a;
  font-size: 26rpx;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-space {
  width: 64rpx;
}
</style>
