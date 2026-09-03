// Naive UI 深色主题：基于官方 darkTheme + overrides
// 侧栏在深色主题下保持更深的 slate，工作区用 #0b1220
import { darkTheme } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import { tokens } from './tokens'

export const naiveDarkTheme = darkTheme

export const themeDarkOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: tokens.color.brand,
    primaryColorHover: tokens.color.brand600,
    primaryColorPressed: tokens.color.brand700,
    primaryColorSuppl: tokens.color.brand,
    infoColor: tokens.color.info,
    successColor: tokens.color.success,
    warningColor: tokens.color.warning,
    errorColor: tokens.color.danger,

    bodyColor: tokens.dark.workspace,
    cardColor: tokens.dark.card,
    modalColor: tokens.dark.card,
    popoverColor: tokens.dark.card,
    tableColor: tokens.dark.card,

    textColor1: tokens.dark.textPrimary,
    textColor2: tokens.dark.textPrimary,
    textColor3: tokens.dark.textSecondary,

    borderColor: tokens.dark.border,
    dividerColor: tokens.dark.border,

    borderRadius: tokens.radius.input,
    borderRadiusSmall: tokens.radius.tag,
  },
  Card: {
    borderRadius: tokens.radius.card,
    color: tokens.dark.card,
    colorEmbedded: tokens.dark.card,
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
    color: tokens.dark.sidebar,
    colorHover: tokens.dark.sidebarActiveBg,
    colorActive: tokens.dark.sidebarActiveBg,
    itemTextColor: tokens.dark.sidebarText,
    itemTextColorActive: tokens.dark.sidebarActive,
    itemTextColorActiveHover: tokens.dark.sidebarActive,
    itemIconColor: tokens.dark.sidebarText,
    itemIconColorActive: tokens.dark.sidebarActive,
  },
  Tag: {
    borderRadius: tokens.radius.tag,
  },
  DataTable: {
    borderRadius: tokens.radius.card,
  },
}
