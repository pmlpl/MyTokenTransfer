<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="brand">
        <div class="brand-mark">MT</div>
        <h2 class="brand-title">MyTokenTransfer</h2>
        <p class="brand-sub">API 密钥管理台 · 本地登录</p>
      </div>
      <n-form @submit.prevent="onLogin">
        <n-form-item :show-label="false">
          <n-input
            v-model:value="form.password"
            type="password"
            placeholder="主密码"
            show-password-on="click"
            size="large"
            @keyup.enter="onLogin"
          />
        </n-form-item>
        <n-button
          type="primary"
          size="large"
          block
          :loading="loading"
          attr-type="submit"
          @click="onLogin"
        >
          登录
        </n-button>
      </n-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NForm, NFormItem, NInput, NButton, useMessage } from 'naive-ui'
import { api } from '@/api'
import { useAuthStore } from '@/stores/auth'

const form = reactive({ password: '' })
const loading = ref(false)
const router = useRouter()
const auth = useAuthStore()
const message = useMessage()

async function onLogin() {
  if (!form.password) {
    message.warning('请输入主密码')
    return
  }
  loading.value = true
  try {
    const { token } = await api.auth.login(form.password)
    auth.setToken(token)
    router.push('/')
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background:
    radial-gradient(circle at 20% 20%, rgba(79, 124, 255, 0.08), transparent 40%),
    radial-gradient(circle at 80% 80%, rgba(99, 102, 241, 0.06), transparent 40%),
    #f7f8fa;
}
.login-card {
  width: 400px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 32px rgba(15, 23, 42, 0.08);
  padding: 40px 36px 32px;
}
.brand {
  text-align: center;
  margin-bottom: 28px;
}
.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  background: #4f7cff;
  border-radius: 12px;
  color: #fff;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
}
.brand-title {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
  color: #0f172a;
}
.brand-sub {
  margin: 0;
  font-size: 13px;
  color: #64748b;
}
</style>
