import { inject, type InjectionKey } from 'vue'
import { authStore, type AuthStore } from './store'

export const authKey: InjectionKey<AuthStore> = Symbol('harbor-market-auth')

export function useAuth(): AuthStore {
  return inject(authKey, authStore)
}
