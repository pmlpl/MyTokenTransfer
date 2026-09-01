<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2>MyTokenTransfer</h2>
      <p class="sub">API 密钥管理台 · 本地登录</p>
      <el-form :model="form" @submit.prevent="onLogin">
        <el-form-item>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="主密码"
            show-password
            size="large"
            @keyup.enter="onLogin"
          />
        </el-form-item>
        <el-button type="primary" :loading="loading" size="large" style="width: 100%" @click="onLogin">
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { useAuthStore } from '@/stores/auth'

const form = reactive({ password: '' })
const loading = ref(false)
const router = useRouter()
const auth = useAuthStore()

async function onLogin() {
  if (!form.password) {
    ElMessage.warning('请输入主密码')
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
  background: #f0f2f5;
}
.login-card {
  width: 380px;
}
.login-card h2 {
  text-align: center;
  margin: 0 0 4px;
}
.sub {
  text-align: center;
  color: #909399;
  margin: 0 0 24px;
  font-size: 13px;
}
</style>
