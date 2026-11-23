<template>
  <div class="empty-state">
    <div class="icon-wrap">
      <slot name="icon">
        <svg viewBox="0 0 80 80" width="64" height="64" fill="none">
          <rect x="10" y="14" width="60" height="52" rx="6" stroke="currentColor" stroke-width="2" opacity="0.25" />
          <path d="M22 30h36M22 40h36M22 50h22" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity="0.2" />
        </svg>
      </slot>
    </div>
    <div class="title">{{ title }}</div>
    <div v-if="$slots.actions" class="actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
// 空状态：图标 + 一句说明 + 可选操作按钮。
// 不要描述"为什么空/怎么修"——能一键修的就给按钮，说不清的是设计问题。
defineProps<{
  title: string
}>()
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  color: var(--app-text-secondary, #64748b);
}
.icon-wrap {
  margin-bottom: 16px;
  color: var(--app-border, #cbd5e1);
}
.title {
  font-size: 15px;
  font-weight: 500;
  color: var(--app-text-primary, #0f172a);
}
.actions {
  margin-top: 16px;
}
</style>
