<template>
  <div>
    <div class="toolbar">
      <span>统计区间：</span>
      <el-radio-group v-model="days" @change="load">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="14">近 14 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
      </el-radio-group>
      <el-button @click="load" style="margin-left: 12px">刷新</el-button>
      <div style="flex: 1" />
      <el-button type="success" @click="$router.push('/pricing')">管理模型价格</el-button>
    </div>

    <el-row :gutter="16">
      <el-col :span="8"><el-card shadow="hover"><div class="k">总调用次数</div><div class="num">{{ totals.calls }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="k">总 Tokens</div><div class="num">{{ totals.total_tokens }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="k">总成本 ({{ currency }})</div><div class="num">{{ fmtCost(totals.cost) }}</div></el-card></el-col>
    </el-row>

    <el-card style="margin-top: 16px">
      <template #header>每日调用与成本</template>
      <v-chart v-if="dailyOpt" :option="dailyOpt" autoresize style="height: 320px" />
      <el-empty v-else description="无数据" />
    </el-card>

    <el-card style="margin-top: 16px">
      <template #header>按模型分布</template>
      <v-chart v-if="modelOpt" :option="modelOpt" autoresize style="height: 320px" />
      <el-empty v-else description="无数据" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { api, type UsageResp } from '@/api'

use([CanvasRenderer, LineChart, PieChart, GridComponent, TooltipComponent, LegendComponent])

const data = ref<UsageResp | null>(null)
const days = ref(14)
const currency = ref('CNY')

const totals = computed(() => data.value?.totals ?? { calls: 0, total_tokens: 0, cost: 0 })

const dailyOpt = computed(() => {
  const d = data.value?.daily ?? []
  if (!d.length) return null
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['调用次数', '成本'] },
    grid: { left: 48, right: 48, top: 40, bottom: 32 },
    xAxis: { type: 'category', data: d.map((x) => x.date) },
    yAxis: [
      { type: 'value', name: '次数' },
      { type: 'value', name: '成本', axisLabel: { formatter: (v: number) => fmtCost(v) } },
    ],
    series: [
      { name: '调用次数', type: 'line', smooth: true, data: d.map((x) => x.calls) },
      { name: '成本', type: 'line', smooth: true, yAxisIndex: 1, data: d.map((x) => Number(x.cost.toFixed(6))) },
    ],
  }
})

const modelOpt = computed(() => {
  const m = data.value?.by_model ?? []
  if (!m.length) return null
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} 次 ({d}%)' },
    legend: { bottom: 0 },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        data: m.map((x) => ({ name: x.model, value: x.calls })),
      },
    ],
  }
})

function fmtCost(v: number) {
  if (v === 0) return '0'
  if (v < 0.01) return v.toExponential(2)
  return v.toFixed(4)
}

async function load() {
  data.value = await api.stats.usage(days.value)
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; align-items: center; margin-bottom: 16px; }
.k { color: #909399; font-size: 13px; }
.num { font-size: 30px; font-weight: 600; margin-top: 6px; }
</style>
