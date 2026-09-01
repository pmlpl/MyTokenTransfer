import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('mtt_token') || '')

  function setToken(t: string) {
    token.value = t
    localStorage.setItem('mtt_token', t)
  }

  function logout() {
    token.value = ''
    localStorage.removeItem('mtt_token')
  }

  return { token, setToken, logout }
})
