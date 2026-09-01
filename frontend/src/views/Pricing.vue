<template>
  <div>
    <div class="toolbar">
      <span class="hint">价格按“每 1M tokens”填写，单位自定（CNY/USD）。网关按 native_model 查表计成本。</span>
      <div style="flex: 1" />
      <el-button type="success" @click="openAdd">新增模型价格</el-button>
    </div>

    <el-table v-loading="loading" :data="list" stripe>
      <el-table-column prop="model_name" label="模型" min-width="160" />
      <el-table-column label="输入价 / 1M" width="160">
        <template #default="{ row }">{{ row.input_price }} {{ row.currency }}</template>
      </el-table-column>
      <el-table-column label="输出价 / 1M" width="160">
        <template #default="{ row }">{{ row.output_price }} {{ row.currency }}</template>
      </el-table-column>
      <el-table-column prop="note" label="备注" min-width="120" />
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link size="small" @click="openEdit(row)">编辑</el-button>
          <el-popconfirm title="删除该价格？" @confirm="onRemove(row)">
            <template #reference>
              <el-button link size="small" type="danger">删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑价格' : '新增价格'" width="460px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="模型名"><el-input v-model="form.model_name" :disabled="!!dialog.id" placeholder="如 glm-5.2" /></el-form-item>
        <el-form-item label="输入价/1M"><el-input-number v-model="form.input_price" :min="0" :step="0.1" :precision="6" style="width: 100%" /></el-form-item>
        <el-form-item label="输出价/1M"><el-input-number v-model="form.output_price" :min="0" :step="0.1" :precision="6" style="width: 100%" /></el-form-item>
        <el-form-item label="币种">
          <el-select v-model="form.currency" style="width: 100%">
            <el-option label="CNY 人民币" value="CNY" />
            <el-option label="USD 美元" value="USD" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.note" /></el-form-item>
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
import { api, type Pricing } from '@/api'

const list = ref<Pricing[]>([])
const loading = ref(false)
const dialog = reactive<{ visible: boolean; id: number | null; saving: boolean }>({ visible: false, id: null, saving: false })
const form = reactive({ model_name: '', input_price: 0, output_price: 0, currency: 'CNY', note: '' })

async function load() {
  loading.value = true
  try {
    list.value = await api.pricing.list()
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}
function openAdd() {
  dialog.id = null
  Object.assign(form, { model_name: '', input_price: 0, output_price: 0, currency: 'CNY', note: '' })
  dialog.visible = true
}
function openEdit(row: Pricing) {
  dialog.id = row.id
  Object.assign(form, { model_name: row.model_name, input_price: row.input_price, output_price: row.output_price, currency: row.currency, note: row.note || '' })
  dialog.visible = true
}
async function onSave() {
  if (!form.model_name) return ElMessage.warning('模型名必填')
  dialog.saving = true
  try {
    if (dialog.id) {
      await api.pricing.update(dialog.id, { input_price: form.input_price, output_price: form.output_price, currency: form.currency, note: form.note })
      ElMessage.success('已更新')
    } else {
      await api.pricing.create({ ...form })
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
async function onRemove(row: Pricing) {
  try {
    await api.pricing.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    // 拦截器已提示
  }
}
onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; align-items: center; margin-bottom: 16px; }
.hint { color: #909399; font-size: 12px; }
</style>
