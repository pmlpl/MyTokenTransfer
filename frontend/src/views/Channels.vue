<template>
  <div class="channels-page">
    <PageHeader title="转发通道" subtitle="虚拟模型 · 多层 fallback · 策略调度">
      <template #actions>
        <n-button type="primary" @click="openAdd">新增通道</n-button>
      </template>
    </PageHeader>

    <DataCard title="通道列表">
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
      :title="dialog.id ? '编辑通道' : '新增通道'"
      style="width: 760px; max-width: 96vw"
      :mask-closable="false"
    >
      <n-form :model="form" label-placement="left" label-width="90">
        <div class="form-row">
          <n-form-item label="名称" :show-feedback="false" style="flex: 1">
            <n-input v-model:value="form.name" placeholder="如 SenseNova 池" />
          </n-form-item>
          <n-form-item label="虚拟模型" :show-feedback="false" style="flex: 1">
            <n-input
              v-model:value="form.virtual_model"
              :disabled="!!dialog.id"
              placeholder="客户端请求里的 model 名"
            />
          </n-form-item>
        </div>
        <div class="form-row">
          <n-form-item label="策略" :show-feedback="false" style="flex: 1">
            <n-select v-model:value="form.strategy" :options="strategyOptions" />
          </n-form-item>
          <n-form-item label="层内重试" :show-feedback="false" style="flex: 1">
            <n-input-number v-model:value="form.max_retries" :min="1" :max="10" style="width: 100%" />
          </n-form-item>
        </div>

        <n-form-item label="密钥池">
          <div class="tiers">
            <div v-for="(t, ti) in form.tiers" :key="ti" class="tier-box">
              <div class="tier-head">
                <n-tag size="small" :type="ti === 0 ? 'primary' : 'default'">
                  {{ ti === 0 ? '主池 T0' : `兜底 T${ti}` }}
                </n-tag>
                <n-select
                  v-model:value="t.provider_id"
                  :options="providerOptions"
                  placeholder="选择供应商"
                  size="small"
                  style="width: 200px"
                  @update:value="t.keys = []"
                />
                <div style="flex: 1" />
                <n-button
                  size="small"
                  quaternary
                  type="error"
                  :disabled="form.tiers.length <= 1"
                  @click="form.tiers.splice(ti, 1)"
                >
                  移除该层
                </n-button>
              </div>
              <div v-for="(k, ki) in t.keys" :key="ki" class="key-row">
                <n-select
                  v-model:value="k.key_id"
                  :options="keyOptionsOf(t.provider_id)"
                  placeholder="选择密钥"
                  size="small"
                  style="width: 260px"
                />
                <n-input-number v-model:value="k.weight" :min="1" :max="100" size="small" style="width: 90px" />
                <span class="hint">权重</span>
                <n-input-number v-model:value="k.priority" :min="0" :max="100" size="small" style="width: 90px" />
                <span class="hint">优先级</span>
                <n-button size="small" quaternary type="error" @click="t.keys.splice(ki, 1)">删</n-button>
              </div>
              <n-button
                size="small"
                quaternary
                type="primary"
                @click="t.keys.push({ key_id: undefined as unknown as number, weight: 1, priority: 0 })"
              >
                + 添加密钥
              </n-button>
            </div>
            <n-button
              size="small"
              @click="form.tiers.push({ provider_id: undefined as unknown as number, keys: [] })"
            >
              + 添加兜底层（跨供应商 fallback）
            </n-button>
          </div>
        </n-form-item>

        <n-form-item label="模型映射">
          <div class="tiers">
            <div v-for="(m, mi) in form.mappings" :key="mi" class="key-row">
              <n-select
                v-model:value="m.provider_id"
                :options="providerOptions"
                placeholder="供应商"
                size="small"
                style="width: 200px"
              />
              <span class="hint">→</span>
              <n-input v-model:value="m.native_model" placeholder="该供应商的原生模型名" size="small" style="width: 240px" />
              <n-button size="small" quaternary type="error" @click="form.mappings.splice(mi, 1)">删</n-button>
            </div>
            <n-button
              size="small"
              @click="form.mappings.push({ provider_id: undefined as unknown as number, native_model: '' })"
            >
              + 添加映射（虚拟模型在该供应商上的真实模型名）
            </n-button>
          </div>
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
import { computed, h, reactive, ref, onMounted } from 'vue'
import {
  NButton, NDataTable, NInput, NInputNumber, NSelect, NForm, NFormItem,
  NModal, NPopconfirm, NTag, useMessage, type DataTableColumns, type SelectOption,
} from 'naive-ui'
import { api, type Channel, type Provider, type ApiKey, type ChannelKeyItem } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'

const list = ref<Channel[]>([])
const providers = ref<Provider[]>([])
const allKeys = ref<ApiKey[]>([])
const loading = ref(false)
const message = useMessage()

const dialog = reactive<{ visible: boolean; id: number | null; saving: boolean }>({
  visible: false,
  id: null,
  saving: false,
})

interface FormKey { key_id: number; weight: number; priority: number }
interface FormTier { provider_id: number; keys: FormKey[] }
interface FormMapping { provider_id: number; native_model: string }

const form = reactive<{
  name: string
  virtual_model: string
  strategy: string
  max_retries: number
  tiers: FormTier[]
  mappings: FormMapping[]
}>({ name: '', virtual_model: '', strategy: 'round_robin', max_retries: 3, tiers: [], mappings: [] })

