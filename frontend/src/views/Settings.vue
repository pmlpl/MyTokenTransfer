<template>
  <div class="settings-page">
    <PageHeader title="系统设置" />

    <DataCard>
      <n-tabs v-model:value="activeTab" type="line">
        <!-- Tab 1：账户 -->
        <n-tab-pane name="account" tab="账户">
          <div class="tab-content">
            <n-form
              :model="pwdForm"
              label-placement="left"
              label-width="100"
              style="max-width: 480px"
              @submit.prevent="onChangePwd"
            >
              <n-form-item label="旧密码">
                <n-input
                  v-model:value="pwdForm.old_password"
                  type="password"
                  show-password-on="click"
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
        <n-tab-pane name="gateway" tab="Gateway">
          <div class="tab-content">
            <div class="gw-info">
              <div class="gw-row">
                <span class="gw-label">请求地址</span>
                <n-input :value="gwBaseUrl" readonly style="flex: 1" />
              </div>
              <div class="gw-row">
                <span class="gw-label">鉴权</span>
                <n-tag type="info" size="small">Authorization: Bearer {{ authHeaderTemplate }}</n-tag>
              </div>
              <div class="gw-row">
                <span class="gw-label">Token</span>
                <n-input
                  :value="gatewayTokenDisplay"
                  type="password"
                  show-password-on="click"
                  readonly
                  style="width: 480px"
                />
                <n-tag size="small" :bordered="false">改 .env 后重启生效</n-tag>
              </div>
            </div>
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

const message = useMessage()

const activeTab = ref('account')
const pwdLoading = ref(false)

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
.gw-info {
  display: flex;
  flex-direction: column;
  gap: 12px;
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
