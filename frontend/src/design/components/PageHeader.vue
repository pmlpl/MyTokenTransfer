<template>
  <div class="page-header">
    <div class="title-block">
      <h2 class="title">{{ title }}</h2>
      <p v-if="subtitle" class="subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
// v2 通用页头：标题 + 副标题 + 右侧操作区
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--app-text-primary, #0f172a);
  letter-spacing: 0.2px;
}
.subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--app-text-secondary, #64748b);
}
.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
