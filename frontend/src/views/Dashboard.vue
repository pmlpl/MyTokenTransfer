<template>
  <div>
    <el-row :gutter="20">
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="label">供应商</div>
          <div class="num">{{ stats.providers }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="label">密钥总数</div>
          <div class="num">{{ stats.keys }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="label">可用</div>
          <div class="num" style="color: #67c23a">{{ stats.active }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="label">异常 / 受限</div>
          <div class="num" style="color: #f56c6c">{{ abnormal }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card style="margin-top: 20px">
      <template #header>密钥状态分布</template>
      <el-table :data="statusRows" stripe>
        <el-table-column prop="status" label="状态" width="200">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="count" label="数量" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { api } from '@/api'

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

function statusText(s: string) {
  return { active: '可用', rate_limited: '限流中', exhausted: '已耗尽', invalid: '失效', disabled: '已停用' }[s] || s
}
function statusTag(s: string): 'success' | 'warning' | 'danger' | 'info' {
  return ({ active: 'success', rate_limited: 'warning', exhausted: 'danger', invalid: 'danger', disabled: 'info' } as const)[
    s as 'active'
  ] || 'info'
}

async function load() {
  stats.value = await api.stats.overview()
}
onMounted(load)
</script>

<style scoped>
.label {
  color: #909399;
  font-size: 13px;
}
.num {
  font-size: 30px;
  font-weight: 600;
  margin-top: 6px;
}
</style>
