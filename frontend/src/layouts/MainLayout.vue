<template>
  <el-container style="min-height: 100vh">
    <el-aside width="210px" class="aside">
      <div class="logo">MyTokenTransfer</div>
      <el-menu
        :default-active="route.path"
        router
        background-color="#001529"
        text-color="#bfcbd9"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/dashboard"><el-icon><Odometer /></el-icon><span>仪表盘</span></el-menu-item>
        <el-menu-item index="/keys"><el-icon><Key /></el-icon><span>密钥管理</span></el-menu-item>
        <el-menu-item index="/providers"><el-icon><Connection /></el-icon><span>供应商</span></el-menu-item>
        <el-menu-item index="/channels"><el-icon><Share /></el-icon><span>转发通道</span></el-menu-item>
        <el-menu-item index="/playground"><el-icon><Promotion /></el-icon><span>Playground</span></el-menu-item>
        <el-menu-item index="/logs"><el-icon><Document /></el-icon><span>调用日志</span></el-menu-item>
        <el-menu-item index="/usage"><el-icon><TrendCharts /></el-icon><span>用量统计</span></el-menu-item>
        <el-menu-item index="/pricing"><el-icon><Money /></el-icon><span>模型价格</span></el-menu-item>
        <el-menu-item index="/alerts">
          <el-icon><Bell /></el-icon>
          <span>告警事件</span>
          <el-badge v-if="unresolved > 0" :value="unresolved" :max="99" class="alert-badge" />
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="title">API 密钥管理台</span>
        <el-button text @click="onLogout">退出登录</el-button>
      </el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { api } from '@/api'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const unresolved = ref(0)
let timer: number | undefined

async function refreshBadge() {
  try {
    const resp = await api.alerts.list({ unresolved: true, limit: 1 })
    unresolved.value = resp.unresolved
  } catch {
    // 静默：拦截器已提示
  }
}

function onLogout() {
  auth.logout()
  router.push('/login')
}

onMounted(() => {
  refreshBadge()
  timer = window.setInterval(refreshBadge, 30000)
})
onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<style scoped>
.aside {
  background: #001529;
}
.logo {
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  text-align: center;
  padding: 18px 0;
  letter-spacing: 1px;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #eee;
}
.title {
  font-weight: 600;
}
.alert-badge {
  margin-left: 6px;
  margin-top: -2px;
}
</style>
