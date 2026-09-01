<template>
  <div>
    <div class="toolbar">
      <el-select v-model="filter.provider_id" placeholder="供应商" clearable style="width: 160px" @change="load">
        <el-option v-for="p in providers" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
      <el-select v-model="filter.status" placeholder="状态" clearable style="width: 140px" @change="load">
        <el-option label="可用" value="active" />
        <el-option label="限流中" value="rate_limited" />
        <el-option label="已耗尽" value="exhausted" />
        <el-option label="失效" value="invalid" />
        <el-option label="已停用" value="disabled" />
      </el-select>
      <el-button type="primary" :loading="testing" @click="onBatchTest">批量健康测试</el-button>
      <div style="flex: 1" />
      <el-button @click="openExport">导出备份</el-button>
      <el-button @click="openImport">导入备份</el-button>
      <el-button type="success" @click="openAdd">新增密钥</el-button>
    </div>

    <el-table v-loading="loading" :data="keys" stripe>
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column prop="provider_name" label="供应商" width="110" />
      <el-table-column prop="account_label" label="账号" width="120" />
      <el-table-column prop="key_mask" label="密钥" min-width="140">
        <template #default="{ row }">
          <span>{{ row.key_mask }}</span>
          <el-button link size="small" @click="onReveal(row)">揭示</el-button>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusTag(row.status)">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="weight" label="权重" width="70" />
      <el-table-column prop="tags" label="标签" min-width="100" />
      <el-table-column prop="last_tested_at" label="最近测试" width="170">
        <template #default="{ row }">{{ fmt(row.last_tested_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link size="small" :loading="row._testing" @click="onTest(row)">测试</el-button>
          <el-button link size="small" @click="openEdit(row)">编辑</el-button>
          <el-popconfirm title="确认删除该密钥？" @confirm="onRemove(row)">
            <template #reference>
              <el-button link size="small" type="danger">删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新增/编辑对话框 -->
    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑密钥' : '新增密钥'" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="供应商">
          <el-select v-model="form.provider_id" placeholder="选择供应商" style="width: 100%">
            <el-option v-for="p in providers" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="分组">
          <el-select v-model="form.group_id" placeholder="无" clearable style="width: 100%">
            <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="账号">
          <el-input v-model="form.account_label" placeholder="如 账号A / org-xxx" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="便于识别的名字" />
        </el-form-item>
        <el-form-item label="密钥">
          <el-input
            v-model="form.key_value"
            type="password"
            show-password
            :placeholder="dialog.id ? '留空则不修改' : '明文密钥，落库前加密'"
          />
        </el-form-item>
        <el-form-item label="权重">
          <el-input-number v-model="form.weight" :min="1" :max="100" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="form.tags" placeholder="逗号分隔" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 导出/导入对话框 -->
    <el-dialog v-model="bundleDialog.visible" :title="bundleDialog.mode === 'export' ? '导出备份' : '导入备份'" width="480px">
      <el-form label-width="90px">
        <el-form-item label="加密口令">
          <el-input
            v-model="bundleDialog.password"
            type="password"
            show-password
            :placeholder="bundleDialog.mode === 'export' ? '用于加密导出包（导入时需一致）' : '导出时设置的口令'"
          />
        </el-form-item>
        <el-form-item v-if="bundleDialog.mode === 'import'" label="备份文件">
          <input ref="fileInput" type="file" accept=".json" @change="onFilePick" />
        </el-form-item>
      </el-form>
      <div style="color: #999; font-size: 12px; line-height: 1.6">
        导出包用该口令加密（口令不落库），包含全部供应商/分组/密钥明文，请妥善保管。<br />
        导入按名称调谐：已存在同名密钥跳过，缺失的补建。
      </div>
      <template #footer>
        <el-button @click="bundleDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="bundleDialog.saving" @click="onBundleSubmit">
          {{ bundleDialog.mode === 'export' ? '导出' : '导入' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type ApiKey, type Provider, type KeyGroup } from '@/api'

const keys = ref<(ApiKey & { _testing?: boolean })[]>([])
const providers = ref<Provider[]>([])
const groups = ref<KeyGroup[]>([])
const loading = ref(false)
const testing = ref(false)

const filter = reactive<{ provider_id?: number; status?: string }>({})

const dialog = reactive<{ visible: boolean; id: number | null; saving: boolean }>({
  visible: false,
  id: null,
  saving: false,
})
const form = reactive<Record<string, unknown>>({
  provider_id: undefined,
  group_id: undefined,
  account_label: '',
  name: '',
  key_value: '',
  weight: 1,
  tags: '',
})

async function load() {
  loading.value = true
  try {
    keys.value = await api.keys.list(filter)
  } finally {
    loading.value = false
  }
}

async function loadMeta() {
  ;[providers.value, groups.value] = await Promise.all([api.providers.list(), api.groups.list()])
}

function openAdd() {
  dialog.id = null
  Object.assign(form, {
    provider_id: providers.value[0]?.id,
    group_id: undefined,
    account_label: '',
    name: '',
    key_value: '',
    weight: 1,
    tags: '',
  })
  dialog.visible = true
}

function openEdit(row: ApiKey) {
  dialog.id = row.id
  Object.assign(form, {
    provider_id: row.provider_id,
    group_id: row.group_id ?? undefined,
    account_label: row.account_label ?? '',
    name: row.name,
    key_value: '',
    weight: row.weight,
    tags: row.tags ?? '',
  })
  dialog.visible = true
}

async function onSave() {
  if (!form.provider_id) {
    ElMessage.warning('请选择供应商')
    return
  }
  if (!dialog.id && !form.key_value) {
    ElMessage.warning('请填写密钥')
    return
  }
  const data = { ...form }
  if (dialog.id && !data.key_value) delete data.key_value
  dialog.saving = true
  try {
    if (dialog.id) {
      await api.keys.update(dialog.id, data)
      ElMessage.success('已更新')
    } else {
      await api.keys.create(data)
      ElMessage.success('已新增')
    }
    dialog.visible = false
    await load()
  } catch {
    // 拦截器已提示
  } finally {
    dialog.saving = false
  }
}

async function onRemove(row: ApiKey) {
  try {
    await api.keys.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    // 拦截器已提示
  }
}

async function onReveal(row: ApiKey) {
  try {
    await ElMessageBox.confirm('揭示明文密钥将显示完整内容，确认继续？', '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    const { key_value } = await api.keys.reveal(row.id)
    await ElMessageBox.alert(
      `<div style="word-break:break-all;font-family:monospace">${escapeHtml(key_value)}</div>`,
      `${row.name} · 明文密钥`,
      { dangerouslyUseHTMLString: true },
    )
  } catch {
    // 拦截器已提示
  }
}

async function onTest(row: ApiKey & { _testing?: boolean }) {
  row._testing = true
  try {
    const r = await api.keys.test(row.id)
    ElMessage[r.ok ? 'success' : 'error'](r.ok ? '测试通过' : `失败：${r.error || ''}`)
    await load()
  } catch {
    // 拦截器已提示
  } finally {
    row._testing = false
  }
}

async function onBatchTest() {
  testing.value = true
  try {
    const results = await api.keys.testBatch(filter)
    const ok = results.filter((r: any) => r.ok).length
    ElMessage.success(`批量测试完成：${ok}/${results.length} 可用`)
    await load()
  } catch {
    // 拦截器已提示
  } finally {
    testing.value = false
  }
}

// ---- 导出/导入 ----
const fileInput = ref<HTMLInputElement | null>(null)
const bundleDialog = reactive<{
  visible: boolean
  mode: 'export' | 'import'
  password: string
  saving: boolean
}>({ visible: false, mode: 'export', password: '', saving: false })
let pickedBundle: Record<string, unknown> | null = null

function openExport() {
  bundleDialog.mode = 'export'
  bundleDialog.password = ''
  bundleDialog.visible = true
}

function openImport() {
  bundleDialog.mode = 'import'
  bundleDialog.password = ''
  pickedBundle = null
  bundleDialog.visible = true
}

function onFilePick(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  file
    .text()
    .then((text) => {
      pickedBundle = JSON.parse(text)
    })
    .catch(() => {
      ElMessage.error('备份文件不是合法 JSON')
    })
}

async function onBundleSubmit() {
  if (!bundleDialog.password) {
    ElMessage.warning('请输入加密口令')
    return
  }
  bundleDialog.saving = true
  try {
    if (bundleDialog.mode === 'export') {
      const bundle = await api.keys.exportBundle(bundleDialog.password)
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `mytokentransfer-backup-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      ElMessage.success('已导出（请妥善保管文件与口令）')
    } else {
      if (!pickedBundle) {
        ElMessage.warning('请先选择备份文件')
        return
      }
      const r = await api.keys.importBundle(bundleDialog.password, pickedBundle)
      ElMessage.success(`导入完成：新增密钥 ${r.keys_imported}，跳过 ${r.keys_skipped}（供应商 +${r.providers} / 分组 +${r.groups}）`)
      await load()
    }
    bundleDialog.visible = false
  } catch {
    // 拦截器已提示
  } finally {
    bundleDialog.saving = false
  }
}

function statusText(s: string) {
  return { active: '可用', rate_limited: '限流中', exhausted: '已耗尽', invalid: '失效', disabled: '已停用' }[s] || s
}
function statusTag(s: string): 'success' | 'warning' | 'danger' | 'info' {
  return ({ active: 'success', rate_limited: 'warning', exhausted: 'danger', invalid: 'danger', disabled: 'info' } as const)[
    s as 'active'
  ] || 'info'
}
function fmt(s: string | null) {
  return s ? new Date(s).toLocaleString('zh-CN') : '—'
}
function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string))
}

onMounted(async () => {
  await loadMeta()
  await load()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}
</style>
