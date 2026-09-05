<template>
  <div class="pricing-page">
    <PageHeader title="模型价格 & 用量" subtitle="按模型维度统计调用与成本 · 价格请在下方手动录入/维护">
      <template #actions>
        <n-radio-group v-model:value="days" @update:value="loadUsageStats">
          <n-radio-button :value="7">近 7 天</n-radio-button>
          <n-radio-button :value="14">近 14 天</n-radio-button>
          <n-radio-button :value="30">近 30 天</n-radio-button>
        </n-radio-group>
        <n-button type="primary" @click="openAdd">新增模型价格</n-button>
      </template>
    </PageHeader>

    <!-- 模型用量柱状统计 -->
    <div class="charts-grid">
      <DataCard title="模型调用次数（Top N）">
        <v-chart v-if="callsOpt" :option="callsOpt" autoresize style="height: 280px" />
        <EmptyState v-else title="暂无数据" description="该时间范围内还没有调用记录" />
      </DataCard>
      <DataCard title="模型累计成本">
        <v-chart v-if="costOpt" :option="costOpt" autoresize style="height: 280px" />
        <EmptyState v-else title="暂无数据" description="需要先录入模型价格，或该时间范围无调用" />
      </DataCard>
    </div>

    <DataCard title="本地价格列表">
      <template #actions>
        <n-tag v-if="list.length" size="small" type="info" round>共 {{ list.length }} 条</n-tag>
      </template>
      <n-data-table
        :columns="columns"
        :data="list"
        :loading="loading"
        :bordered="false"
        :single-line="false"
        size="small"
      />
    </DataCard>

    <!-- 新增/编辑对话框 -->
    <n-modal
      v-model:show="dialog.visible"
      preset="card"
      :title="dialog.id ? '编辑价格' : '新增价格'"
      style="width: 460px; max-width: 92vw"
      :mask-closable="false"
    >
      <n-form :model="form" label-placement="left" label-width="110">
        <n-form-item label="模型名">
          <n-input v-model:value="form.model_name" :disabled="!!dialog.id" placeholder="如 glm-5.2" />
        </n-form-item>
        <n-form-item label="输入价/1M">
          <n-input-number v-model:value="form.input_price" :min="0" :step="0.1" :precision="6" style="width: 100%" />
        </n-form-item>
        <n-form-item label="缓存命中价/1M">
          <n-input-number v-model:value="form.cached_input_price" :min="0" :step="0.1" :precision="6" style="width: 100%" />
        </n-form-item>
        <n-form-item label="输出价/1M">
          <n-input-number v-model:value="form.output_price" :min="0" :step="0.1" :precision="6" style="width: 100%" />
        </n-form-item>
        <n-form-item label="空闲输入价/1M">
          <n-input-number v-model:value="form.off_peak_input_price" :min="0" :step="0.1" :precision="6" style="width: 100%" placeholder="0=沿用标准价" />
        </n-form-item>
        <n-form-item label="空闲输出价/1M">
          <n-input-number v-model:value="form.off_peak_output_price" :min="0" :step="0.1" :precision="6" style="width: 100%" placeholder="0=沿用标准价" />
        </n-form-item>
        <n-form-item label=" ">
          <span style="font-size: 12px; color: var(--n-text-color-3, #999)">空闲时段为北京时间 00:30–08:30，折扣价仅该时段生效；无分时定价的模型保持 0 即可</span>
        </n-form-item>
        <n-form-item label="币种">
          <n-select v-model:value="form.currency" :options="currencyOptions" />
        </n-form-item>
        <n-form-item label="上下文长度">
          <n-input-number v-model:value="form.context_length" :min="0" :step="1024" style="width: 100%" placeholder="0=未设置，如 1048576" />
        </n-form-item>
        <n-form-item label="备注">
          <n-input v-model:value="form.note" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <n-button @click="dialog.visible = false">取消</n-button>
          <n-button type="primary" :loading="dialog.saving" @click="onSave">保存</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { h, reactive, ref, computed, onMounted } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import {
  NButton, NDataTable, NInput, NInputNumber, NSelect, NForm, NFormItem,
  NModal, NPopconfirm, NTag, NRadioGroup, NRadioButton,
  useMessage, type DataTableColumns, type SelectOption,
} from 'naive-ui'
import { api, type Pricing, type UsageResp } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'
import EmptyState from '@/design/components/EmptyState.vue'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

type ByModel = UsageResp['by_model'][number]

const list = ref<Pricing[]>([])
const loading = ref(false)
const message = useMessage()

const days = ref(14)
const usageStats = ref<UsageResp['by_model']>([])

const currencyOptions: SelectOption[] = [
  { label: 'CNY 人民币', value: 'CNY' },
  { label: 'USD 美元', value: 'USD' },
]

const dialog = reactive<{ visible: boolean; id: number | null; saving: boolean }>({
  visible: false,
  id: null,
  saving: false,
})
const form = reactive({ model_name: '', input_price: 0, cached_input_price: 0, output_price: 0, off_peak_input_price: 0, off_peak_output_price: 0, context_length: 0, currency: 'CNY', note: '' })

// 空闲档价格人读格式：两档都为 0（未设置）显示 "—"，否则 "输入 x / 输出 y"
function fmtOffPeak(row: Pricing): string {
  const i = Number(row.off_peak_input_price ?? 0)
  const o = Number(row.off_peak_output_price ?? 0)
  if (!i && !o) return '—'
  return `输入 ${i} / 输出 ${o}`
}

