<template>
  <div class="usage-page">
    <PageHeader title="用量统计" subtitle="调用次数 · Tokens · 成本 · 缓存命中">
      <template #actions>
        <n-radio-group v-model:value="days" @update:value="load">
          <n-radio-button :value="1">今日</n-radio-button>
          <n-radio-button :value="7">近 7 天</n-radio-button>
          <n-radio-button :value="14">近 14 天</n-radio-button>
          <n-radio-button :value="30">近 30 天</n-radio-button>
        </n-radio-group>
        <n-button @click="load">刷新</n-button>
        <n-button type="primary" @click="$router.push('/pricing')">管理模型价格</n-button>
      </template>
    </PageHeader>

    <!-- KPI 卡片 -->
    <div class="kpi-grid">
      <KpiCard label="总调用次数" :value="totals.calls" />
      <KpiCard label="总 Tokens" :value="totals.total_tokens" />
      <KpiCard label="缓存命中率" :value="totals.cache_hit_rate != null ? totals.cache_hit_rate + '%' : '—'" />
      <KpiCard label="总成本" :value="fmtCost(totals.cost)" :suffix="currency" />
    </div>

    <!-- 主趋势图：维度切换 -->
    <DataCard>
      <template #title>
        <div class="trend-header">
          <n-tabs v-model:value="metric" type="segment" size="small">
            <n-tab-pane name="tokens" tab="Tokens" />
            <n-tab-pane name="calls" tab="请求数" />
            <n-tab-pane name="cost" tab="成本" />
            <n-tab-pane name="cache" tab="缓存命中" />
          </n-tabs>
        </div>
      </template>
      <v-chart v-if="trendOpt" :option="trendOpt" autoresize style="height: 360px" />
      <EmptyState v-else title="无数据" description="该时间范围内无调用记录" />
    </DataCard>

    <!-- 按模型堆叠柱状图 -->
    <DataCard title="按模型堆叠柱状图（每日 Tokens）">
      <v-chart v-if="dailyOpt" :option="dailyOpt" autoresize style="height: 320px" />
      <EmptyState v-else title="无数据" description="近段时间无调用记录" />
    </DataCard>

    <!-- 各密钥缓存命中率 -->
    <DataCard title="各密钥缓存命中率" description="缓存按上游 key 隔离；命中率偏低说明该 key 存在冷启动或 key 抖动">
      <div v-if="byKey.length" class="key-table-wrap">
        <table class="key-table">
          <thead>
            <tr>
              <th>密钥</th>
              <th>调用次数</th>
              <th>输入 Tokens</th>
              <th>命中 Tokens</th>
              <th>命中率</th>
              <th>成本</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="k in byKey" :key="k.key_id ?? k.key_name">
              <td class="key-name">{{ k.key_name }}</td>
              <td>{{ Number(k.calls).toLocaleString() }}</td>
              <td>{{ fmtAxis(k.prompt_tokens) }}</td>
              <td>{{ fmtAxis(k.cached_tokens) }}</td>
              <td>
                <div class="hit-cell">
                  <div class="hit-bar">
                    <div class="hit-bar-fill" :style="{ width: k.cache_hit_rate + '%', background: hitColor(k.cache_hit_rate) }"></div>
                  </div>
                  <span class="hit-val" :style="{ color: hitColor(k.cache_hit_rate) }">{{ k.cache_hit_rate }}%</span>
                </div>
              </td>
              <td>{{ fmtCost(k.cost) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else title="无数据" description="该时间范围内无调用记录" />
    </DataCard>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { NButton, NRadioGroup, NRadioButton, NTabs, NTabPane } from 'naive-ui'
import { api, type UsageResp } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'
import KpiCard from '@/design/components/KpiCard.vue'
import EmptyState from '@/design/components/EmptyState.vue'

use([CanvasRenderer, BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent])

const data = ref<UsageResp | null>(null)
const days = ref(14)
const currency = ref('CNY')
const metric = ref<'tokens' | 'calls' | 'cost' | 'cache'>('tokens')

const totals = computed(() => data.value?.totals ?? { calls: 0, total_tokens: 0, cached_tokens: 0, cache_hit_rate: 0, cost: 0 })

const byKey = computed(() => data.value?.by_key ?? [])

// 命中率配色：>=85 绿 / >=60 橙 / 其余红
function hitColor(rate: number) {
  if (rate >= 85) return '#10b981'
  if (rate >= 60) return '#f59e0b'
  return '#ef4444'
}

// 主趋势图：根据 metric 切换不同维度
const trendOpt = computed(() => {
  const rows = data.value?.daily ?? []
  if (!rows.length) return null
  const dates = rows.map((r) => r.date)

  if (metric.value === 'calls') {
    // 每日请求数
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: any[]) => {
          const date = params[0]?.axisValue ?? ''
          const v = Number(params[0]?.value ?? 0).toLocaleString()
          return `${date}<br/><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#4f7cff;margin-right:6px"></span>调用次数: ${v} 次`
        },
      },
      grid: { left: 56, right: 24, top: 16, bottom: 32 },
      xAxis: { type: 'category', boundaryGap: true, data: dates },
      yAxis: { type: 'value', name: '调用次数' },
      series: [{ type: 'bar', barMaxWidth: 36, data: rows.map((r) => r.calls), itemStyle: { color: '#4f7cff', borderRadius: [4, 4, 0, 0] } }],
    }
  }

  if (metric.value === 'cost') {
    // 每日成本
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: any[]) => {
          const date = params[0]?.axisValue ?? ''
          const v = Number(params[0]?.value ?? 0)
          return `${date}<br/><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#10b981;margin-right:6px"></span>成本: ${v.toFixed(4)} 元`
        },
      },
      grid: { left: 56, right: 24, top: 16, bottom: 32 },
      xAxis: { type: 'category', boundaryGap: true, data: dates },
      yAxis: { type: 'value', name: '成本（元）' },
      series: [{ type: 'bar', barMaxWidth: 36, data: rows.map((r) => Number(r.cost)), itemStyle: { color: '#10b981', borderRadius: [4, 4, 0, 0] } }],
    }
  }

  if (metric.value === 'cache') {
    // 缓存命中 vs 未命中 堆叠柱状
    const hasData = rows.some((r) => Number(r.cached_tokens ?? 0) > 0 || Number(r.prompt_tokens ?? 0) > 0)
    if (!hasData) return null
    const cached = rows.map((r) => Number(r.cached_tokens ?? 0))
    const missed = rows.map((r) => Math.max(0, Number(r.prompt_tokens ?? 0) - Number(r.cached_tokens ?? 0)))
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: any[]) => {
          const date = params[0]?.axisValue ?? ''
          let html = `${date}<br/>`
          for (const p of params) {
            const v = Number(p.value).toLocaleString()
            html += `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${p.color};margin-right:6px"></span>${p.seriesName}: ${v} tokens<br/>`
          }
          return html
        },
      },
      legend: { data: ['缓存命中', '未命中'], top: 0 },
      grid: { left: 56, right: 24, top: 36, bottom: 32 },
      xAxis: { type: 'category', boundaryGap: true, data: dates },
      yAxis: { type: 'value', name: 'Prompt Tokens', axisLabel: { formatter: (v: number) => fmtAxis(v) } },
      series: [
        { name: '缓存命中', type: 'bar', stack: 'prompt', barMaxWidth: 36, data: cached, itemStyle: { color: '#f59e0b' } },
        { name: '未命中', type: 'bar', stack: 'prompt', barMaxWidth: 36, data: missed, itemStyle: { color: '#d1d5db' } },
      ],
    }
  }

  // 默认：tokens 维度 — 输入（命中）+ 输入（未命中）+ 输出 三层堆叠
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params: any[]) => {
        const date = params[0]?.axisValue ?? ''
        let html = `${date}<br/>`
        for (const p of params) {
          const v = Number(p.value).toLocaleString()
          const color = p.color
          html += `${color ? `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${color};margin-right:6px"></span>` : ''}${p.seriesName}: ${v} tokens<br/>`
        }
        return html
      },
    },
    legend: { data: ['输入（命中）', '输入（未命中）', '输出'], top: 0 },
    grid: { left: 56, right: 24, top: 36, bottom: 32 },
    xAxis: { type: 'category', boundaryGap: true, data: dates },
    yAxis: { type: 'value', name: '总 Tokens', axisLabel: { formatter: (v: number) => fmtAxis(v) } },
    series: [
      { name: '输入（命中）', type: 'bar', stack: 'tokens', barMaxWidth: 36, data: rows.map((r) => Number(r.cached_tokens ?? 0)), itemStyle: { color: '#f59e0b' } },
      { name: '输入（未命中）', type: 'bar', stack: 'tokens', barMaxWidth: 36, data: rows.map((r) => Math.max(0, r.prompt_tokens - Number(r.cached_tokens ?? 0))), itemStyle: { color: '#d1d5db' } },
      { name: '输出', type: 'bar', stack: 'tokens', barMaxWidth: 36, data: rows.map((r) => r.completion_tokens), itemStyle: { color: '#6366f1' } },
    ],
  }
})

