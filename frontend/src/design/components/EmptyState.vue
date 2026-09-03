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
    <div v-if="description" class="desc">{{ description }}</div>
    <div v-if="$slots.actions" class="actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
// v2 空状态：图标 + 标题 + 描述 + 可选操作按钮
defineProps<{
  title: string
  description?: string
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
  margin-bottom: 4px;
}
.desc {
  font-size: 13px;
  color: var(--app-text-secondary, #64748b);
}
.actions {
  margin-top: 16px;
}
</style>
