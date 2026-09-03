/** @type {import('tailwindcss').Config} */
// Phase 0：tailwind 仅做 utility 辅助，主样式走 Naive UI 主题
// 关掉 preflight 避免与 Naive UI 的内联样式/reset 冲突
export default {
  content: ['./index.html', './src/**/*.{vue,ts,tsx}'],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        // 与 src/design/tokens.ts 保持一致，便于在 className 里用语义色
        brand: {
          DEFAULT: '#4f7cff',
          50: '#eef3ff',
          500: '#4f7cff',
          600: '#3b63e0',
          700: '#2f4fb3',
        },
      },
      borderRadius: {
        card: '12px',
        btn: '8px',
        input: '6px',
      },
    },
  },
  plugins: [],
}