// 按模型堆叠柱状图
const dailyOpt = computed(() => {
  const rows = data.value?.daily_by_model ?? []
  if (!rows.length) return null
  const dateSet = new Set<string>()
  const modelSet = new Set<string>()
  for (const r of rows) {
    dateSet.add(r.date)
    modelSet.add(r.model)
  }
  const dates = Array.from(dateSet).sort()
  const models = Array.from(modelSet)
  const matrix: Record<string, Record<string, number>> = {}
  for (const d of dates) matrix[d] = {}
  for (const r of rows) matrix[r.date]![r.model] = Number(r.total_tokens ?? 0)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      valueFormatter: (v: number) => `${Number(v).toLocaleString()} tokens`,
    },
    legend: { data: models },
    grid: { left: 56, right: 24, top: 40, bottom: 32 },
    xAxis: { type: 'category', boundaryGap: true, data: dates },
    yAxis: { type: 'value', name: '总 Tokens', axisLabel: { formatter: (v: number) => fmtAxis(v) } },
    series: models.map((m) => ({
      name: m,
      type: 'bar',
      stack: 'tokens',
      barMaxWidth: 36,
      emphasis: { focus: 'series' },
      data: dates.map((d) => matrix[d]?.[m] ?? 0),
    })),
  }
})

function fmtCost(v: number) {
  const n = Number(v ?? 0)
  if (n === 0) return '0'
  if (n < 0.01) return n.toExponential(2)
  return n.toFixed(4)
}

// Y 轴大数字格式化：0 / 50K / 1.2M / 3.5B
function fmtAxis(v: number): string {
  if (v >= 1_000_000_000) return (v / 1_000_000_000).toFixed(1) + 'B'
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(0) + 'M'
  if (v >= 1_000) return (v / 1_000).toFixed(0) + 'K'
  return String(v)
}

async function load() {
  try {
    data.value = await api.stats.usage(days.value)
  } catch {
    // 拦截器已提示
  }
}

onMounted(load)
</script>

<style scoped>
.usage-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
@media (max-width: 1024px) {
  .kpi-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
.trend-header {
  display: flex;
  align-items: center;
}
.key-table-wrap {
  overflow-x: auto;
}
.key-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.key-table th,
.key-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid rgba(128, 128, 128, 0.15);
  white-space: nowrap;
}
.key-table th {
  color: var(--mtt-text-secondary, #888);
  font-weight: 500;
}
.key-name {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.hit-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 160px;
}
.hit-bar {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: rgba(128, 128, 128, 0.2);
  overflow: hidden;
}
.hit-bar-fill {
  height: 100%;
  border-radius: 3px;
}
.hit-val {
  font-variant-numeric: tabular-nums;
  min-width: 52px;
  text-align: right;
}
</style>
