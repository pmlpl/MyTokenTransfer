<template>
  <div class="page-header">
    <h2 class="title">{{ title }}</h2>
    <div v-if="$slots.actions" class="actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
// 通用页头：标题 + 右侧操作区。副标题已移除——页面靠内容自解释。
defineProps<{
  title: string
}>()
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 34px;
  margin-bottom: 20px;
}
.title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--app-text-primary, #0f172a);
  letter-spacing: 0.2px;
}
.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
