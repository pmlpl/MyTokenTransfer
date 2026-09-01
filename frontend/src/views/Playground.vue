<template>
  <div>
    <el-card>
      <template #header>网关接入信息（给任意 OpenAI 兼容客户端用）</template>
      <div class="info-line"><span class="k">Base URL</span><code>{{ baseUrl }}/v1</code></div>
      <div class="info-line">
        <span class="k">API Key</span>
        <code>{{ gwToken || '加载中…' }}</code>
        <el-button size="small" text type="primary" @click="copy(gwToken)">复制</el-button>
      </div>
      <div class="info-line"><span class="k">鉴权方式</span><code>Authorization: Bearer &lt;API Key&gt;</code></div>
      <div class="info-line">
        <span class="k">可用模型</span>
        <el-tag v-for="m in models" :key="m" size="small" class="model-tag">{{ m }}</el-tag>
        <span v-if="!models.length" class="hint">（先到"转发通道"页创建通道）</span>
      </div>
    </el-card>

    <el-card style="margin-top: 16px">
      <template #header>Playground · 端到端调试</template>
      <el-form inline>
        <el-form-item label="模型">
          <el-select v-model="selModel" placeholder="选择虚拟模型" style="width: 220px">
            <el-option v-for="m in models" :key="m" :label="m" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="温度">
          <el-input-number v-model="temperature" :min="0" :max="2" :step="0.1" />
        </el-form-item>
      </el-form>
      <el-input
        v-model="message"
        type="textarea"
        :rows="3"
        placeholder="输入消息，如：用一句话介绍你自己"
        style="margin-bottom: 12px"
      />
      <el-button type="primary" :loading="sending" @click="send">发送（经统一网关）</el-button>

      <div v-if="reply" class="reply">
        <div class="reply-head">
          <span>回复</span>
          <span class="meta" v-if="usage">{{ usage.prompt_tokens }} + {{ usage.completion_tokens }} tokens · {{ latency }}ms</span>
        </div>
        <div class="reply-body">{{ reply }}</div>
        <div class="meta" v-if="lastLog">
          本次由 key #{{ lastLog.key_id }}（T{{ lastLog.tier_order }} 层）服务 ·
          <el-tag size="small" type="success">查看"调用日志"页可见完整记录</el-tag>
        </div>
      </div>
      <el-alert v-if="err" type="error" :title="err" style="margin-top: 12px" :closable="false" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type CallLogItem } from '@/api'

const baseUrl = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8082'
const gwToken = ref('')
const models = ref<string[]>([])
const selModel = ref('')
const temperature = ref(0.7)
const message = ref('')
const sending = ref(false)
const reply = ref('')
const usage = ref<{ prompt_tokens: number; completion_tokens: number } | null>(null)
const latency = ref(0)
const lastLog = ref<CallLogItem | null>(null)
const err = ref('')

async function loadModels() {
  try {
    const r = await api.gateway.models(gwToken.value)
    models.value = (r?.data || []).map((x: { id: string }) => x.id)
    if (models.value.length && !selModel.value) selModel.value = models.value[0]
  } catch {
    // 拦截器已提示
  }
}

async function send() {
  if (!selModel.value) return ElMessage.warning('请选择模型')
  if (!message.value.trim()) return ElMessage.warning('请输入消息')
  sending.value = true
  reply.value = ''
  err.value = ''
  usage.value = null
  lastLog.value = null
  const t0 = performance.now()
  try {
    const r = await api.gateway.chat(
      {
        model: selModel.value,
        messages: [{ role: 'user', content: message.value }],
        temperature: temperature.value,
        stream: false,
      },
      gwToken.value,
    )
    latency.value = Math.round(performance.now() - t0)
    reply.value = r?.choices?.[0]?.message?.content ?? JSON.stringify(r)
    usage.value = r?.usage ?? null
    const logs = await api.logs.list({ limit: 1 })
    lastLog.value = logs.items[0] ?? null
  } catch (e: unknown) {
    const resp = (e as { response?: { data?: { error?: { message?: string } } } })?.response?.data
    err.value = resp?.error?.message || '请求失败（详见后端日志）'
  } finally {
    sending.value = false
  }
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

onMounted(async () => {
  const s = await api.settings.get()
  gwToken.value = s.gateway_token
  await loadModels()
})
</script>

<style scoped>
.info-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.info-line .k {
  color: #909399;
  width: 70px;
  flex-shrink: 0;
}
.info-line code {
  background: #f5f7fa;
  padding: 2px 8px;
  border-radius: 4px;
  word-break: break-all;
}
.model-tag {
  margin-right: 6px;
}
.hint {
  color: #909399;
  font-size: 12px;
}
.reply {
  margin-top: 16px;
  border-top: 1px solid #eee;
  padding-top: 12px;
}
.reply-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-weight: 600;
}
.reply-body {
  white-space: pre-wrap;
  background: #f5f7fa;
  padding: 12px;
  border-radius: 6px;
  line-height: 1.6;
}
.meta {
  color: #909399;
  font-size: 12px;
  margin-top: 8px;
}
</style>
