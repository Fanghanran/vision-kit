import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('va-token') || '')
  const isAuthenticated = computed(() => !!token.value)

  function setToken(newToken: string) {
    token.value = newToken
    if (newToken) {
      localStorage.setItem('va-token', newToken)
    } else {
      localStorage.removeItem('va-token')
    }
  }

  function clearToken() {
    token.value = ''
    localStorage.removeItem('va-token')
  }

  return { token, isAuthenticated, setToken, clearToken }
})
