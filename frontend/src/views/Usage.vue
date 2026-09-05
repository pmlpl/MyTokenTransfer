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

    <!-- KPI 卡片（带进度条） -->
    <div class="kpi-grid">
      <div class="kpi-wrap">
        <KpiCard label="总调用次数" :value="totals.calls" />
        <div class="kpi-progress">
          <div class="kpi-progress-bar" :style="{ width: kpiProgress.calls + '%', background: '#4f7cff' }" />
        </div>
        <div class="kpi-progress-label">{{ kpiProgress.calls }}% · 峰值时段占比</div>
      </div>
      <div class="kpi-wrap">
        <KpiCard label="总 Tokens" :value="totals.total_tokens" />
        <div class="kpi-progress">
          <div class="kpi-progress-bar" :style="{ width: kpiProgress.tokens + '%', background: '#6366f1' }" />
        </div>
        <div class="kpi-progress-label">{{ kpiProgress.tokens }}% · 峰值时段占比</div>
      </div>
      <div class="kpi-wrap">
        <KpiCard label="缓存命中率" :value="totals.cache_hit_rate != null ? totals.cache_hit_rate + '%' : '—'" />
        <div class="kpi-progress">
          <div class="kpi-progress-bar" :style="{ width: (totals.cache_hit_rate ?? 0) + '%', background: hitColor(totals.cache_hit_rate ?? 0) }" />
        </div>
        <div class="kpi-progress-label" :style="{ color: hitColor(totals.cache_hit_rate ?? 0) }">{{ totals.cache_hit_rate ?? 0 }}% · 命中效率</div>
      </div>
      <div class="kpi-wrap">
        <KpiCard label="总成本" :value="fmtCost(totals.cost)" :suffix="currency" />
        <div class="kpi-progress">
          <div class="kpi-progress-bar" :style="{ width: kpiProgress.cost + '%', background: '#10b981' }" />
        </div>
        <div class="kpi-progress-label">{{ kpiProgress.cost }}% · 峰值时段占比</div>
      </div>
    </div>

    <!-- 趋势分析：维度切换 -->
    <DataCard title="趋势分析">
      <template #actions>
        <n-tabs v-model:value="metric" type="segment" size="small">
          <n-tab-pane name="tokens" tab="Tokens" />
          <n-tab-pane name="calls" tab="请求数" />
          <n-tab-pane name="cost" tab="成本" />
          <n-tab-pane name="cache" tab="缓存命中" />
          <n-tab-pane name="model" tab="按模型" />
        </n-tabs>
      </template>
      <v-chart v-if="trendOpt" :option="trendOpt" autoresize style="height: 360px" />
      <EmptyState v-else title="无数据" description="该时间范围内无调用记录" />
    </DataCard>

    <!-- 用量明细：按密钥 / 按模型 Tab 切换 -->
    <DataCard title="用量明细">
      <template #actions>
        <n-tabs v-model:value="detailTab" type="segment" size="small" class="detail-tabs">
          <n-tab-pane name="key" tab="按密钥" />
          <n-tab-pane name="model" tab="按模型" />
        </n-tabs>
      </template>

      <!-- 按密钥 -->
      <template v-if="detailTab === 'key'">
        <n-data-table
          v-if="byKey.length"
          :columns="keyColumns"
          :data="byKey"
          :bordered="false"
          size="small"
          :row-key="(row: any) => row.key_id ?? row.key_name"
        />
        <EmptyState v-else title="无数据" description="该时间范围内无调用记录" />
      </template>

      <!-- 按模型 -->
      <template v-else>
        <n-data-table
          v-if="byModel.length"
          :columns="modelColumns"
          :data="byModel"
          :bordered="false"
          size="small"
          :row-key="(row: any) => row.model"
        />
        <EmptyState v-else title="无数据" description="该时间范围内无调用记录" />
      </template>
    </DataCard>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, h } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { NButton, NRadioGroup, NRadioButton, NTabs, NTabPane, NDataTable, type DataTableColumns } from 'naive-ui'
