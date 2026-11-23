<template>
  <div class="data-card">
    <!-- 有标题：标准卡头；只有操作：操作条，不加分隔线与标题位 -->
    <div v-if="title" class="header">
      <span class="title">{{ title }}</span>
      <div v-if="$slots.actions" class="header-actions">
        <slot name="actions" />
      </div>
    </div>
    <div v-else-if="$slots.actions" class="toolbar">
      <slot name="actions" />
    </div>
    <div class="body">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
// 通用数据卡片：可选标题栏 + 右侧操作 + 内容区
import { tokens } from '../tokens'

defineProps<{
  title?: string
}>()
</script>

<style scoped>
.data-card {
  background: var(--app-card-bg, #fff);
  border: 1px solid var(--app-border, #e5e7eb);
  border-radius: v-bind('tokens.radius.card');
  box-shadow: v-bind('tokens.shadow.card');
  overflow: hidden;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--app-border, #e5e7eb);
}
.title {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text-primary, #0f172a);
}
.header-actions,
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.toolbar {
  justify-content: flex-end;
  padding: 16px 20px 0;
}
.body {
  padding: 20px;
}
</style>
