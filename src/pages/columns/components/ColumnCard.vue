<script lang="ts" setup>
import { computed, ref } from 'vue'
import { formatDuration } from '@/utils/format'
import type { Column } from '@/types/column'

defineOptions({
  name: 'ColumnCard',
})

const props = defineProps<{
  column: Column
}>()

const emit = defineEmits<{
  (e: 'open', column: Column): void
}>()

const coverFailed = ref(false)

const collected = computed(() => props.column.nEpisodes ?? props.column.episodes?.length ?? 0)

const totalDuration = computed(() => {
  const sum = (props.column.episodes || []).reduce((acc, ep) => acc + (ep.duration || 0), 0)
  return formatDuration(sum)
})

// 单篇桶没有集数概念，换个图标和系列区分
const isLoose = computed(() => (props.column.name || '').includes('单篇'))
</script>

<template>
  <view class="column-card" @tap="emit('open', column)">
    <view class="cover-box">
      <image
        v-if="column.cover && !coverFailed"
        class="cover-img"
        :src="column.cover"
        mode="aspectFill"
        @error="coverFailed = true"
      />
      <view v-else class="cover-fallback">
        <view class="fallback-icon" :class="isLoose ? 'i-lucide-file-text' : 'i-lucide-library'" />
      </view>
    </view>

    <view class="card-body">
      <text class="column-name">{{ column.name }}</text>
      <text class="column-account">{{ column.accountName }}</text>

      <view class="card-foot">
        <text class="foot-count">{{ collected }} 集</text>
        <text v-if="column.episodeTotal > collected" class="foot-total">全 {{ column.episodeTotal }} 集</text>
        <text class="foot-dur">{{ totalDuration }}</text>
        <view class="foot-go">
          <view class="go-icon i-lucide-chevron-right" />
        </view>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.column-card {
  display: flex;
  gap: 22rpx;
  box-sizing: border-box;
  padding: 24rpx;
  border: 1rpx solid #e0e6e1;
  border-radius: 18rpx;
  background: #ffffff;
}

.column-card:active {
  background: #f8faf8;
}

.cover-box {
  flex-shrink: 0;
  width: 128rpx;
  height: 170rpx;
  overflow: hidden;
  border-radius: 12rpx;
  background: #eef2ee;
}

.cover-img {
  width: 128rpx;
  height: 170rpx;
}

.cover-fallback {
  display: flex;
  width: 128rpx;
  height: 170rpx;
  align-items: center;
  justify-content: center;
  background: #e7ede8;
}

.fallback-icon {
  width: 46rpx;
  height: 46rpx;
  color: #8b978f;
}

.card-body {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.column-name {
  color: #18221e;
  font-size: 30rpx;
  font-weight: 800;
  line-height: 1.4;
}

.column-account {
  margin-top: 8rpx;
  color: #8b958f;
  font-size: 22rpx;
}

.card-foot {
  display: flex;
  align-items: center;
  gap: 14rpx;
  margin-top: auto;
  padding-top: 16rpx;
}

.foot-count {
  padding: 4rpx 14rpx;
  border-radius: 999rpx;
  background: #eef4ef;
  color: #1f5146;
  font-size: 21rpx;
  font-weight: 700;
}

.foot-total,
.foot-dur {
  color: #8b958f;
  font-size: 21rpx;
}

.foot-go {
  display: flex;
  margin-left: auto;
  align-items: center;
  color: #d35d42;
}

.go-icon {
  width: 28rpx;
  height: 28rpx;
}
</style>