import { api, type UsageResp } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'
import KpiCard from '@/design/components/KpiCard.vue'
import EmptyState from '@/design/components/EmptyState.vue'

use([CanvasRenderer, BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent])

const data = ref<UsageResp | null>(null)
const days = ref(1)
const currency = ref('CNY')
const metric = ref<'tokens' | 'calls' | 'cost' | 'cache' | 'model'>('tokens')

const totals = computed(() => data.value?.totals ?? { calls: 0, total_tokens: 0, cached_tokens: 0, cache_hit_rate: 0, cost: 0 })

const byKey = computed(() => data.value?.by_key ?? [])
const byModel = computed(() => data.value?.by_model ?? [])
const detailTab = ref<'key' | 'model'>('key')

// 模型颜色池（用于按模型波形图，每个模型不同颜色）
const MODEL_COLORS = ['#4f7cff', '#10b981', '#f59e0b', '#ef4444', '#6366f1', '#ec4899', '#14b8a6', '#f97316', '#8b5cf6', '#06b6d4', '#84cc16', '#f43f5e']

function modelColor(model: string, index: number): string {
  return MODEL_COLORS[index % MODEL_COLORS.length]
}

// KPI 进度条：当前值占理论最大值（峰值时段 × 总时段数）的比例
const kpiProgress = computed(() => {
  const isToday = days.value === 1
  const rows = isToday ? (data.value?.hourly ?? []) : (data.value?.daily ?? [])
  if (!rows.length) return { calls: 0, tokens: 0, cost: 0 }

  const totalSlots = isToday ? 24 : days.value
  const peakCalls = Math.max(...rows.map((r: any) => r.calls), 1)
  const peakTokens = Math.max(...rows.map((r: any) => r.total_tokens), 1)
  const peakCost = Math.max(...rows.map((r: any) => r.cost), 0.0001)

  const calls = Math.min(100, Math.round((totals.value.calls / (peakCalls * totalSlots)) * 100))
  const tokens = Math.min(100, Math.round((totals.value.total_tokens / (peakTokens * totalSlots)) * 100))
  const cost = Math.min(100, Math.round((totals.value.cost / (peakCost * totalSlots)) * 100))
  return { calls, tokens, cost }
})

// 按密钥表格列
const keyColumns: DataTableColumns<any> = [
  { title: '密钥', key: 'key_name', minWidth: 160, render: (row: any) =>
    h('span', { class: 'cell-key-name' }, row.key_name)
  },
  { title: '调用次数', key: 'calls', width: 110, align: 'right', render: (row: any) =>
    h('span', { class: 'cell-num' }, Number(row.calls).toLocaleString())
  },
  { title: '输入 Tokens', key: 'prompt_tokens', width: 120, align: 'right', render: (row: any) =>
    h('span', { class: 'cell-num' }, fmtAxis(row.prompt_tokens))
  },
  { title: '命中 Tokens', key: 'cached_tokens', width: 120, align: 'right', render: (row: any) =>
    h('span', { class: 'cell-num' }, fmtAxis(row.cached_tokens))
  },
  { title: '缓存命中率', key: 'cache_hit_rate', width: 180, render: (row: any) =>
    h('div', { class: 'hit-cell' }, [
      h('div', { class: 'hit-bar' }, [
        h('div', { class: 'hit-bar-fill', style: { width: row.cache_hit_rate + '%', background: hitColor(row.cache_hit_rate) } }),
      ]),
      h('span', { class: 'hit-val', style: { color: hitColor(row.cache_hit_rate) } }, row.cache_hit_rate + '%'),
    ])
  },
  { title: '成本', key: 'cost', width: 110, align: 'right', render: (row: any) =>
    h('span', { class: 'cell-num cell-cost' }, fmtCost(row.cost))
  },
]

