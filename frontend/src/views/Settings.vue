<template>
  <div class="settings-page">
    <PageHeader title="系统设置" subtitle="账户 · 网关访问 · 系统能力">
      <template #actions>
        <n-tag v-if="pwdChanged" type="success" size="small" round>密码已更新</n-tag>
      </template>
    </PageHeader>

    <DataCard>
      <n-tabs v-model:value="activeTab" type="line">
        <!-- Tab 1：账户 -->
        <n-tab-pane name="account" tab="账户">
          <div class="tab-content">
            <h3 class="tab-title">修改主密码</h3>
            <p class="tab-desc">主密码用于登录管理台。修改后立即生效，下次登录请使用新密码。</p>
            <n-form
              :model="pwdForm"
              label-placement="left"
              label-width="100"
              style="max-width: 480px; margin-top: 16px"
              @submit.prevent="onChangePwd"
            >
              <n-form-item label="旧密码">
                <n-input
                  v-model:value="pwdForm.old_password"
                  type="password"
                  show-password-on="click"
                  placeholder="当前主密码"
                />
              </n-form-item>
              <n-form-item label="新密码">
                <n-input
                  v-model:value="pwdForm.new_password"
                  type="password"
                  show-password-on="click"
                  placeholder="至少 8 位"
                />
              </n-form-item>
              <n-form-item label="确认新密码">
                <n-input
                  v-model:value="pwdForm.confirm_password"
                  type="password"
                  show-password-on="click"
                  placeholder="重复新密码"
                  @keyup.enter="onChangePwd"
                />
              </n-form-item>
              <n-form-item :show-label="false">
                <n-button type="primary" :loading="pwdLoading" attr-type="submit" @click="onChangePwd">
                  确认修改
                </n-button>
              </n-form-item>
            </n-form>
          </div>
        </n-tab-pane>

        <!-- Tab 2：Gateway -->
        <n-tab-pane name="gateway" tab="Gateway 访问">
          <div class="tab-content">
            <h3 class="tab-title">网关统一 Token</h3>
            <p class="tab-desc">
              此 Token 用于外部客户端通过 <code>/v1/*</code> 访问转发网关。与管理台登录密码独立。
            </p>
            <div class="gw-info">
              <div class="gw-row">
                <span class="gw-label">请求地址</span>
                <n-input :value="gwBaseUrl" readonly style="flex: 1" />
              </div>
              <div class="gw-row">
                <span class="gw-label">鉴权方式</span>
                <n-tag type="info" size="small">Authorization: Bearer {{ authHeaderTemplate }}</n-tag>
              </div>
              <div class="gw-row">
                <span class="gw-label">当前 Token</span>
                <n-input
                  :value="gatewayTokenDisplay"
                  type="password"
                  show-password-on="click"
                  readonly
                  style="width: 480px"
                />
              </div>
            </div>
            <p class="tab-desc warn">
              提示：GATEWAY_TOKEN 在 <code>.env</code> 中配置。管理台 v2.1 将支持在线轮换（当前修改需重启服务）。
            </p>
          </div>
        </n-tab-pane>

        <!-- Tab 3：系统 -->
        <n-tab-pane name="system" tab="系统">
          <div class="tab-content">
            <EmptyState
              title="v2.1 计划中"
              description="多用户管理 · 操作审计 · 通知渠道（邮件/飞书/企业微信）· 备份自动同步等能力将在 v2.1 推出。"
            />
          </div>
        </n-tab-pane>
      </n-tabs>
    </DataCard>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, computed } from 'vue'
import { useMessage } from 'naive-ui'
import { NTabs, NTabPane, NForm, NFormItem, NInput, NButton, NTag } from 'naive-ui'
import { api } from '@/api'
import PageHeader from '@/design/components/PageHeader.vue'
import DataCard from '@/design/components/DataCard.vue'
import EmptyState from '@/design/components/EmptyState.vue'

const message = useMessage()

const activeTab = ref('account')
const pwdLoading = ref(false)
const pwdChanged = ref(false)

const pwdForm = reactive({
  old_password: '',
  new_password: '',
  confirm_password: '',
})

async function onChangePwd() {
  if (!pwdForm.old_password) return message.warning('请输入旧密码')
  if (pwdForm.new_password.length < 8) return message.warning('新密码至少 8 位')
  if (pwdForm.new_password !== pwdForm.confirm_password) return message.warning('两次新密码不一致')
  pwdLoading.value = true
  try {
    const resp = await api.auth.changePassword(pwdForm.old_password, pwdForm.new_password)
    message.success(resp.message || '密码已更新')
    pwdChanged.value = true
    pwdForm.old_password = ''
    pwdForm.new_password = ''
    pwdForm.confirm_password = ''
  } catch {
    // 拦截器已提示
  } finally {
    pwdLoading.value = false
  }
}

// —— Gateway 信息（URL 推导 + API 取 token）——
const gwBaseUrl = computed(() => {
  const { protocol, hostname } = window.location
  const port = window.location.port ? `:${window.location.port}` : ''
  return `${protocol}//${hostname}${port}/v1/`
})
const authHeaderTemplate = '<GATEWAY_TOKEN>'

const gatewayToken = ref('')
const gatewayTokenDisplay = computed(() => {
  if (!gatewayToken.value) return '加载中…'
  if (gatewayToken.value.length <= 14) return gatewayToken.value
  return gatewayToken.value.slice(0, 10) + '…' + gatewayToken.value.slice(-6)
})

onMounted(async () => {
  try {
    const resp = await api.settings.get()
    if (resp?.gateway_token) gatewayToken.value = resp.gateway_token
  } catch {
    // 端点失败静默（掩码占位）
  }
})
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.tab-content {
  padding: 4px 4px 12px;
}
.tab-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text-primary, #0f172a);
}
.tab-desc {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--app-text-secondary, #64748b);
  line-height: 1.6;
}
.tab-desc code {
  background: var(--app-code-bg, #f1f5f9);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
}
.tab-desc.warn {
  margin-top: 16px;
  padding: 10px 12px;
  background: #fef3c7;
  color: #92400e;
  border-radius: 6px;
}
.gw-info {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
}
.gw-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.gw-label {
  width: 96px;
  font-size: 13px;
  color: var(--app-text-secondary, #64748b);
  flex-shrink: 0;
}
</style>
