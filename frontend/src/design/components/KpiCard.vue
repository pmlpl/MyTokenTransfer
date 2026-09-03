<template>
  <div class="kpi-card" :class="[`tone-${tone}`]">
    <div class="label">{{ label }}</div>
    <div class="value">
      <span v-if="prefix" class="prefix">{{ prefix }}</span>
      <span class="num">{{ formatted }}</span>
      <span v-if="suffix" class="suffix">{{ suffix }}</span>
    </div>
    <div class="delta" v-if="delta !== undefined" :class="deltaClass">
      <component :is="deltaIcon" v-if="deltaIcon" />
      <span>{{ deltaText }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { tokens } from '../tokens'

const props = withDefaults(
  defineProps<{
    label: string
    value: number | string
    prefix?: string
    suffix?: string
    tone?: 'default' | 'success' | 'warning' | 'danger' | 'info'
    delta?: number
    deltaUnit?: string
  }>(),
  { tone: 'default', deltaUnit: '%' },
)

const formatted = computed(() => {
  if (typeof props.value === 'string') return props.value
  if (Number.isInteger(props.value)) return props.value.toLocaleString()
  return props.value.toFixed(2)
})

const deltaClass = computed(() =>
  props.delta === undefined ? '' : props.delta >= 0 ? 'up' : 'down',
)
const deltaText = computed(() => {
  if (props.delta === undefined) return ''
  const sign = props.delta >= 0 ? '+' : ''
  return `${sign}${props.delta}${props.deltaUnit}`
})
const deltaIcon = computed(() => {
  if (props.delta === undefined) return null
  return props.delta >= 0 ? ArrowUpIcon : ArrowDownIcon
})

// 内联 SVG（不依赖 icon 库，避免 KpiCard 与 vicons 耦合）
const ArrowUpIcon = {
  template: '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M8 4l4 5h-8z" fill="currentColor"/></svg>',
}
const ArrowDownIcon = {
  template: '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M8 12l4-5h-8z" fill="currentColor"/></svg>',
}

const toneColors = {
  default: { num: 'inherit', accent: tokens.color.brand },
  success: { num: tokens.color.success, accent: tokens.color.success },
  warning: { num: tokens.color.warning, accent: tokens.color.warning },
  danger: { num: tokens.color.danger, accent: tokens.color.danger },
  info: { num: tokens.color.info, accent: tokens.color.info },
}
</script>

<style scoped>
.kpi-card {
  background: var(--app-card-bg, #fff);
  border: 1px solid var(--app-border, #e5e7eb);
  border-radius: v-bind('tokens.radius.card');
  padding: 20px 24px;
  box-shadow: v-bind('tokens.shadow.card');
  transition: box-shadow v-bind('tokens.motion.durationNormal') v-bind('tokens.motion.ease');
}
.kpi-card:hover {
  box-shadow: v-bind('tokens.shadow.cardHover');
}
.label {
  font-size: 13px;
  color: var(--app-text-secondary, #64748b);
  margin-bottom: 8px;
}
.value {
  display: flex;
  align-items: baseline;
  gap: 4px;
}
.num {
  font-size: v-bind('tokens.font.kpi');
  font-weight: 600;
  color: v-bind('toneColors[props.tone].num');
  letter-spacing: -0.5px;
  font-variant-numeric: tabular-nums;
}
.prefix,
.suffix {
  font-size: 14px;
  color: var(--app-text-secondary, #64748b);
}
.delta {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}
.delta.up {
  color: v-bind('tokens.color.success');
}
.delta.down {
  color: v-bind('tokens.color.danger');
}
</style>
