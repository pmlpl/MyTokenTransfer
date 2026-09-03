<template>
  <div class="data-card">
    <div v-if="title || $slots.header" class="header">
      <div class="header-text">
        <span class="title">{{ title }}</span>
        <span v-if="subtitle" class="subtitle">{{ subtitle }}</span>
      </div>
      <div v-if="$slots.actions" class="header-actions">
        <slot name="actions" />
      </div>
    </div>
    <div class="body">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
// v2 通用数据卡片：带标题栏 + 右侧操作 + 内容区
import { tokens } from '../tokens'

defineProps<{
  title?: string
  subtitle?: string
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
  padding: 16px 20px;
  border-bottom: 1px solid var(--app-border, #e5e7eb);
}
.header-text {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.title {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text-primary, #0f172a);
}
.subtitle {
  font-size: 12px;
  color: var(--app-text-secondary, #64748b);
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.body {
  padding: 20px;
}
</style>
