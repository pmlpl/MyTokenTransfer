<template>
  <div class="playground-page">
    <PageHeader title="Playground" subtitle="网关接入信息 · 端到端调试" />

    <DataCard title="网关接入信息" subtitle="给任意 OpenAI 兼容客户端用">
      <div class="info-line">
        <span class="k">固定地址</span>
        <code class="url-code">{{ fixedUrl }}</code>
        <n-button size="small" quaternary @click="copy(fixedUrl)">
          <template #icon><n-icon :component="CopyOutline" /></template>
        </n-button>
        <span class="hint">跨网络可用，其他设备接入 Tailscale 后使用（推荐）</span>
      </div>
      <div class="info-line">
        <span class="k">本机地址</span>
        <code class="url-code">{{ baseUrl }}/v1</code>
        <n-button size="small" quaternary @click="copy(`${baseUrl}/v1`)">
          <template #icon><n-icon :component="CopyOutline" /></template>
        </n-button>
        <span class="hint">仅当前网络入口，地址会随网络变化</span>
      </div>
      <div class="info-line">
        <span class="k">API Key</span>
        <code>{{ gwToken || '加载中…' }}</code>
        <n-button size="small" quaternary @click="copy(gwToken)">
          <template #icon><n-icon :component="CopyOutline" /></template>
        </n-button>
      </div>
      <div class="info-line">
        <span class="k">鉴权方式</span>
        <code>Authorization: Bearer &lt;API Key&gt;</code>
      </div>
      <div class="info-line">
        <span class="k">可用模型</span>
        <n-select
          v-model:value="selModel"
          :options="modelOptions"
          placeholder="选择模型"
          style="width: 280px"
          @update:value="onModelSelect"
        />
        <n-button size="small" quaternary @click="copy(selModel)" :disabled="!selModel">
          <template #icon><n-icon :component="CopyOutline" /></template>
        </n-button>
        <span v-if="!models.length" class="hint">（先到"转发通道"页创建通道）</span>
      </div>
    </DataCard>

    <DataCard title="Playground · 端到端调试" subtitle="经统一网关调用，计入日志与用量">
      <n-form inline label-placement="left" :show-feedback="false">
        <n-form-item label="模型">
          <n-select
            v-model:value="selModel"
            :options="modelOptions"
            placeholder="选择虚拟模型"
            style="width: 220px"
          />
        </n-form-item>
        <n-form-item label="温度" style="margin-left: 16px">
          <n-input-number v-model:value="temperature" :min="0" :max="2" :step="0.1" />
        </n-form-item>
      </n-form>
      <n-input
        v-model:value="inputMessage"
        type="textarea"
        :rows="3"
        placeholder="输入消息，如：用一句话介绍你自己"
        style="margin-top: 12px"
      />
      <div style="margin-top: 12px">
        <n-button type="primary" :loading="sending" @click="send">发送（经统一网关）</n-button>
      </div>

      <div v-if="reply" class="reply">
        <div class="reply-head">
          <span>回复</span>
          <span class="meta" v-if="usage">
            {{ usage.prompt_tokens }} + {{ usage.completion_tokens }} tokens · {{ latency }}ms
          </span>
        </div>
        <div class="reply-body">{{ reply }}</div>
        <div class="meta" v-if="lastLog">
          本次由 key #{{ lastLog.key_id }}（T{{ lastLog.tier_order }} 层）服务 ·
          <n-tag size="small" type="success">查看"调用日志"页可见完整记录</n-tag>
        </div>
      </div>
      <n-alert v-if="err" type="error" :show-icon="true" style="margin-top: 12px">
        {{ err }}
      </n-alert>
    </DataCard>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import {
  NButton, NInput, NInputNumber, NSelect, NForm, NFormItem,
  NTag, NAlert, NIcon, useMessage, type SelectOption,
} from 'naive-ui'
import { CopyOutline } from '@vicons/ionicons5'
import { api, type CallLogItem } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'

// 固定接入地址：Tailscale 虚拟网络 IP，不随物理网络（WiFi/热点）变化
const fixedUrl = 'http://100.83.19.7:8085/v1'

// 当前网络入口：跟随浏览器访问来源（dev=vite 5175，prod=nginx 8085）
const baseUrl = window.location.origin
const gwToken = ref('')
const models = ref<string[]>([])
const selModel = ref('')
const temperature = ref(0.7)
const inputMessage = ref('')
const sending = ref(false)
const reply = ref('')
const usage = ref<{ prompt_tokens: number; completion_tokens: number } | null>(null)
const latency = ref(0)
const lastLog = ref<CallLogItem | null>(null)
const err = ref('')
const message = useMessage()

const modelOptions = computed<SelectOption[]>(() =>
  models.value.map((m) => ({ label: m, value: m })),
)

function onModelSelect(val: string) {
  selModel.value = val
}

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
  if (!selModel.value) {
    message.warning('请选择模型')
    return
  }
  if (!inputMessage.value.trim()) {
    message.warning('请输入消息')
    return
  }
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
        messages: [{ role: 'user', content: inputMessage.value }],
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
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    message.success('已复制')
  } catch {
    message.error('复制失败')
  }
}

onMounted(async () => {
  const s = await api.settings.get()
  gwToken.value = s.gateway_token
  await loadModels()
})
</script>

<style scoped>
.playground-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.info-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.info-line .k {
  color: var(--app-text-secondary, #64748b);
  width: 70px;
  flex-shrink: 0;
}
.info-line code {
  background: var(--app-hover-bg, #f5f7fa);
  padding: 2px 8px;
  border-radius: 4px;
  word-break: break-all;
  font-family: 'FiraCode', monospace;
}
.info-line .url-code {
  flex: 1;
  min-width: 120px;
  word-break: break-all;
  white-space: normal;
}
.hint {
  color: var(--app-text-secondary, #64748b);
  font-size: 12px;
}
.reply {
  margin-top: 16px;
  border-top: 1px solid var(--app-border, #eee);
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
  background: var(--app-hover-bg, #f5f7fa);
  padding: 12px;
  border-radius: 6px;
  line-height: 1.6;
  font-family: 'FiraCode', monospace;
}
.meta {
  color: var(--app-text-secondary, #64748b);
  font-size: 12px;
  margin-top: 8px;
}
</style>
