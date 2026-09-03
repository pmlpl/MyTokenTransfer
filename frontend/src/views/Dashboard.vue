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
        :bordered="false"
        :single-line="false"
        size="small"
      />
      <EmptyState v-if="!statusRows.length" title="暂无密钥" description="去密钥管理添加第一条 API 密钥" />
    </DataCard>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NDataTable, type DataTableColumns } from 'naive-ui'
import { api } from '@/api'
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

async function load() {
  stats.value = await api.stats.overview()
}
onMounted(load)
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
  min-height: 200px;
}
@media (max-width: 1024px) {
  .kpi-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
