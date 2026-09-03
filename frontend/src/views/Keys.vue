<template>
  <div class="keys-page">
    <PageHeader title="密钥管理" subtitle="API 密钥池 · 健康测试 · 备份导入导出">
      <template #actions>
        <n-button @click="openExport">导出备份</n-button>
        <n-button @click="openImport">导入备份</n-button>
        <n-button type="primary" @click="openAdd">新增密钥</n-button>
      </template>
    </PageHeader>

    <DataCard>
      <template #header>
        <span class="card-title">密钥列表</span>
      </template>
      <template #actions>
        <n-select
          v-model:value="filter.provider_id"
          :options="providerOptions"
          placeholder="供应商"
          clearable
          size="small"
          style="width: 160px"
          @update:value="load"
        />
        <n-select
          v-model:value="filter.status"
          :options="statusOptions"
          placeholder="状态"
          clearable
          size="small"
          style="width: 140px"
          @update:value="load"
        />
        <n-button size="small" type="primary" :loading="testing" @click="onBatchTest">
          批量健康测试
        </n-button>
      </template>

      <n-data-table
        :columns="columns"
        :data="keys"
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
      :title="dialog.id ? '编辑密钥' : '新增密钥'"
      style="width: 520px; max-width: 92vw"
      :mask-closable="false"
    >
      <n-form :model="form" label-placement="left" label-width="80">
        <n-form-item label="供应商">
          <n-select
            v-model:value="form.provider_id"
            :options="providerOptions"
            placeholder="选择供应商"
          />
        </n-form-item>
        <n-form-item label="分组">
          <n-select
            v-model:value="form.group_id"
            :options="groupOptions"
            placeholder="无"
            clearable
          />
        </n-form-item>
        <n-form-item label="账号">
          <n-input v-model:value="form.account_label" placeholder="如 账号A / org-xxx" />
        </n-form-item>
        <n-form-item label="名称">
          <n-input v-model:value="form.name" placeholder="便于识别的名字" />
        </n-form-item>
        <n-form-item label="密钥">
          <n-input
            v-model:value="form.key_value"
            type="password"
            show-password-on="click"
            :placeholder="dialog.id ? '留空则不修改' : '明文密钥，落库前加密'"
          />
        </n-form-item>
        <n-form-item label="权重">
          <n-input-number v-model:value="form.weight" :min="1" :max="100" />
        </n-form-item>
        <n-form-item label="标签">
          <n-input v-model:value="form.tags" placeholder="逗号分隔" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <n-button @click="dialog.visible = false">取消</n-button>
          <n-button type="primary" :loading="dialog.saving" @click="onSave">保存</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 导出/导入对话框 -->
    <n-modal
      v-model:show="bundleDialog.visible"
      preset="card"
      :title="bundleDialog.mode === 'export' ? '导出备份' : '导入备份'"
      style="width: 480px; max-width: 92vw"
    >
      <n-form label-placement="left" label-width="80">
        <n-form-item label="加密口令">
          <n-input
            v-model:value="bundleDialog.password"
            type="password"
            show-password-on="click"
            :placeholder="bundleDialog.mode === 'export' ? '用于加密导出包（导入时需一致）' : '导出时设置的口令'"
          />
        </n-form-item>
        <n-form-item v-if="bundleDialog.mode === 'import'" label="备份文件">
          <input ref="fileInput" type="file" accept=".json" @change="onFilePick" />
        </n-form-item>
      </n-form>
      <div class="hint-box">
        导出包用该口令加密（口令不落库），包含全部供应商/分组/密钥明文，请妥善保管。<br />
        导入按名称调谐：已存在同名密钥跳过，缺失的补建。
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <n-button @click="bundleDialog.visible = false">取消</n-button>
          <n-button type="primary" :loading="bundleDialog.saving" @click="onBundleSubmit">
            {{ bundleDialog.mode === 'export' ? '导出' : '导入' }}
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, reactive, ref, onMounted } from 'vue'
import {
  NButton, NDataTable, NInput, NInputNumber, NSelect, NForm, NFormItem,
  NModal, NPopconfirm, NTag, useMessage, useDialog,
  type DataTableColumns, type SelectOption,
} from 'naive-ui'
import { api, type ApiKey, type Provider, type KeyGroup } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'
import StatusDot from '@/design/components/StatusDot.vue'

type KeyRow = ApiKey & { _testing?: boolean }

const keys = ref<KeyRow[]>([])
const providers = ref<Provider[]>([])
const groups = ref<KeyGroup[]>([])
const loading = ref(false)
const testing = ref(false)
const message = useMessage()
const nDialog = useDialog() // Naive UI dialog API；避免与下面 reactive 同名

const filter = reactive<{ provider_id?: number; status?: string }>({})

const providerOptions = computed<SelectOption[]>(() =>
  providers.value.map((p) => ({ label: p.name, value: p.id })),
)
const groupOptions = computed<SelectOption[]>(() =>
  groups.value.map((g) => ({ label: g.name, value: g.id })),
)
const statusOptions: SelectOption[] = [
  { label: '可用', value: 'active' },
  { label: '限流中', value: 'rate_limited' },
  { label: '已耗尽', value: 'exhausted' },
  { label: '失效', value: 'invalid' },
  { label: '已停用', value: 'disabled' },
]

