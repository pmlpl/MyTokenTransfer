<template>
  <div>
    <div class="toolbar">
      <el-select v-model="status" placeholder="状态" clearable style="width: 140px" @change="load">
        <el-option label="成功" value="success" />
        <el-option label="失败" value="error" />
      </el-select>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="items" stripe>
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="created_at" label="时间" width="170">
        <template #default="{ row }">{{ fmt(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="virtual_model" label="虚拟模型" width="120" />
      <el-table-column prop="native_model" label="实际模型" width="120" />
      <el-table-column prop="key_id" label="Key #" width="70" />
      <el-table-column label="层" width="60">
        <template #default="{ row }">T{{ row.tier_order ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="Tokens (入/出)" width="120">
        <template #default="{ row }">{{ row.prompt_tokens }} / {{ row.completion_tokens }}</template>
      </el-table-column>
      <el-table-column prop="latency_ms" label="耗时(ms)" width="90" />
      <el-table-column label="流式" width="70">
        <template #default="{ row }">{{ row.stream ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'success' ? 'success' : 'danger'">
            {{ row.status === 'success' ? '成功' : '失败' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="error" label="错误" min-width="220">
        <template #default="{ row }">
          <span class="err">{{ row.error || '—' }}</span>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api, type CallLogItem } from '@/api'

const items = ref<CallLogItem[]>([])
const loading = ref(false)
const status = ref<string | undefined>(undefined)

async function load() {
  loading.value = true
  try {
    const r = await api.logs.list({ limit: 200, status: status.value })
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
.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.err {
  color: #f56c6c;
  font-size: 12px;
  word-break: break-all;
}
</style>
