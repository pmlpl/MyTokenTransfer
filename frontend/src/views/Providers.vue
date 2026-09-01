<template>
  <div>
    <div class="toolbar">
      <div style="flex: 1" />
      <el-button type="success" @click="openAdd">新增供应商</el-button>
    </div>

    <el-table v-loading="loading" :data="list" stripe>
      <el-table-column prop="name" label="名称" width="120" />
      <el-table-column prop="slug" label="slug" width="120" />
      <el-table-column prop="base_url" label="Base URL" min-width="220" />
      <el-table-column prop="probe_kind" label="探测方式" width="150" />
      <el-table-column label="模型数" width="80">
        <template #default="{ row }">{{ (row.models || []).length || '—' }}</template>
      </el-table-column>
      <el-table-column prop="is_active" label="启用" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '是' : '否' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link size="small" @click="openEdit(row)">编辑</el-button>
          <el-popconfirm title="删除供应商？其下密钥需先移除" @confirm="onRemove(row)">
            <template #reference>
              <el-button link size="small" type="danger">删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑供应商' : '新增供应商'" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="如 OpenAI" /></el-form-item>
        <el-form-item label="slug">
          <el-input v-model="form.slug" placeholder="openai / anthropic / gemini" />
        </el-form-item>
        <el-form-item label="Base URL"><el-input v-model="form.base_url" placeholder="留空用默认" /></el-form-item>
        <el-form-item label="探测方式">
          <el-select v-model="form.probe_kind" style="width: 100%">
            <el-option label="OpenAI /v1/models" value="openai_models" />
            <el-option label="Anthropic /v1/models" value="anthropic" />
            <el-option label="Gemini /v1beta/models" value="gemini_models" />
          </el-select>
        </el-form-item>
        <el-form-item label="模型列表">
          <el-input
            v-model="form.models_text"
            type="textarea"
            :rows="4"
            placeholder="每行一个模型名，如：&#10;SenseNova-V6.5-Turbo&#10;SenseChat-5"
          />
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type Provider } from '@/api'

const list = ref<Provider[]>([])
const loading = ref(false)
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
    ElMessage.warning('名称与 slug 必填')
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
      ElMessage.success('已更新')
    } else {
      await api.providers.create(data)
      ElMessage.success('已新增')
    }
    dialog.visible = false
    await load()
  } finally {
    dialog.saving = false
  }
}

async function onRemove(row: Provider) {
  await api.providers.remove(row.id)
  ElMessage.success('已删除')
  await load()
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  margin-bottom: 16px;
}
</style>
