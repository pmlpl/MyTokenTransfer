import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

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
      ElMessage.error('会话已失效，请重新登录')
    } else if (err.response?.data?.detail) {
      ElMessage.error(err.response.data.detail)
    } else {
      ElMessage.error(err.message || '请求失败')
    }
    return Promise.reject(err)
  },
)

export default client
