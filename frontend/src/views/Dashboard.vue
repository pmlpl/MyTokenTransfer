<template>
  <div class="dashboard">
    <PageHeader title="仪表盘" subtitle="密钥与转发整体概览" />

    <!-- KPI 条：4 张卡 -->
    <div class="kpi-grid">
      <KpiCard label="供应商" :value="stats.providers" />
      <KpiCard label="密钥总数" :value="stats.keys" />
      <KpiCard label="可用" :value="stats.active" tone="success" />
      <KpiCard label="异常 / 受限" :value="abnormal" tone="danger" />
    </div>

    <!-- 状态分布表 -->
    <DataCard title="密钥状态分布" class="status-card">
      <n-data-table
        :columns="columns"
        :data="statusRows"
        ::bordered="false"
        :single-line="false"
        size="small"
      />
      <EmptyState v-if="!statusRows.length" title="暂无密钥" description="去密钥管理添加第一条 API 密钥" />
    </DataCard>

    <!-- 密钥×模型 细粒度状态 -->
    <DataCard title="密钥×模型 细粒度状态" class="kms-card">
      <template #actions>
        <div class="kms-actions">
          <n-select
            v-model:value="selectedChannelId"
            :options="channelOptions"
            placeholder="选择渠道按模型探测"
            clearable
            size="small"
            style="width: 200px"
          />
          <n-button
            size="small"
            type="primary"
            :loading="channelTesting"
            :disabled="!selectedChannelId"
            @click="onTestChannel"
          >
            按渠道测试
          </n-button>
          <n-button size="small" quaternary :loading="kmsLoading" @click="loadKms">刷新</n-button>
        </div>
      </template>

      <!-- 测试结果摘要 -->
      <div v-if="channelTestResult" class="test-result-bar">
        <n-tag size="small" :type="channelTestResult.failed === 0 ? 'success' : 'warning'" :bordered="false">
          渠道「{{ channelTestResult.channel_name }}」探测完成：{{ channelTestResult.ok }}/{{ channelTestResult.total }} 可用
        </n-tag>
      </div>

      <n-data-table
        :columns="kmsColumns"
        :data="displayKmsItems"
        :loading="kmsLoading"
        ::bordered="false"
        :single-line="false"
        size="small"
      />
      <EmptyState
        v-if="!kmsLoading && !displayKmsItems.length"
        title="暂无模型级状态记录"
        description="选择渠道点击「按渠道测试」，或密钥在实际请求后会自动生成各模型的独立状态"
      />
    </DataCard>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NButton, NDataTable, NSelect, NTag, type DataTableColumns } from 'naive-ui'
import { api, type Channel, type KeyModelStatusItem } from '@/api'
import { tokens } from '@/design/tokens'
import PageHeader from '@/design/components/PageHeader.vue'
import KpiCard from '@/design/components/KpiCard.vue'
import DataCard from '@/design/components/DataCard.vue'
import EmptyState from '@/design/components/EmptyState.vue'
import StatusDot from '@/design/components/StatusDot.vue'

const stats = ref<{ providers: number; keys: number; active: number; by_status: Record<string, number> }>({
  providers: 0,
  keys: 0,
  active: 0,
  by_status: {},
})

const abnormal = computed(() => Math.max(0, stats.value.keys - stats.value.active))
const statusRows = computed(() =>
  Object.entries(stats.value.by_status).map(([status, count]) => ({ status, count: Number(count) })),
)

const statusTextMap: Record<string, string> = {
  active: '可用',
  rate_limited: '限流中',
  exhausted: '已耗尽',
  invalid: '失效',
  disabled: '已停用',
}

const columns: DataTableColumns<{ status: string; count: number }> = [
  {
    title: '状态',
    key: 'status',
    render: (row) =>
      h(StatusDot, { status: row.status }, { default: () => statusTextMap[row.status] || row.status }),
  },
  { title: '数量', key: 'count' },
]

// ── 密钥×模型 细粒度状态 ──
const kmsLoading = ref(false)
const kmsItems = ref<KeyModelStatusItem[]>([])

const kmsStatusType: Record<string, 'success' | 'warning' | 'error' | 'default'> = {
  active: 'success',
  rate_limited: 'warning',
  exhausted: 'error',
  invalid: 'error',
  disabled: 'default',
}

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const kmsColumns: DataTableColumns<KeyModelStatusItem> = [
  { title: '密钥', key: 'key_name', minWidth: 120 },
  { title: '供应商', key: 'provider_name', width: 110 },
  { title: '模型', key: 'model_name', minWidth: 140 },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) =>
      h(NTag, { size: 'small', type: kmsStatusType[row.status] || 'default', bordered: false }, {
        default: () => statusTextMap[row.status] || row.status,
      }),
  },
  {
    title: '最近错误',
    key: 'last_error',
    minWidth: 180,
    render: (row) =>
      row.last_error
        ? h('span', { style: 'color:var(--n-text-color-2);font-size:12px' }, row.last_error.slice(0, 80))
        : '—',
  },
  {
    title: '最近测试',
    key: 'last_tested_at',
    width: 140,
    render: (row) => fmt(row.last_tested_at),
  },
]

async function loadKms() {
  kmsLoading.value = true
  try {
    const resp = await api.stats.keyModelStatuses()
    kmsItems.value = resp.items
  } finally {
    kmsLoading.value = false
  }
}

// ── 按渠道测试 ──
const channels = ref<Channel[]>([])
const selectedChannelId = ref<number | null>(null)
const channelTesting = ref(false)
const channelTestResult = ref<{ channel_name: string; total: number; ok: number; failed: number } | null>(null)

const channelOptions = computed(() =>
  channels.value.map((c) => ({ label: `${c.name}（${c.virtual_model}）`, value: c.id })),
)

// 选中渠道时，表格只显示该渠道对应的 (key_id, native_model) 记录
const displayKmsItems = computed(() => {
  if (!selectedChannelId.value) return kmsItems.value
  const ch = channels.value.find((c) => c.id === selectedChannelId.value)
  if (!ch) return kmsItems.value
  // 构建该渠道所有 tier 的 (key_id, native_model) 集合
  const pairs = new Set<string>()
  for (const tier of ch.tiers) {
    const mapping = ch.mappings?.find((m) => m.provider_id === tier.provider_id)
    const native = mapping?.native_model || ch.virtual_model
    for (const ck of tier.keys) {
      pairs.add(`${ck.key_id}:${native}`)
    }
  }
  return kmsItems.value.filter((item) => pairs.has(`${item.key_id}:${item.model_name}`))
})

async function loadChannels() {
  channels.value = await api.channels.list()
}

async function onTestChannel() {
  if (!selectedChannelId.value) return
  channelTesting.value = true
  channelTestResult.value = null
  try {
    const result = await api.channels.test(selectedChannelId.value)
    channelTestResult.value = {
      channel_name: result.channel_name || result.virtual_model,
      total: result.total,
      ok: result.ok,
      failed: result.failed,
    }
    // 测试完成后刷新状态表
    await loadKms()
  } catch (e) {
    console.error('channel test failed', e)
  } finally {
    channelTesting.value = false
  }
}

async function load() {
  stats.value = await api.stats.overview()
}
onMounted(() => {
  load()
  loadKms()
  loadChannels()
})
</script>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.status-card {
  min-height: 160px;
}
.kms-card {
  min-height: 200px;
}
.kms-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.test-result-bar {
  margin-bottom: 12px;
}
@media (max-width: 1024px) {
  .kpi-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
