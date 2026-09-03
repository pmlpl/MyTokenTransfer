// v2 设计系统单一来源：颜色 / 间距 / 圆角 / 字号 / 动效 / 阴影
// 所有 Naive UI 主题 override 与业务组件都从这里读，保证改一处全站生效
// 色板来自 plan.md B 节：业务蓝主色 + slate 侧栏 + 浅/深双工作区

export const tokens = {
  // —— 主色 / 语义色（双主题共用）——
  color: {
    brand: '#4f7cff',
    brand50: '#eef3ff', // hover/选中底色
    brand600: '#3b63e0', // hover
    brand700: '#2f4fb3', // pressed
    success: '#10b981',
    warning: '#f59e0b',
    danger: '#ef4444',
    info: '#6366f1',
  },

  // —— 浅色主题（默认）——
  light: {
    workspace: '#f7f8fa', // 工作区背景
    card: '#ffffff', // 卡片背景
    sidebar: '#0f172a', // 侧栏（slate-900，深色固定）
    sidebarText: '#cbd5e1', // 侧栏文字
    sidebarActive: '#ffffff', // 侧栏选中文字
    sidebarActiveBg: '#1e293b', // 侧栏选中底色
    textPrimary: '#0f172a',
    textSecondary: '#64748b',
    border: '#e5e7eb',
    hover: '#f1f5f9',
  },

  // —— 深色主题 ——
  dark: {
    workspace: '#0b1220',
    card: '#111a2e',
    sidebar: '#070d18',
    sidebarText: '#94a3b8',
    sidebarActive: '#ffffff',
    sidebarActiveBg: '#1e293b',
    textPrimary: '#e2e8f0',
    textSecondary: '#94a3b8',
    border: '#1e293b',
    hover: '#1e293b',
  },

  // —— 圆角（plan：卡片 12 / 按钮 8 / 输入 6）——
  radius: {
    card: '12px',
    btn: '8px',
    input: '6px',
    tag: '4px',
  },

  // —— 阴影 ——
  shadow: {
    card: '0 1px 2px rgba(15,23,42,.04), 0 4px 12px rgba(15,23,42,.04)',
    cardHover: '0 2px 4px rgba(15,23,42,.06), 0 8px 24px rgba(15,23,42,.08)',
    sidebar: '2px 0 8px rgba(15,23,42,.04)',
  },

  // —— 动效 ——
  motion: {
    ease: 'cubic-bezier(0.4, 0, 0.2, 1)',
    easeOut: 'cubic-bezier(0, 0, 0.2, 1)',
    durationFast: '150ms',
    durationNormal: '250ms',
    durationSlow: '400ms',
  },

  // —— 字号 ——
  font: {
    xs: '12px',
    sm: '13px',
    base: '14px',
    lg: '16px',
    xl: '18px',
    kpi: '30px', // KPI 大数字
  },
} as const

export type Tokens = typeof tokens
