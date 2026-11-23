<template>
  <n-layout has-sider class="main-layout">
    <!-- 深色侧栏（slate-900，固定不受主题影响） -->
    <n-layout-sider
      bordered
      :width="220"
      :native-scrollbar="false"
      content-class="sidebar-scroll"
      class="sider"
    >
      <div class="logo">
        <img class="logo-mark" src="/logo.png" alt="MTT" />
        <span class="logo-text">MyTokenTransfer</span>
      </div>
      <n-menu
        class="sidebar-menu"
        :value="activeKey"
        :options="menuOptions"
        :inverted="true"
        :indent="18"
        @update:value="onMenuSelect"
      />
      <!-- 全局操作收进侧栏底部：页面标题因此只需出现一次 -->
      <div class="sidebar-foot">
        <n-badge :value="unresolved" :max="99" :show="unresolved > 0">
          <n-tooltip trigger="hover" placement="right">
            <template #trigger>
              <n-button quaternary circle aria-label="告警事件" @click="goAlerts">
                <template #icon><n-icon :component="NotificationsIcon" /></template>
              </n-button>
            </template>
            告警事件
          </n-tooltip>
        </n-badge>
        <n-tooltip trigger="hover" placement="right">
          <template #trigger>
            <n-button quaternary circle aria-label="退出登录" @click="onLogout">
              <template #icon><n-icon :component="LogOutIcon" /></template>
            </n-button>
          </template>
          退出登录
        </n-tooltip>
      </div>
    </n-layout-sider>

    <n-layout>
      <n-layout-content class="content" :native-scrollbar="false">
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NLayout,
  NLayoutSider,
  NLayoutContent,
  NMenu,
  NBadge,
  NButton,
  NIcon,
  NTooltip,
  type MenuOption,
} from 'naive-ui'
import {
  GridOutline,
  KeyOutline,
  ServerOutline,
  GitNetworkOutline,
  TerminalOutline,
  DocumentTextOutline,
  BarChartOutline,
  PricetagsOutline,
  NotificationsOutline,
  SettingsOutline,
  LogOutOutline,
} from '@vicons/ionicons5'
import { useAuthStore } from '@/stores/auth'
import { api } from '@/api'
import { tokens } from '@/design/tokens'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const unresolved = ref(0)
let timer: number | undefined

// 菜单项（plan C 节新 IA 分组：概览/密钥/转发/运营；Settings 子路由 Phase 1 加）
const menuOptions: MenuOption[] = [
  { label: '仪表盘', key: '/dashboard', icon: () => h(NIcon, null, { default: () => h(GridOutline) }) },
  { label: '密钥管理', key: '/keys', icon: () => h(NIcon, null, { default: () => h(KeyOutline) }) },
  { label: '供应商', key: '/providers', icon: () => h(NIcon, null, { default: () => h(ServerOutline) }) },
  { label: '转发通道', key: '/channels', icon: () => h(NIcon, null, { default: () => h(GitNetworkOutline) }) },
  { label: 'Playground', key: '/playground', icon: () => h(NIcon, null, { default: () => h(TerminalOutline) }) },
  { label: '调用日志', key: '/logs', icon: () => h(NIcon, null, { default: () => h(DocumentTextOutline) }) },
  { label: '用量统计', key: '/usage', icon: () => h(NIcon, null, { default: () => h(BarChartOutline) }) },
  { label: '模型价格', key: '/pricing', icon: () => h(NIcon, null, { default: () => h(PricetagsOutline) }) },
  { label: '系统设置', key: '/settings', icon: () => h(NIcon, null, { default: () => h(SettingsOutline) }) },
]

const NotificationsIcon = NotificationsOutline
const LogOutIcon = LogOutOutline

const activeKey = computed(() => route.path)

function onMenuSelect(key: string) {
  router.push(key)
}
function goAlerts() {
  router.push('/alerts')
}
function onLogout() {
  auth.logout()
  router.push('/login')
}

async function refreshBadge() {
  try {
    const resp = await api.alerts.list({ unresolved: true, limit: 1 })
    unresolved.value = resp.unresolved
  } catch {
    // 静默：拦截器已提示
  }
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
.main-layout {
  height: 100vh;
}
.sider {
  background: v-bind('tokens.light.sidebar') !important;
  color: v-bind('tokens.light.sidebarText');
}
:deep(.sidebar-scroll) {
  background: v-bind('tokens.light.sidebar');
  display: flex;
  flex-direction: column;
  height: 100%;
}
.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 18px 20px;
  color: #fff;
  flex-shrink: 0;
}
.logo-mark {
  display: block;
  width: 30px;
  height: 30px;
  padding: 3px;
  background: #ffffff;
  border-radius: 8px;
  object-fit: contain;
  box-sizing: border-box;
  flex-shrink: 0;
}
.logo-text {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.sidebar-menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.sidebar-foot {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 10px 14px;
  border-top: 1px solid rgba(148, 163, 184, 0.18);
  flex-shrink: 0;
}
.sidebar-foot :deep(.n-button) {
  color: #94a3b8;
}
.sidebar-foot :deep(.n-button:hover) {
  color: #fff;
  background: rgba(255, 255, 255, 0.08);
}
.content {
  padding: 24px;
  background: var(--app-workspace-bg, #f7f8fa);
}
</style>

<style>
/* 深色侧栏 n-menu 文字色覆盖（Naive UI inverted 模式仍偏浅，调到 slate-400） */
.n-layout-sider .n-menu .n-menu-item-content {
  color: #94a3b8;
}
.n-layout-sider .n-menu .n-menu-item-content--selected,
.n-layout-sider .n-menu .n-menu-item-content--selected .n-menu-item-content-header {
  color: #fff;
}
</style>
