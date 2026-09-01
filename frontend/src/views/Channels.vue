<template>
  <div>
    <div class="toolbar">
      <div style="flex: 1" />
      <el-button type="success" @click="openAdd">新增通道</el-button>
    </div>

    <el-table v-loading="loading" :data="list" stripe>
      <el-table-column prop="name" label="名称" width="140" />
      <el-table-column prop="virtual_model" label="虚拟模型" min-width="130">
        <template #default="{ row }"><el-tag>{{ row.virtual_model }}</el-tag></template>
      </el-table-column>
      <el-table-column label="路由计划" min-width="320">
        <template #default="{ row }">
          <div v-for="t in row.tiers" :key="t.id ?? t.order" class="tier-line">
            <el-tag size="small" :type="t.order === 0 ? 'primary' : 'info'" class="tier-tag">T{{ t.order }}</el-tag>
            {{ t.provider_name }}
            <span class="keys">（{{ formatKeys(t.keys) }}）</span>
          </div>
          <div v-if="!row.tiers?.length" class="keys">未配置</div>
        </template>
      </el-table-column>
      <el-table-column prop="strategy" label="策略" width="110" />
      <el-table-column prop="max_retries" label="重试" width="60" />
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '是' : '否' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link size="small" @click="openEdit(row)">编辑</el-button>
          <el-popconfirm title="删除该通道？" @confirm="onRemove(row)">
            <template #reference>
              <el-button link size="small" type="danger">删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑通道' : '新增通道'" width="760px" top="4vh">
      <el-form :model="form" label-width="90px">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="名称"><el-input v-model="form.name" placeholder="如 SenseNova 池" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="虚拟模型">
              <el-input v-model="form.virtual_model" :disabled="!!dialog.id" placeholder="客户端请求里的 model 名" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="策略">
              <el-select v-model="form.strategy" style="width: 100%">
                <el-option label="轮询 round_robin" value="round_robin" />
                <el-option label="加权 weighted" value="weighted" />
                <el-option label="优先级 priority" value="priority" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="层内重试">
              <el-input-number v-model="form.max_retries" :min="1" :max="10" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="密钥池">
          <div class="tiers">
            <div v-for="(t, ti) in form.tiers" :key="ti" class="tier-box">
              <div class="tier-head">
                <el-tag :type="ti === 0 ? 'primary' : 'info'">{{ ti === 0 ? '主池 T0' : `兜底 T${ti}` }}</el-tag>
                <el-select v-model="t.provider_id" placeholder="选择供应商" size="small" style="width: 200px" @change="t.keys = []">
                  <el-option v-for="p in providers" :key="p.id" :label="p.name" :value="p.id" />
                </el-select>
                <div style="flex: 1" />
                <el-button size="small" text type="danger" :disabled="form.tiers.length <= 1" @click="form.tiers.splice(ti, 1)">
                  移除该层
                </el-button>
              </div>
              <div v-for="(k, ki) in t.keys" :key="ki" class="key-row">
                <el-select v-model="k.key_id" placeholder="选择密钥" size="small" style="width: 260px">
                  <el-option
                    v-for="key in keysOf(t.provider_id)"
                    :key="key.id"
                    :label="`${key.name}（${key.key_mask} · ${statusText(key.status)}）`"
                    :value="key.id"
                  />
                </el-select>
                <el-input-number v-model="k.weight" :min="1" :max="100" size="small" controls-position="right" style="width: 90px" />
                <span class="hint">权重</span>
                <el-input-number v-model="k.priority" :min="0" :max="100" size="small" controls-position="right" style="width: 90px" />
                <span class="hint">优先级</span>
                <el-button size="small" text type="danger" @click="t.keys.splice(ki, 1)">删</el-button>
              </div>
              <el-button size="small" text type="primary" @click="t.keys.push({ key_id: undefined as unknown as number, weight: 1, priority: 0 })">
                + 添加密钥
              </el-button>
            </div>
            <el-button size="small" @click="form.tiers.push({ provider_id: undefined as unknown as number, keys: [] })">
              + 添加兜底层（跨供应商 fallback）
            </el-button>
          </div>
        </el-form-item>

        <el-form-item label="模型映射">
          <div class="tiers">
            <div v-for="(m, mi) in form.mappings" :key="mi" class="key-row">
              <el-select v-model="m.provider_id" placeholder="供应商" size="small" style="width: 200px">
                <el-option v-for="p in providers" :key="p.id" :label="p.name" :value="p.id" />
              </el-select>
              <span class="hint">→</span>
              <el-input v-model="m.native_model" placeholder="该供应商的原生模型名" size="small" style="width: 240px" />
              <el-button size="small" text type="danger" @click="form.mappings.splice(mi, 1)">删</el-button>
            </div>
            <el-button size="small" @click="form.mappings.push({ provider_id: undefined as unknown as number, native_model: '' })">
              + 添加映射（虚拟模型在该供应商上的真实模型名）
            </el-button>
          </div>
        </el-form-item>
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
import { api, type Channel, type Provider, type ApiKey, type ChannelKeyItem } from '@/api'

const list = ref<Channel[]>([])
const providers = ref<Provider[]>([])
const allKeys = ref<ApiKey[]>([])
const loading = ref(false)
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

function keysOf(providerId: number) {
  return allKeys.value.filter((k: ApiKey) => k.provider_id === providerId)
}
function formatKeys(keys: ChannelKeyItem[] | undefined): string {
  return (keys ?? []).map((k: ChannelKeyItem) => k.key_mask ?? k.key_id).join('、') || '无密钥'
}
function statusText(s: string) {
  return { active: '可用', rate_limited: '限流', exhausted: '耗尽', invalid: '失效', disabled: '停用' }[s] || s
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
    ElMessage.warning('名称与虚拟模型必填')
    return
  }
  const tiers = form.tiers
    .filter((t) => t.provider_id && t.keys.length && t.keys.every((k) => k.key_id))
  if (!tiers.length) {
    ElMessage.warning('至少配置一个层且层内要有密钥')
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
      ElMessage.success('已更新')
    } else {
      await api.channels.create(data)
      ElMessage.success('已创建')
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
    ElMessage.success('已删除')
    await load()
  } catch {
    // 拦截器已提示
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  margin-bottom: 16px;
}
.tier-line {
  line-height: 22px;
}
.tier-tag {
  margin-right: 6px;
}
.keys {
  color: #909399;
  font-size: 12px;
}
.tiers {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tier-box {
  border: 1px solid #e4e7ed;
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
  color: #909399;
  font-size: 12px;
}
</style>