interface DialogState { visible: boolean; id: number | null; saving: boolean }
interface KeyForm {
  provider_id?: number
  group_id?: number
  account_label: string
  name: string
  key_value: string
  weight: number
  tags: string
}

const dialog = reactive<DialogState>({
  visible: false,
  id: null,
  saving: false,
})
const form = reactive<KeyForm>({
  provider_id: undefined,
  group_id: undefined,
  account_label: '',
  name: '',
  key_value: '',
  weight: 1,
  tags: '',
})

const statusTextMap: Record<string, string> = {
  active: '可用',
  rate_limited: '限流中',
  exhausted: '已耗尽',
  invalid: '失效',
  disabled: '已停用',
}

const columns: DataTableColumns<KeyRow> = [
  { title: '名称', key: 'name', minWidth: 120 },
  { title: '供应商', key: 'provider_name', width: 110 },
  { title: '账号', key: 'account_label', width: 120 },
  {
    title: '密钥',
    key: 'key_mask',
    minWidth: 140,
    render: (row) =>
      h('span', { style: 'display:inline-flex; align-items:center; gap:8px' }, [
        h('span', null, row.key_mask),
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => onReveal(row) }, { default: () => '揭示' }),
      ]),
  },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h(StatusDot, { status: row.status }, { default: () => statusTextMap[row.status] || row.status }),
  },
  { title: '权重', key: 'weight', width: 70 },
  { title: '标签', key: 'tags', minWidth: 100 },
  {
    title: '最近测试',
    key: 'last_tested_at',
    width: 170,
    render: (row) => fmt(row.last_tested_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 220,
    fixed: 'right',
    render: (row) =>
      h('div', { style: 'display:flex; gap:4px' }, [
        h(NButton, { size: 'tiny', quaternary: true, loading: row._testing, onClick: () => onTest(row) }, { default: () => '测试' }),
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => openEdit(row) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => onRemove(row) },
          {
            default: () => '确认删除该密钥？',
            trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'error' }, { default: () => '删除' }),
          },
        ),
      ]),
  },
]

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
    message.warning('请选择供应商')
    return
  }
  if (!dialog.id && !form.key_value) {
    message.warning('请填写密钥')
    return
  }
  const data = { ...form } as Partial<KeyForm>
  if (dialog.id && !data.key_value) delete data.key_value
  dialog.saving = true
  try {
    if (dialog.id) {
      await api.keys.update(dialog.id, data)
      message.success('已更新')
    } else {
      await api.keys.create(data)
      message.success('已新增')
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
    message.success('已删除')
    await load()
  } catch {
    // 拦截器已提示
  }
}

async function onReveal(row: ApiKey) {
  nDialog.warning({
    title: '提示',
    content: '揭示明文密钥将显示完整内容，确认继续？',
    positiveText: '继续',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const { key_value } = await api.keys.reveal(row.id)
        nDialog.info({
          title: `${row.name} · 明文密钥`,
          content: () =>
            h('div', { style: 'word-break:break-all; font-family:monospace' }, key_value),
        })
      } catch {
        // 拦截器已提示
      }
    },
  })
}

async function onTest(row: KeyRow) {
  row._testing = true
  try {
    const r = await api.keys.test(row.id)
    if (r.ok) {
      message.success('测试通过')
    } else {
      message.error(`失败：${r.error || ''}`)
    }
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
    const fail = results.length - ok
    // 修复原"全坏也绿"bug：按 ok/fail 比例选择提示级别
    if (fail === 0) {
      message.success(`批量测试完成：${ok}/${results.length} 全部可用`)
    } else if (ok === 0) {
      message.error(`批量测试完成：${ok}/${results.length} 全部失败`)
    } else {
      message.warning(`批量测试完成：${ok}/${results.length} 可用，${fail} 失败`)
    }
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
      message.error('备份文件不是合法 JSON')
    })
}

async function onBundleSubmit() {
  if (!bundleDialog.password) {
    message.warning('请输入加密口令')
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
      message.success('已导出（请妥善保管文件与口令）')
    } else {
      if (!pickedBundle) {
        message.warning('请先选择备份文件')
        return
      }
      const r = await api.keys.importBundle(bundleDialog.password, pickedBundle)
      message.success(`导入完成：新增密钥 ${r.keys_imported}，跳过 ${r.keys_skipped}（供应商 +${r.providers} / 分组 +${r.groups}）`)
      await load()
    }
    bundleDialog.visible = false
  } catch {
    // 拦截器已提示
  } finally {
    bundleDialog.saving = false
  }
}

function fmt(s: string | null) {
  return s ? new Date(s).toLocaleString('zh-CN') : '—'
}

onMounted(async () => {
  await loadMeta()
  await load()
})
</script>

<style scoped>
.keys-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text-primary, #0f172a);
}
.hint-box {
  color: var(--app-text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.6;
}
</style>