// 上下文长度人读格式：0=未设置，>=1M 显示 "1M"，>=1K 显示 "128K"，否则原样
function fmtContext(n: number): string {
  if (!n) return '未设置'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n % 1_000 ? 1 : 0)}K`
  return String(n)
}

const columns: DataTableColumns<Pricing> = [
  { title: '模型', key: 'model_name', minWidth: 200 },
  {
    title: '输入价 / 1M',
    key: 'input_price',
    width: 150,
    render: (row) => `${row.input_price} ${row.currency}`,
  },
  {
    title: '缓存命中价 / 1M',
    key: 'cached_input_price',
    width: 150,
    render: (row) => `${row.cached_input_price} ${row.currency}`,
  },
  {
    title: '输出价 / 1M',
    key: 'output_price',
    width: 150,
    render: (row) => `${row.output_price} ${row.currency}`,
  },
  {
    title: '空闲档 / 1M',
    key: 'off_peak_prices',
    width: 180,
    render: (row) => fmtOffPeak(row),
  },
  {
    title: '上下文',
    key: 'context_length',
    width: 120,
    render: (row) => fmtContext(row.context_length),
  },
  { title: '备注', key: 'note', minWidth: 220 },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    fixed: 'right',
    render: (row) =>
      h('div', { style: 'display:flex; gap:4px' }, [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => openEdit(row) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => onRemove(row) },
          {
            default: () => '删除该价格？',
            trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'error' }, { default: () => '删除' }),
          },
        ),
      ]),
  },
]

async function load() {
  loading.value = true
  try {
    list.value = await api.pricing.list()
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}

async function loadUsageStats() {
  try {
    const u = await api.stats.usage(days.value)
    usageStats.value = u.by_model
  } catch {
    usageStats.value = []
  }
}

// 调用次数柱状 Top 12
const callsOpt = computed(() => {
  const rows: ByModel[] = [...usageStats.value]
    .filter((r) => (r.calls ?? 0) > 0)
    .sort((a, b) => b.calls - a.calls)
    .slice(0, 12)
  if (!rows.length) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v: number) => `${v.toLocaleString()} 次` },
    grid: { left: 72, right: 16, top: 20, bottom: 48 },
    xAxis: {
      type: 'category',
      data: rows.map((r) => r.model),
      axisLabel: { rotate: 30, interval: 0, fontSize: 11 },
    },
    yAxis: { type: 'value', name: '调用次数' },
    series: [
      {
        type: 'bar',
        barMaxWidth: 36,
        data: rows.map((r) => r.calls),
        itemStyle: { color: '#4f7cff', borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
})

// 累计成本柱状 Top 12
const costOpt = computed(() => {
  const rows: ByModel[] = [...usageStats.value]
    .filter((r) => Number(r.cost ?? 0) > 0)
    .sort((a, b) => Number(b.cost) - Number(a.cost))
    .slice(0, 12)
  if (!rows.length) return null
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (v: number) => `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 })} 元`,
    },
    grid: { left: 72, right: 16, top: 20, bottom: 48 },
    xAxis: {
      type: 'category',
      data: rows.map((r) => r.model),
      axisLabel: { rotate: 30, interval: 0, fontSize: 11 },
    },
    yAxis: { type: 'value', name: '成本（元）' },
    series: [
      {
        type: 'bar',
        barMaxWidth: 36,
        data: rows.map((r) => Number(r.cost ?? 0)),
        itemStyle: { color: '#10b981', borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
})

function openAdd() {
  dialog.id = null
  Object.assign(form, { model_name: '', input_price: 0, cached_input_price: 0, output_price: 0, off_peak_input_price: 0, off_peak_output_price: 0, context_length: 0, currency: 'CNY', note: '' })
  dialog.visible = true
}

function openEdit(row: Pricing) {
  dialog.id = row.id
  Object.assign(form, {
    model_name: row.model_name,
    input_price: row.input_price,
    cached_input_price: row.cached_input_price,
    output_price: row.output_price,
    off_peak_input_price: row.off_peak_input_price,
    off_peak_output_price: row.off_peak_output_price,
    context_length: row.context_length,
    currency: row.currency,
    note: row.note || '',
  })
  dialog.visible = true
}

async function onSave() {
  if (!form.model_name) {
    message.warning('模型名必填')
    return
  }
  dialog.saving = true
  try {
    if (dialog.id) {
      await api.pricing.update(dialog.id, {
        input_price: form.input_price,
        cached_input_price: form.cached_input_price,
        output_price: form.output_price,
        off_peak_input_price: form.off_peak_input_price,
        off_peak_output_price: form.off_peak_output_price,
        context_length: form.context_length,
        currency: form.currency,
        note: form.note,
      })
      message.success('已更新')
    } else {
      await api.pricing.create({ ...form })
      message.success('已创建')
    }
    dialog.visible = false
    await load()
  } catch {
    // 拦截器已提示
  } finally {
    dialog.saving = false
  }
}

async function onRemove(row: Pricing) {
  try {
    await api.pricing.remove(row.id)
    message.success('已删除')
    await load()
  } catch {
    // 拦截器已提示
  }
}

onMounted(() => {
  load()
  loadUsageStats()
})
</script>

<style scoped>
.pricing-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.charts-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 960px) {
  .charts-grid {
    grid-template-columns: 1fr;
  }
}
</style>
