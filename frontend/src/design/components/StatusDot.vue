<template>
  <span class="status-dot" :class="`tone-${tone}`">
    <span class="dot" :class="{ pulse: pulse }" />
    <span class="text"><slot /></span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { tokens } from '../tokens'

const props = withDefaults(
  defineProps<{
    status: 'active' | 'rate_limited' | 'exhausted' | 'invalid' | 'disabled' | string
    pulse?: boolean
  }>(),
  { pulse: false },
)

const toneMap = {
  active: 'success',
  rate_limited: 'warning',
  exhausted: 'danger',
  invalid: 'danger',
  disabled: 'info',
} as const

const tone = computed(
  () => toneMap[props.status as keyof typeof toneMap] || 'info',
)

// 暴露给 CSS v-bind 的颜色
const colors = {
  success: tokens.color.success,
  warning: tokens.color.warning,
  danger: tokens.color.danger,
  info: '#94a3b8',
}
</script>

<style scoped>
.status-dot {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: v-bind('colors[tone]');
  flex-shrink: 0;
}
.dot.pulse {
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.4); }
}
.text {
  font-size: 13px;
  color: var(--app-text-primary, #0f172a);
}
</style>