// 按模型表格列
const modelColumns: DataTableColumns<any> = [
  { title: '模型', key: 'model', minWidth: 200, render: (row: any) =>
    h('span', { class: 'cell-model-name' }, row.model)
  },
  { title: '调用次数', key: 'calls', width: 130, align: 'right', render: (row: any) =>
    h('span', { class: 'cell-num' }, Number(row.calls).toLocaleString())
  },
  { title: '总 Tokens', key: 'total_tokens', width: 140, align: 'right', render: (row: any) =>
    h('span', { class: 'cell-num' }, fmtAxis(row.total_tokens))
  },
  { title: '成本', key: 'cost', width: 130, align: 'right', render: (row: any) =>
    h('span', { class: 'cell-num cell-cost' }, fmtCost(row.cost))
  },
]

// 命中率配色：>=85 绿 / >=60 橙 / 其余红
function hitColor(rate: number) {
  if (rate >= 85) return '#10b981'
  if (rate >= 60) return '#f59e0b'
  return '#ef4444'
}

// 通用 tooltip 格式化
function lineTooltip(params: any[], unit: string, fmt?: (v: number) => string) {
  const label = params[0]?.axisValue ?? ''
  let html = `${label}<br/>`
  for (const p of params) {
    const v = fmt ? fmt(Number(p.value)) : Number(p.value).toLocaleString()
    html += `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${p.color};margin-right:6px"></span>${p.seriesName}: ${v} ${unit}<br/>`
  }
  return html
}

// 趋势图：今日用折线波形图，多日用柱状图
const trendOpt = computed(() => {
  const isToday = days.value === 1

  if (isToday) {
    return buildTodayWave()
  }
  return buildMultiDayBar()
})

