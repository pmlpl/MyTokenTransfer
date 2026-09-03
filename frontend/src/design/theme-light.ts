// Naive UI 浅色主题 overrides（默认主题）
// 所有颜色/圆角/阴影从 tokens.ts 派生，单一来源
import type { GlobalThemeOverrides } from 'naive-ui'
import { tokens } from './tokens'

export const themeLight: GlobalThemeOverrides = {
  common: {
    primaryColor: tokens.color.brand,
    primaryColorHover: tokens.color.brand600,
    primaryColorPressed: tokens.color.brand700,
    primaryColorSuppl: tokens.color.brand,
    infoColor: tokens.color.info,
    infoColorHover: tokens.color.info,
    successColor: tokens.color.success,
    successColorHover: tokens.color.success,
    warningColor: tokens.color.warning,
    warningColorHover: tokens.color.warning,
    errorColor: tokens.color.danger,
    errorColorHover: tokens.color.danger,

    bodyColor: tokens.light.workspace,
    cardColor: tokens.light.card,
    modalColor: tokens.light.card,
    popoverColor: tokens.light.card,
    tableColor: tokens.light.card,

    textColorBase: tokens.light.textPrimary,
    textColor1: tokens.light.textPrimary,
    textColor2: tokens.light.textPrimary,
    textColor3: tokens.light.textSecondary,
    placeholderColor: tokens.light.textSecondary,

    borderColor: tokens.light.border,
    dividerColor: tokens.light.border,

    borderRadius: tokens.radius.input,
    borderRadiusSmall: tokens.radius.tag,
  },
  Card: {
    borderRadius: tokens.radius.card,
    color: tokens.light.card,
    colorEmbedded: tokens.light.card,
  },
  Button: {
    borderRadiusMedium: tokens.radius.btn,
    borderRadiusLarge: tokens.radius.btn,
    borderRadiusSmall: tokens.radius.tag,
  },
  Input: {
    borderRadius: tokens.radius.input,
  },
  Menu: {
    // 侧栏菜单（深色侧栏固定，不受浅/深主题影响）
    color: tokens.light.sidebar,
    colorHover: tokens.light.sidebarActiveBg,
    colorActive: tokens.light.sidebarActiveBg,
    itemTextColor: tokens.light.sidebarText,
    itemTextColorActive: tokens.light.sidebarActive,
    itemTextColorActiveHover: tokens.light.sidebarActive,
    itemTextColorChildActive: tokens.light.sidebarActive,
    itemIconColor: tokens.light.sidebarText,
    itemIconColorActive: tokens.light.sidebarActive,
  },
  Tag: {
    borderRadius: tokens.radius.tag,
  },
  DataTable: {
    borderRadius: tokens.radius.card,
  },
}
