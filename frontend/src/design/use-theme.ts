// 主题切换 store：浅色默认 + 可切深色/跟随系统（用户拍板 4.3-1）
// 持久化到 localStorage，监听系统 prefers-color-scheme 变化
// 暴露 naiveTheme + overrides 给 NConfigProvider，isDark 给业务组件用（ECharts 等）
import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { GlobalTheme, GlobalThemeOverrides } from 'naive-ui'
import { themeLight } from './theme-light'
import { naiveDarkTheme, themeDarkOverrides } from './theme-dark'

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'mtt_theme_mode'

function readStoredMode(): ThemeMode {
  const v = localStorage.getItem(STORAGE_KEY)
  if (v === 'light' || v === 'dark' || v === 'system') return v
  return 'light' // 默认浅色
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>(readStoredMode())
  const systemDark = ref(
    typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia('(prefers-color-scheme: dark)').matches
      : false,
  )

  // 监听系统主题变化（仅 mode=system 时跟随生效）
  if (typeof window !== 'undefined' && window.matchMedia) {
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    mql.addEventListener('change', (e) => {
      systemDark.value = e.matches
    })
  }

  const isDark = computed(
    () => mode.value === 'dark' || (mode.value === 'system' && systemDark.value),
  )

  // NConfigProvider 的 :theme（深色用 darkTheme，浅色 null）
  const naiveTheme = computed<GlobalTheme | null>(() => (isDark.value ? naiveDarkTheme : null))
  // NConfigProvider 的 :theme-overrides
  const overrides = computed<GlobalThemeOverrides>(() =>
    isDark.value ? themeDarkOverrides : themeLight,
  )

  function setMode(m: ThemeMode) {
    mode.value = m
    localStorage.setItem(STORAGE_KEY, m)
  }

  // 同步 data-theme 到 html，供非 Naive UI 处（ECharts、自定义背景）用 CSS 变量
  watch(
    isDark,
    (dark) => {
      document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
    },
    { immediate: true },
  )

  return { mode, isDark, naiveTheme, overrides, setMode }
})
