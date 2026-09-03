import axios from 'axios'
import { createDiscreteApi } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

// axios 拦截器在组件 setup 之外，用 Naive UI 的 discrete API 创建全局 message 实例
// 主题不跟随 NConfigProvider（discrete 是独立容器），但错误提示主题差异可接受
// 传入 ['message'] 仅创建 message API，避免无谓初始化 dialog/notification/loadingBar
const { message: discreteMessage } = createDiscreteApi(['message'])

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000',
  timeout: 30000,
})

client.interceptors.request.use((config) => {
  const auth = useAuthStore()
  // 请求已显式携带鉴权头（如网关 token）时不覆盖
  if (auth.token && !config.headers?.Authorization) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

client.interceptors.response.use(
  (res) => res,
  async (err) => {
    const status = err.response?.status
    // /v1/* 是网关鉴权（另一套 token），失败不应登出管理台会话
    const url: string = err.config?.url || ''
    const isGatewayCall = url.includes('/v1/')
    if (status === 401 && !isGatewayCall) {
      const auth = useAuthStore()
      auth.logout()
      const { default: router } = await import('@/router')
      if (router.currentRoute.value.name !== 'login') {
        router.push({ name: 'login' })
      }
      discreteMessage.error('会话已失效，请重新登录')
    } else if (err.response?.data?.detail) {
      discreteMessage.error(err.response.data.detail)
    } else {
      discreteMessage.error(err.message || '请求失败')
    }
    return Promise.reject(err)
  },
)

export default client
