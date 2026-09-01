<template>
  <div>
    <div class="toolbar">
      <span class="hint">密钥状态劣化（额度耗尽 / 认证失效）时自动记录告警事件。</span>
      <div style="flex: 1" />
      <el-radio-group v-model="filter" @change="load">
        <el-radio-button :value="false">全部</el-radio-button>
        <el-radio-button :value="true">未解决</el-radio-button>
      </el-radio-group>
      <el-button :icon="Refresh" @click="load" style="margin-left: 12px" />
    </div>

    <el-table v-loading="loading" :data="list" stripe>
      <el-table-column label="级别" width="100">
        <template #default="{ row }">
          <el-tag :type="row.severity === 'critical' ? 'danger' : 'warning'" size="small">
            {{ row.severity }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="150">
        <template #default="{ row }">{{ typeLabel(row.type) }}</template>
      </el-table-column>
      <el-table-column prop="message" label="事件" min-width="320" show-overflow-tooltip />
      <el-table-column label="密钥" width="90">
        <template #default="{ row }">{{ row.key_id ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.resolved ? 'info' : 'danger'" size="small" effect="plain">
            {{ row.resolved ? '已解决' : '未解决' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ fmt(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="!row.resolved"
            link
            size="small"
            type="success"
            :loading="row._resolving"
            @click="onResolve(row)"
          >标记解决</el-button>
          <span v-else style="color: #909399; font-size: 12px">{{ fmt(row.resolved_at) }}</span>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api, type AlertItem } from '@/api'

const list = ref<(AlertItem & { _resolving?: boolean })[]>([])
const loading = ref(false)
const filter = ref(true)

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

async function onResolve(row: AlertItem & { _resolving?: boolean }) {
  row._resolving = true
  try {
    await api.alerts.resolve(row.id)
    ElMessage.success('已标记解决')
    await load()
  } catch {
    // 拦截器已提示
  } finally {
    row._resolving = false
  }
}

function typeLabel(t: string) {
  return { key_exhausted: '密钥额度耗尽', key_invalid: '密钥认证失效' }[t] || t
}
function fmt(s: string | null) {
  if (!s) return '-'
  return s.replace('T', ' ').slice(0, 19)
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; align-items: center; margin-bottom: 16px; gap: 8px; }
.hint { color: #909399; font-size: 12px; }
</style>
