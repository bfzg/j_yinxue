<script lang="ts" setup>
import { computed, ref } from 'vue'
import columnsDoc from '@/static/data/columns.json'
import ColumnCard from './components/ColumnCard.vue'
import type { Column, ColumnDoc } from '@/types/column'

defineOptions({
  name: 'ColumnsHome',
})

definePage({
  style: {
    navigationStyle: 'custom',
    navigationBarTitleText: '',
    navigationBarBackgroundColor: '#f3f5f2',
    navigationBarTextStyle: 'black',
  },
})

const allColumns = computed<Column[]>(() => (columnsDoc as unknown as ColumnDoc).items || [])

const accounts = computed(() => Array.from(new Set(allColumns.value.map(item => item.accountName || '未归账号'))))

const activeAccount = ref('全部')

// 系列在前、单篇桶在后，账号内按采集时的 sort 顺序
function isLoose(column: Column) {
  return (column.name || '').includes('单篇')
}

const groups = computed(() => {
  const source = allColumns.value.filter(
    item => activeAccount.value === '全部' || (item.accountName || '未归账号') === activeAccount.value,
  )
  const map = new Map<string, Column[]>()
  source.forEach((column) => {
    const key = column.accountName || '未归账号'
    if (!map.has(key)) {
      map.set(key, [])
    }
    map.get(key)!.push(column)
  })
  return Array.from(map.entries()).map(([accountName, list]) => ({
    accountName,
    columns: [...list].sort((a, b) => Number(isLoose(a)) - Number(isLoose(b))),
  }))
})

const episodeCount = computed(() => allColumns.value.reduce((acc, item) => acc + (item.episodes?.length || 0), 0))

function goBack() {
  const pages = getCurrentPages()
  if (pages.length > 1) {
    uni.navigateBack()
    return
  }
  uni.reLaunch({ url: '/pages/index/index' })
}

function openColumn(column: Column) {
  uni.navigateTo({ url: `/pages/column/column?id=${encodeURIComponent(column.id)}` })
}
</script>

<template>
  <view class="page">
    <view class="header">
      <view class="top-space" />

      <view class="nav-row px-4">
        <view class="back-btn" @tap="goBack">
          <view class="back-icon i-lucide-chevron-left" />
        </view>
        <view class="nav-title-box">
          <text class="nav-title">栏目</text>
          <text class="nav-sub">{{ groups.length }} 个账号 · {{ episodeCount }} 集</text>
        </view>
      </view>

      <scroll-view v-if="accounts.length > 1" class="account-scroll pl-4" scroll-x :show-scrollbar="false">
        <view class="account-list">
          <text
            v-for="name in ['全部', ...accounts]"
            :key="name"
            class="account-item"
            :class="{ selected: activeAccount === name }"
            @tap="activeAccount = name"
          >
            {{ name }}
          </text>
        </view>
      </scroll-view>
    </view>

    <scroll-view class="content-scroll" scroll-y :show-scrollbar="false">
      <view v-if="groups.length" class="list-area px-4 pb-40">
        <view v-for="group in groups" :key="group.accountName" class="group">
          <view class="group-head">
            <text class="group-title">{{ group.accountName }}</text>
            <text class="group-count">{{ group.columns.length }} 个栏目</text>
          </view>

          <view class="group-list">
            <ColumnCard
              v-for="column in group.columns"
              :key="column.id"
              :column="column"
              @open="openColumn"
            />
          </view>
        </view>
      </view>

      <view v-else class="empty">
        <view class="empty-icon i-lucide-library" />
        <text class="empty-title">还没有栏目</text>
        <text class="empty-desc">采集流水线导出数据后，这里会出现按标题归好的系列。</text>
      </view>
    </scroll-view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  box-sizing: border-box;
  background: #f3f5f2;
  color: #18221e;
}

.header {
  flex-shrink: 0;
  background: #f3f5f2;
}

.content-scroll {
  flex: 1;
  min-height: 0;
  height: 0;
}

.top-space {
  height: 92rpx;
}

.nav-row {
  display: flex;
  align-items: center;
  gap: 14rpx;
}

.back-btn {
  display: flex;
  width: 62rpx;
  height: 62rpx;
  align-items: center;
  justify-content: center;
  border: 1rpx solid #e0e6e1;
  border-radius: 999rpx;
  background: #ffffff;
}

.back-icon {
  width: 34rpx;
  height: 34rpx;
  color: #1f5146;
}

.nav-title-box {
  display: flex;
  flex-direction: column;
}

.nav-title {
  color: #18221e;
  font-size: 34rpx;
  font-weight: 800;
}

.nav-sub {
  color: #8b958f;
  font-size: 21rpx;
}

.account-scroll {
  margin: 30rpx -32rpx 0;
  white-space: nowrap;
}

.account-list {
  display: inline-flex;
  gap: 18rpx;
  padding: 0 32rpx 14rpx;
}

.account-item {
  flex-shrink: 0;
  padding: 8rpx 24rpx;
  white-space: nowrap;
  border: 1rpx solid #dfe6e1;
  border-radius: 999rpx;
  background: #ffffff;
  color: #758179;
  font-size: 24rpx;
}

.account-item.selected {
  border-color: #1f5146;
  background: #1f5146;
  color: #ffffff;
  font-weight: 700;
}

.list-area {
  min-height: 100%;
}

.group {
  margin-top: 40rpx;
}

.group-head {
  display: flex;
  align-items: baseline;
  gap: 14rpx;
  margin-bottom: 18rpx;
}

.group-title {
  color: #18221e;
  font-size: 26rpx;
  font-weight: 800;
}

.group-count {
  color: #9aa39d;
  font-size: 21rpx;
}

.group-list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.empty {
  display: flex;
  min-height: 60vh;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14rpx;
}

.empty-icon {
  width: 74rpx;
  height: 74rpx;
  color: #c3ccc5;
}

.empty-title {
  color: #18221e;
  font-size: 30rpx;
  font-weight: 800;
}

.empty-desc {
  width: 460rpx;
  color: #8b958f;
  font-size: 24rpx;
  text-align: center;
}
</style>
