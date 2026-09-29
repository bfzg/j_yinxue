<script lang="ts" setup>
import type { Article } from '@/types/article'
import HomeArticleCard from './HomeArticleCard.vue'

defineOptions({
  name: 'HomeArticleList',
})

defineProps<{
  articles: Article[]
  playingId?: string
}>()

const emit = defineEmits<{
  (e: 'open', article: Article): void
}>()
</script>

<template>
  <view class="article-list">
    <HomeArticleCard
      v-for="(article, index) in articles"
      :key="article.id"
      :article="article"
      :index="index"
      :is-playing="article.id === playingId"
      @open="emit('open', $event)"
    />
  </view>
</template>

<style lang="scss" scoped>
.article-list {
  display: flex;
  flex-direction: column;
  gap: 18rpx;
}
</style>
