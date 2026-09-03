<template>
  <div class="providers-page">
    <PageHeader title="供应商" subtitle="API 厂商 · 模型列表 · 探测方式">
      <template #actions>
        <n-button type="primary" @click="openAdd">新增供应商</n-button>
      </template>
    </PageHeader>

    <DataCard title="供应商列表">
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
      :title="dialog.id ? '编辑供应商' : '新增供应商'"
      style="width: 520px; max-width: 92vw"
      :mask-closable="false"
    >
      <n-form :model="form" label-placement="left" label-width="90">
        <n-form-item label="名称">
          <n-input v-model:value="form.name" placeholder="如 OpenAI" />
        </n-form-item>
        <n-form-item label="slug">
          <n-input v-model:value="form.slug" placeholder="openai / anthropic / gemini" />
        </n-form-item>
        <n-form-item label="Base URL">
          <n-input v-model:value="form.base_url" placeholder="留空用默认" />
        </n-form-item>
        <n-form-item label="探测方式">
          <n-select v-model:value="form.probe_kind" :options="probeOptions" />
        </n-form-item>
        <n-form-item label="模型列表">
          <n-input
            v-model:value="form.models_text"
            type="textarea"
            :rows="4"
            placeholder="每行一个模型名，如：&#10;SenseNova-V6.5-Turbo&#10;SenseChat-5"
          />
        </n-form-item>
        <n-form-item label="备注">
          <n-input v-model:value="form.note" type="textarea" :rows="2" />
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
import { h, reactive, ref, onMounted } from 'vue'
import {
  NButton, NDataTable, NInput, NSelect, NForm, NFormItem, NModal,
  NPopconfirm, NTag, useMessage, type DataTableColumns, type SelectOption,
} from 'naive-ui'
import { api, type Provider } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'

const list = ref<Provider[]>([])
const loading = ref(false)
const message = useMessage()

const probeOptions: SelectOption[] = [
  { label: 'OpenAI /v1/models', value: 'openai_models' },
  { label: 'Anthropic /v1/models', value: 'anthropic' },
  { label: 'Gemini /v1beta/models', value: 'gemini_models' },
]

const dialog = reactive<{ visible: boolean; id: number | null; saving: boolean }>({
  visible: false,
  id: null,
  saving: false,
})
const form = reactive<{
  name: string
  slug: string
  base_url: string
  probe_kind: string
  models_text: string
  note: string
}>({
  name: '',
  slug: '',
  base_url: '',
  probe_kind: 'openai_models',
  models_text: '',
  note: '',
})

const columns: DataTableColumns<Provider> = [
  { title: '名称', key: 'name', width: 120 },
  { title: 'slug', key: 'slug', width: 120 },
  { title: 'Base URL', key: 'base_url', minWidth: 220 },
  { title: '探测方式', key: 'probe_kind', width: 150 },
  {
    title: '模型数',
    key: 'models_count',
    width: 80,
    render: (row) => (row.models?.length || '—') as unknown as string,
  },
  {
    title: '启用',
    key: 'is_active',
    width: 80,
    render: (row) =>
      h(NTag, { type: row.is_active ? 'success' : 'default', size: 'small' }, {
        default: () => (row.is_active ? '是' : '否'),
      }),
  },
  {
    title: '操作',
    key: 'actions',
    width: 180,
    fixed: 'right',
    render: (row) =>
      h('div', { style: 'display:flex; gap:4px' }, [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => openEdit(row) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => onRemove(row) },
          {
            default: () => '删除供应商？其下密钥需先移除',
            trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'error' }, { default: () => '删除' }),
          },
        ),
      ]),
  },
]

async function load() {
  loading.value = true
  try {
    list.value = await api.providers.list()
  } finally {
    loading.value = false
  }
}

function openAdd() {
  dialog.id = null
  Object.assign(form, { name: '', slug: '', base_url: '', probe_kind: 'openai_models', models_text: '', note: '' })
  dialog.visible = true
}

function openEdit(row: Provider) {
  dialog.id = row.id
  Object.assign(form, {
    name: row.name,
    slug: row.slug,
    base_url: row.base_url || '',
    probe_kind: row.probe_kind,
    models_text: (row.models || []).join('\n'),
    note: row.note || '',
  })
  dialog.visible = true
}

async function onSave() {
  if (!form.name || !form.slug) {
    message.warning('名称与 slug 必填')
    return
  }
  const models = form.models_text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
  const data = {
    ...form,
    base_url: form.base_url || null,
    models,
    note: form.note || null,
    is_active: true,
  }
  dialog.saving = true
  try {
    if (dialog.id) {
      await api.providers.update(dialog.id, data)
      message.success('已更新')
    } else {
      await api.providers.create(data)
      message.success('已新增')
    }
    dialog.visible = false
    await load()
  } finally {
    dialog.saving = false
  }
}

async function onRemove(row: Provider) {
  try {
    await api.providers.remove(row.id)
    message.success('已删除')
    await load()
  } catch {
    // 拦截器已提示
  }
}

onMounted(load)
</script>

<style scoped>
.providers-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
