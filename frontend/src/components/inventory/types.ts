/** Normalized relic row shared by the authenticated + anonymous inventory views. */
export type ManagedRelic = {
  key: string // unique row key (DB id for auth, ga_handle for anon)
  gaHandle: number
  realId: number
  name: string
  color: string
  tier: string
  isDeep: boolean
  effects: number[]
  curses: number[]
  isFavorite: boolean
  equipped: boolean
}
