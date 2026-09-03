<template>
  <n-config-provider
    :theme="theme.naiveTheme"
    :theme-overrides="theme.overrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-loading-bar-provider>
      <n-message-provider>
        <n-dialog-provider>
          <n-notification-provider>
            <router-view />
          </n-notification-provider>
        </n-dialog-provider>
      </n-message-provider>
    </n-loading-bar-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
// v2 根：Naive UI providers 包裹 router-view
// 主题（浅/深/跟随系统）由 design/use-theme store 驱动
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NNotificationProvider,
  NLoadingBarProvider,
  zhCN,
  dateZhCN,
} from 'naive-ui'
import { useThemeStore } from '@/design/use-theme'

const theme = useThemeStore()
</script>
