<template>
  <div class="logs-page">
    <PageHeader title="调用日志" subtitle="网关转发记录 · 成功/失败 · token 与耗时" />

    <DataCard title="日志列表">
      <template #actions>
        <n-select
          v-model:value="status"
          :options="statusOptions"
          placeholder="状态"
          clearable
          size="small"
          style="width: 140px"
          @update:value="load"
        />
        <n-button size="small" @click="load">刷新</n-button>
      </template>

      <n-data-table
        :columns="columns"
        :data="items"
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
  NButton, NDataTable, NSelect, NTag, type DataTableColumns, type SelectOption,
} from 'naive-ui'
import { api, type CallLogItem } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'

const items = ref<CallLogItem[]>([])
const loading = ref(false)
const status = ref<string | null>(null)

const statusOptions: SelectOption[] = [
  { label: '成功', value: 'success' },
  { label: '失败', value: 'error' },
]

const columns: DataTableColumns<CallLogItem> = [
  { title: '#', key: 'id', width: 60 },
  {
    title: '时间',
    key: 'created_at',
    width: 170,
    render: (row) => fmt(row.created_at),
  },
  { title: '虚拟模型', key: 'virtual_model', width: 120 },
  { title: '实际模型', key: 'native_model', width: 120 },
  { title: 'Key #', key: 'key_id', width: 70 },
  {
    title: '层',
    key: 'tier',
    width: 60,
    render: (row) => `T${row.tier_order ?? '-'}`,
  },
  {
    title: 'Tokens (入/出)',
    key: 'tokens',
    width: 120,
    render: (row) => `${row.prompt_tokens} / ${row.completion_tokens}`,
  },
  { title: '耗时(ms)', key: 'latency_ms', width: 90 },
  {
    title: '流式',
    key: 'stream',
    width: 70,
    render: (row) => (row.stream ? '是' : '否'),
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row) =>
      h(
        NTag,
        { type: row.status === 'success' ? 'success' : 'error', size: 'small' },
        { default: () => (row.status === 'success' ? '成功' : '失败') },
      ),
  },
  {
    title: '错误',
    key: 'error',
    minWidth: 220,
    render: (row) => h('span', { class: 'err' }, row.error || '—'),
  },
]

async function load() {
  loading.value = true
  try {
    const r = await api.logs.list({ limit: 200, status: status.value || undefined })
    items.value = r.items
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}

function fmt(s: string | null) {
  return s ? new Date(s).toLocaleString('zh-CN') : '—'
}

onMounted(load)
</script>

<style scoped>
.logs-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
:deep(.err) {
  color: var(--app-danger, #f56c6c);
  font-size: 12px;
  word-break: break-all;
}
</style>
