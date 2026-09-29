<script lang="ts" setup>
defineOptions({
  name: 'HomeCategoryTabs',
})

defineProps<{
  categories: string[]
  activeCategory: string
}>()

const emit = defineEmits<{
  (e: 'update:activeCategory', value: string): void
}>()
</script>

<template>
  <scroll-view class="category-scroll pl-4" scroll-x :show-scrollbar="false">
    <view class="category-list">
      <text
        v-for="category in categories"
        :key="category"
        class="category-item"
        :class="{ selected: activeCategory === category }"
        @tap="emit('update:activeCategory', category)"
      >
        {{ category }}
      </text>
    </view>
  </scroll-view>
</template>

<style lang="scss" scoped>
.category-scroll {
  margin: 38rpx -32rpx 0;
  white-space: nowrap;
}

.category-list {
  display: inline-flex;
  gap: 42rpx;
  padding: 0 32rpx 12rpx;
}

.category-item {
  position: relative;
  color: #89938d;
  font-size: 32rpx;
  font-weight: 500;
  line-height: 1.6;
}

.category-item.selected {
  color: #1f5146;
  font-weight: 700;
}

.category-item.selected::after {
  position: absolute;
  right: 0;
  bottom: -12rpx;
  left: 0;
  height: 6rpx;
  border-radius: 6rpx;
  background: #1f5146;
  content: '';
}
</style>
