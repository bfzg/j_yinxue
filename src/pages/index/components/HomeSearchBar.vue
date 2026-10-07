<script lang="ts" setup>
import { computed } from 'vue'

defineOptions({
  name: 'HomeSearchBar',
})

const props = defineProps<{
  modelValue: string
  focused: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'focus'): void
  (e: 'blur'): void
}>()

const searchText = computed({
  get: () => props.modelValue,
  set: value => emit('update:modelValue', value),
})
</script>

<template>
  <view class="search-box" :class="{ focused }">
    <view class="search-icon i-lucide-search" />
    <input
      v-model="searchText"
      class="search-input"
      confirm-type="search"
      placeholder="搜索文章"
      placeholder-class="search-placeholder"
      @focus="emit('focus')"
      @blur="emit('blur')"
    >
    <view v-if="modelValue" class="clear-button center" @tap="searchText = ''">
      <view class="clear-icon i-lucide-x" />
    </view>
  </view>
</template>

<style lang="scss" scoped>
.search-box {
  display: flex;
  width: 100%;
  min-width: 0;
  align-items: center;
  height: 72rpx;
  box-sizing: border-box;
  padding: 0 24rpx;
  border: 1rpx solid #dfe6e1;
  border-radius: 999rpx;
  background: #ffffff;
  box-shadow: 0 12rpx 30rpx rgba(43, 67, 57, 0.05);
  transition: width 240ms ease;
}

.search-icon {
  flex-shrink: 0;
  width: 34rpx;
  height: 34rpx;
  color: #66746c;
}

.search-input {
  flex: 1;
  height: 72rpx;
  margin-left: 16rpx;
  color: #18221e;
  font-size: 28rpx;
}

.search-placeholder {
  color: #a4ada8;
}

.clear-button {
  flex-shrink: 0;
  width: 40rpx;
  height: 40rpx;
}

.clear-icon {
  width: 30rpx;
  height: 30rpx;
  color: #8d9891;
}
</style>