// 今日波形图（折线 + 平滑 + 面积填充）
function buildTodayWave(): any | null {
  const allHourly = data.value?.hourly ?? []
  if (!allHourly.length) return null

  // 只保留当前时间及之前的小时，未来小时不显示
  const now = new Date()
  const currentHour = now.getHours()
  const hourly = allHourly.filter((r) => r.hour <= currentHour)
  if (!hourly.length) return null

  // hourKeys 用于数据索引（HH:00 格式），hours 用于 X 轴显示
  const hourKeys = hourly.map((r) => `${String(r.hour).padStart(2, '0')}:00`)
  const hours = [...hourKeys]
  // 最后一个坐标显示当前实时时间
  const nowLabel = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
  hours[hours.length - 1] = nowLabel

  const baseGrid = { left: 56, right: 24, top: 40, bottom: 32 }
  // 横轴标签：数据点少则全显，多则间隔显示并旋转
  const labelInterval = hourly.length > 12 ? 1 : 0
  const baseXAxis = {
    type: 'category', boundaryGap: false, data: hours,
    axisLabel: { interval: labelInterval, rotate: hourly.length > 10 ? 30 : 0, fontSize: 11 },
  }

  if (metric.value === 'calls') {
    return {
      tooltip: { trigger: 'axis', formatter: (p: any[]) => lineTooltip(p, '次') },
      grid: baseGrid,
      xAxis: baseXAxis,
      yAxis: { type: 'value', name: '调用次数' },
      series: [{
        name: '调用次数', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
        data: hourly.map((r) => r.calls),
        lineStyle: { width: 2.5, color: '#4f7cff' },
        itemStyle: { color: '#4f7cff' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(79,124,255,0.35)' }, { offset: 1, color: 'rgba(79,124,255,0.02)' }] } },
      }],
    }
  }

  if (metric.value === 'cost') {
    return {
      tooltip: { trigger: 'axis', formatter: (p: any[]) => lineTooltip(p, '元', (v) => v.toFixed(4)) },
      grid: baseGrid,
      xAxis: baseXAxis,
      yAxis: { type: 'value', name: '成本（元）' },
      series: [{
        name: '成本', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
        data: hourly.map((r) => Number(r.cost)),
        lineStyle: { width: 2.5, color: '#10b981' },
        itemStyle: { color: '#10b981' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(16,185,129,0.35)' }, { offset: 1, color: 'rgba(16,185,129,0.02)' }] } },
      }],
    }
  }

  if (metric.value === 'cache') {
    const cached = hourly.map((r) => Number(r.cached_tokens ?? 0))
    const missed = hourly.map((r) => Math.max(0, Number(r.prompt_tokens ?? 0) - Number(r.cached_tokens ?? 0)))
    const hasData = cached.some((v) => v > 0) || missed.some((v) => v > 0)
    if (!hasData) return null
    return {
      tooltip: { trigger: 'axis', formatter: (p: any[]) => lineTooltip(p, 'tokens') },
      legend: { data: ['缓存命中', '未命中'], top: 0 },
      grid: { left: 56, right: 24, top: 40, bottom: 32 },
      xAxis: baseXAxis,
      yAxis: { type: 'value', name: 'Prompt Tokens', axisLabel: { formatter: (v: number) => fmtAxis(v) } },
      series: [
        {
          name: '缓存命中', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
          data: cached, lineStyle: { width: 2.5, color: '#f59e0b' }, itemStyle: { color: '#f59e0b' },
          areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(245,158,11,0.3)' }, { offset: 1, color: 'rgba(245,158,11,0.02)' }] } },
        },
        {
          name: '未命中', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
          data: missed, lineStyle: { width: 2.5, color: '#94a3b8' }, itemStyle: { color: '#94a3b8' },
          areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(148,163,184,0.25)' }, { offset: 1, color: 'rgba(148,163,184,0.02)' }] } },
        },
      ],
    }
  }

  if (metric.value === 'model') {
    const rows = data.value?.hourly_by_model ?? []
    if (!rows.length) return null
    const modelSet = new Set<string>()
    for (const r of rows) modelSet.add(r.model)
    const models = Array.from(modelSet)
    const matrix: Record<string, Record<string, number>> = {}
    for (const hr of hourKeys) matrix[hr] = {}
    for (const r of rows) {
      const hrLabel = `${String(r.hour).padStart(2, '0')}:00`
      if (hourKeys.includes(hrLabel)) {
        matrix[hrLabel]![r.model] = Number(r.total_tokens ?? 0)
      }
    }
    return {
      tooltip: { trigger: 'axis', formatter: (p: any[]) => lineTooltip(p, 'tokens') },
      legend: { data: models, top: 0, type: 'scroll' },
      grid: { left: 56, right: 24, top: 40, bottom: 32 },
      xAxis: baseXAxis,
      yAxis: { type: 'value', name: '总 Tokens', axisLabel: { formatter: (v: number) => fmtAxis(v) } },
      series: models.map((m, idx) => ({
        name: m, type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
        emphasis: { focus: 'series' },
        data: hourKeys.map((hr) => matrix[hr]?.[m] ?? 0),
        lineStyle: { width: 2.5, color: modelColor(m, idx) },
        itemStyle: { color: modelColor(m, idx) },
      })),
    }
  }

  // 默认 tokens 维度：输入（命中）+ 输入（未命中）+ 输出 三条波形
  const hit = hourly.map((r) => Number(r.cached_tokens ?? 0))
  const miss = hourly.map((r) => Math.max(0, Number(r.prompt_tokens ?? 0) - Number(r.cached_tokens ?? 0)))
  const out = hourly.map((r) => r.completion_tokens)
  return {
    tooltip: { trigger: 'axis', formatter: (p: any[]) => lineTooltip(p, 'tokens') },
    legend: { data: ['输入（命中）', '输入（未命中）', '输出'], top: 0 },
    grid: { left: 56, right: 24, top: 40, bottom: 32 },
    xAxis: baseXAxis,
    yAxis: { type: 'value', name: '总 Tokens', axisLabel: { formatter: (v: number) => fmtAxis(v) } },
    series: [
      {
        name: '输入（命中）', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
        data: hit, lineStyle: { width: 2.5, color: '#f59e0b' }, itemStyle: { color: '#f59e0b' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(245,158,11,0.3)' }, { offset: 1, color: 'rgba(245,158,11,0.02)' }] } },
      },
      {
        name: '输入（未命中）', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
        data: miss, lineStyle: { width: 2.5, color: '#94a3b8' }, itemStyle: { color: '#94a3b8' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(148,163,184,0.25)' }, { offset: 1, color: 'rgba(148,163,184,0.02)' }] } },
      },
      {
        name: '输出', type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
        data: out, lineStyle: { width: 2.5, color: '#6366f1' }, itemStyle: { color: '#6366f1' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(99,102,241,0.3)' }, { offset: 1, color: 'rgba(99,102,241,0.02)' }] } },
      },
    ],
  }
}

// 多日柱状图（保持原有逻辑）
function buildMultiDayBar(): any | null {
  const rows = data.value?.daily ?? []
  if (!rows.length) return null
  const dates = rows.map((r) => r.date)

  if (metric.value === 'calls') {
    return {
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
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
    return {
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
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
    const hasData = rows.some((r) => Number(r.cached_tokens ?? 0) > 0 || Number(r.prompt_tokens ?? 0) > 0)
    if (!hasData) return null
    const cached = rows.map((r) => Number(r.cached_tokens ?? 0))
    const missed = rows.map((r) => Math.max(0, Number(r.prompt_tokens ?? 0) - Number(r.cached_tokens ?? 0)))
    return {
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
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

  if (metric.value === 'model') {
    const rows = data.value?.daily_by_model ?? []
    if (!rows.length) return null
    const dateSet = new Set<string>()
    const modelSet = new Set<string>()
    for (const r of rows) { dateSet.add(r.date); modelSet.add(r.model) }
    const datesArr = Array.from(dateSet).sort()
    const models = Array.from(modelSet)
    const matrix: Record<string, Record<string, number>> = {}
    for (const d of datesArr) matrix[d] = {}
    for (const r of rows) matrix[r.date]![r.model] = Number(r.total_tokens ?? 0)
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, valueFormatter: (v: number) => `${Number(v).toLocaleString()} tokens` },
      legend: { data: models, top: 0 },
      grid: { left: 56, right: 24, top: 40, bottom: 32 },
      xAxis: { type: 'category', boundaryGap: true, data: datesArr },
      yAxis: { type: 'value', name: '总 Tokens', axisLabel: { formatter: (v: number) => fmtAxis(v) } },
      series: models.map((m, idx) => ({
        name: m, type: 'bar', stack: 'tokens', barMaxWidth: 36, emphasis: { focus: 'series' },
        itemStyle: { color: modelColor(m, idx) },
        data: datesArr.map((d) => matrix[d]?.[m] ?? 0),
      })),
    }
  }

  // 默认 tokens 堆叠柱状
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
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
}

function fmtCost(v: number) {
  const n = Number(v ?? 0)
  if (n === 0) return '0'
  if (n < 0.01) return n.toFixed(6).replace(/\.?0+$/, '')
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
.kpi-wrap {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.kpi-wrap :deep(.kpi-card) {
  border-radius: 12px 12px 0 0;
  border-bottom: none;
}
.kpi-progress {
  background: var(--app-card-bg, #fff);
  border: 1px solid var(--app-border, #e5e7eb);
  border-top: none;
  border-radius: 0 0 12px 12px;
  padding: 0 24px 10px;
}
.kpi-progress-bar {
  height: 5px;
  border-radius: 3px;
  transition: width 0.4s ease;
}
.kpi-progress-label {
  font-size: 11px;
  color: var(--app-text-tertiary, #94a3b8);
  margin-top: 5px;
  font-variant-numeric: tabular-nums;
}
.detail-tabs {
  margin-left: auto;
}
.cell-key-name {
  font-weight: 500;
  color: var(--app-text-primary, #0f172a);
  max-width: 180px;
  display: inline-block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cell-model-name {
  font-weight: 500;
  color: var(--app-text-primary, #0f172a);
  font-family: monospace;
  font-size: 12.5px;
}
.cell-num {
  font-variant-numeric: tabular-nums;
  color: var(--app-text-secondary, #64748b);
}
.cell-cost {
  color: var(--app-text-primary, #0f172a);
  font-weight: 500;
}
.hit-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 150px;
}
.hit-bar {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: rgba(128, 128, 128, 0.15);
  overflow: hidden;
}
.hit-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}
.hit-val {
  font-variant-numeric: tabular-nums;
  min-width: 48px;
  text-align: right;
  font-size: 12.5px;
  font-weight: 500;
}
</style>
