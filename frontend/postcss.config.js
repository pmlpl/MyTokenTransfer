// Phase 0：postcss 启用 tailwindcss + autoprefixer
// tailwindcss 3.x（preflight 已在 tailwind.config.js 关掉，仅提供 utility）
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
