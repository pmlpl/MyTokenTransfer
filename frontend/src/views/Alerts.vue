<template>
  <div class="alerts-page">
    <PageHeader title="告警事件" subtitle="密钥状态劣化（额度耗尽 / 认证失效）自动记录">
      <template #actions>
        <n-radio-group v-model:value="filter" @update:value="load">
          <n-radio-button :value="false">全部</n-radio-button>
          <n-radio-button :value="true">未解决</n-radio-button>
        </n-radio-group>
        <n-button quaternary circle @click="load">
          <template #icon>
            <n-icon :component="RefreshIcon" />
          </template>
        </n-button>
      </template>
    </PageHeader>

    <DataCard title="告警列表">
      <n-data-table
        :columns="columns"
        :data="list"
        :loading="loading"
        :bordered="false"
        :single-line="false"
        size="small"
      />
    </DataCard>
  </div>
</template>

<script setup lang="ts">
import { h, ref, onMounted } from 'vue'
import {
  NButton, NDataTable, NRadioGroup, NRadioButton, NTag, NIcon,
  useMessage, type DataTableColumns,
} from 'naive-ui'
import { Refresh as RefreshIcon } from '@vicons/ionicons5'
import { api, type AlertItem } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'

type AlertRow = AlertItem & { _resolving?: boolean }

const list = ref<AlertRow[]>([])
const loading = ref(false)
const filter = ref(true)
const message = useMessage()

const columns: DataTableColumns<AlertRow> = [
  {
    title: '级别',
    key: 'severity',
    width: 100,
    render: (row) =>
      h(
        NTag,
        { type: row.severity === 'critical' ? 'error' : 'warning', size: 'small' },
        { default: () => row.severity },
      ),
  },
  {
    title: '类型',
    key: 'type',
    width: 150,
    render: (row) => typeLabel(row.type),
  },
  { title: '事件', key: 'message', minWidth: 320, ellipsis: { tooltip: true } },
  {
    title: '密钥',
    key: 'key_id',
    width: 90,
    render: (row) => (row.key_id ?? '-'),
  },
  {
    title: '状态',
    key: 'resolved',
    width: 90,
    render: (row) =>
      h(
        NTag,
        { type: row.resolved ? 'default' : 'error', size: 'small', bordered: false },
        { default: () => (row.resolved ? '已解决' : '未解决') },
      ),
  },
  {
    title: '时间',
    key: 'created_at',
    width: 170,
    render: (row) => fmt(row.created_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 110,
    fixed: 'right',
    render: (row) =>
      row.resolved
        ? h('span', { style: 'color: var(--app-text-secondary); font-size: 12px' }, fmt(row.resolved_at))
        : h(
            NButton,
            { size: 'tiny', quaternary: true, type: 'success', loading: row._resolving, onClick: () => onResolve(row) },
            { default: () => '标记解决' },
          ),
  },
]

async function load() {
  loading.value = true
  try {
    const resp = await api.alerts.list({ unresolved: filter.value || undefined, limit: 500 })
    list.value = resp.items
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}

async function onResolve(row: AlertRow) {
  row._resolving = true
  try {
    await api.alerts.resolve(row.id)
    message.success('已标记解决')
    await load()
  } catch {
    // 拦截器已提示
  } finally {
    row._resolving = false
  }
}

function typeLabel(t: string) {
  return ({ key_exhausted: '密钥额度耗尽', key_invalid: '密钥认证失效' } as const)[t as 'key_exhausted'] || t
}
function fmt(s: string | null) {
  if (!s) return '-'
  return s.replace('T', ' ').slice(0, 19)
}

onMounted(load)
</script>

<style scoped>
.alerts-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