const strategyOptions: SelectOption[] = [
  { label: '轮询 round_robin', value: 'round_robin' },
  { label: '加权 weighted', value: 'weighted' },
  { label: '优先级 priority', value: 'priority' },
]

const providerOptions = computed<SelectOption[]>(() =>
  providers.value.map((p) => ({ label: p.name, value: p.id })),
)

function keyOptionsOf(providerId: number): SelectOption[] {
  return allKeys.value
    .filter((k) => k.provider_id === providerId)
    .map((k) => ({
      label: `${k.name}（${k.key_mask} · ${statusText(k.status)}）`,
      value: k.id,
    }))
}

const columns: DataTableColumns<Channel> = [
  { title: '名称', key: 'name', width: 140 },
  {
    title: '虚拟模型',
    key: 'virtual_model',
    minWidth: 130,
    render: (row) => h(NTag, { size: 'small' }, { default: () => row.virtual_model }),
  },
  {
    title: '路由计划',
    key: 'tiers',
    minWidth: 320,
    render: (row) =>
      h('div', null, [
        ...(row.tiers || []).map((t) =>
          h('div', { class: 'tier-line' }, [
            h(NTag, { size: 'small', type: t.order === 0 ? 'primary' : 'default', class: 'tier-tag' }, { default: () => `T${t.order}` }),
            h('span', null, t.provider_name),
            h('span', { class: 'keys' }, `（${formatKeys(t.keys)}）`),
          ]),
        ),
        !row.tiers?.length ? h('div', { class: 'keys' }, '未配置') : null,
      ]),
  },
  { title: '策略', key: 'strategy', width: 110 },
  { title: '重试', key: 'max_retries', width: 60 },
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
    width: 150,
    fixed: 'right',
    render: (row) =>
      h('div', { style: 'display:flex; gap:4px' }, [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => openEdit(row) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => onRemove(row) },
          {
            default: () => '删除该通道？',
            trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'error' }, { default: () => '删除' }),
          },
        ),
      ]),
  },
]

function formatKeys(keys: ChannelKeyItem[] | undefined): string {
  return (keys ?? []).map((k) => k.key_mask ?? k.key_id).join('、') || '无密钥'
}
function statusText(s: string) {
  return ({ active: '可用', rate_limited: '限流', exhausted: '耗尽', invalid: '失效', disabled: '停用' } as const)[s as 'active'] || s
}

async function load() {
  loading.value = true
  try {
    ;[list.value, providers.value, allKeys.value] = await Promise.all([
      api.channels.list(),
      api.providers.list(),
      api.keys.list(),
    ])
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}

function openAdd() {
  dialog.id = null
  Object.assign(form, {
    name: '',
    virtual_model: '',
    strategy: 'round_robin',
    max_retries: 3,
    tiers: [{ provider_id: providers.value[0]?.id as unknown as number, keys: [] }],
    mappings: [],
  })
  dialog.visible = true
}

function openEdit(row: Channel) {
  dialog.id = row.id
  Object.assign(form, {
    name: row.name,
    virtual_model: row.virtual_model,
    strategy: row.strategy,
    max_retries: row.max_retries,
    tiers: (row.tiers || []).map((t) => ({
      provider_id: t.provider_id,
      keys: (t.keys || []).map((k) => ({ key_id: k.key_id, weight: k.weight, priority: k.priority })),
    })),
    mappings: (row.mappings || []).map((m) => ({ provider_id: m.provider_id, native_model: m.native_model })),
  })
  dialog.visible = true
}

async function onSave() {
  if (!form.name || !form.virtual_model) {
    message.warning('名称与虚拟模型必填')
    return
  }
  const tiers = form.tiers
    .filter((t) => t.provider_id && t.keys.length && t.keys.every((k) => k.key_id))
  if (!tiers.length) {
    message.warning('至少配置一个层且层内要有密钥')
    return
  }
  const data = {
    name: form.name,
    virtual_model: form.virtual_model,
    strategy: form.strategy,
    max_retries: form.max_retries,
    is_active: true,
    tiers: tiers.map((t) => ({ provider_id: t.provider_id, keys: t.keys })),
    mappings: form.mappings.filter((m) => m.provider_id && m.native_model),
  }
  dialog.saving = true
  try {
    if (dialog.id) {
      await api.channels.update(dialog.id, data)
      message.success('已更新')
    } else {
      await api.channels.create(data)
      message.success('已创建')
    }
    dialog.visible = false
    await load()
  } catch {
    // 拦截器已提示
  } finally {
    dialog.saving = false
  }
}

async function onRemove(row: Channel) {
  try {
    await api.channels.remove(row.id)
    message.success('已删除')
    await load()
  } catch {
    // 拦截器已提示
  }
}

onMounted(load)
</script>

<style scoped>
.channels-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.form-row {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}
.tier-line {
  line-height: 22px;
}
:deep(.tier-tag) {
  margin-right: 6px;
}
.keys {
  color: var(--app-text-secondary, #64748b);
  font-size: 12px;
}
.tiers {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tier-box {
  border: 1px solid var(--app-border, #e5e7eb);
  border-radius: 6px;
  padding: 8px;
}
.tier-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.key-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.hint {
  color: var(--app-text-secondary, #64748b);
  font-size: 12px;
}
</style>
