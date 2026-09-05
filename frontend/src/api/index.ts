import client from './client'

export interface ApiKey {
  id: number
  provider_id: number
  provider_name: string | null
  group_id: number | null
  group_name: string | null
  account_label: string | null
  name: string
  key_mask: string
  status: string
  balance: string | null
  weight: number
  tags: string | null
  last_tested_at: string | null
  rate_limited_until: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface Provider {
  id: number
  name: string
  slug: string
  base_url: string | null
  probe_kind: string
  models: string[] | null
  is_active: boolean
  note: string | null
  created_at: string
  updated_at: string
}

export interface KeyGroup {
  id: number
  name: string
  description: string | null
  created_at: string
  updated_at: string
}

export interface ChannelKeyItem {
  id?: number
  key_id: number
  key_mask?: string
  weight: number
  priority: number
}

export interface ChannelTierItem {
  id?: number
  order?: number
  provider_id: number
  provider_name?: string
  keys: ChannelKeyItem[]
}

export interface ChannelMappingItem {
  id?: number
  provider_id: number
  provider_name?: string
  native_model: string
}

export interface Channel {
  id: number
  name: string
  virtual_model: string
  strategy: string
  max_retries: number
  is_active: boolean
  note: string | null
  tiers: ChannelTierItem[]
  mappings: ChannelMappingItem[]
  created_at: string
  updated_at: string
}

export interface CallLogItem {
  id: number
  created_at: string | null
  channel_id: number | null
  tier_order: number | null
  provider_id: number | null
  key_id: number | null
  virtual_model: string | null
  native_model: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost: number
  latency_ms: number
  status: string
  error: string | null
  stream: boolean
}

export interface Pricing {
  id: number
  model_name: string
  input_price: number
  cached_input_price: number
  output_price: number
  off_peak_input_price: number
  off_peak_output_price: number
  context_length: number
  currency: string
  note: string | null
  created_at: string
  updated_at: string
}

export interface UsageResp {
  days: number
  hourly: { hour: number; label: string; is_today: boolean; calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number; cached_tokens: number; cost: number }[]
  hourly_by_model: { hour: number; label: string; is_today: boolean; model: string; calls: number; total_tokens: number; cost: number }[]
  daily: { date: string; calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number; cached_tokens: number; cost: number }[]
  daily_by_model: { date: string; model: string; calls: number; total_tokens: number; cost: number }[]
  by_model: { model: string; calls: number; total_tokens: number; cost: number }[]
  by_key: { key_id: number | null; key_name: string; calls: number; prompt_tokens: number; cached_tokens: number; cache_hit_rate: number; cost: number }[]
  totals: { calls: number; total_tokens: number; cached_tokens: number; cache_hit_rate: number; cost: number }
}

export interface AlertItem {
  id: number
  type: string
  severity: string
  key_id: number | null
  provider_id: number | null
  message: string
  resolved: boolean
  created_at: string
  resolved_at: string | null
}

export interface AlertListResp {
  items: AlertItem[]
  total: number
  unresolved: number
}

export const api = {
  auth: {
    login: (password: string) =>
      client.post<{ token: string; token_type: string }>('/auth/login', { password }).then((r) => r.data),
    me: () => client.get('/auth/me').then((r) => r.data),
    changePassword: (old_password: string, new_password: string) =>
      client.post<{ ok: boolean; message: string }>('/auth/change-password', { old_password, new_password }).then((r) => r.data),
  },
  providers: {
    list: () => client.get<Provider[]>('/providers').then((r) => r.data),
    create: (data: Partial<Provider>) => client.post<Provider>('/providers', data).then((r) => r.data),
    update: (id: number, data: Partial<Provider>) => client.put<Provider>(`/providers/${id}`, data).then((r) => r.data),
    remove: (id: number) => client.delete(`/providers/${id}`),
  },
  groups: {
    list: () => client.get<KeyGroup[]>('/groups').then((r) => r.data),
    create: (data: { name: string; description?: string }) => client.post<KeyGroup>('/groups', data).then((r) => r.data),
    update: (id: number, data: Partial<KeyGroup>) => client.put<KeyGroup>(`/groups/${id}`, data).then((r) => r.data),
    remove: (id: number) => client.delete(`/groups/${id}`),
  },
  keys: {
    list: (params?: { provider_id?: number; group_id?: number; status?: string }) =>
      client.get<ApiKey[]>('/keys', { params }).then((r) => r.data),
    create: (data: Record<string, unknown>) => client.post<ApiKey>('/keys', data).then((r) => r.data),
    update: (id: number, data: Record<string, unknown>) => client.put<ApiKey>(`/keys/${id}`, data).then((r) => r.data),
    remove: (id: number) => client.delete(`/keys/${id}`),
    reveal: (id: number) => client.post<{ id: number; key_value: string }>(`/keys/${id}/reveal`).then((r) => r.data),
    test: (id: number) => client.post(`/keys/${id}/test`).then((r) => r.data),
    testBatch: (params?: { provider_id?: number; group_id?: number }) =>
      client.post('/keys/test', null, { params }).then((r) => r.data as unknown[]),
    exportBundle: (password: string) =>
      client.post<Record<string, unknown>>('/keys/export', { password }).then((r) => r.data),
    importBundle: (password: string, bundle: Record<string, unknown>) =>
      client
        .post<{ providers: number; groups: number; keys_imported: number; keys_skipped: number }>(
          '/keys/import',
          { password, bundle },
        )
        .then((r) => r.data),
  },
  stats: {
    overview: () =>
      client
        .get<{ providers: number; keys: number; active: number; by_status: Record<string, number> }>('/stats/overview')
        .then((r) => r.data),
    usage: (days = 14) =>
      client.get<UsageResp>('/stats/usage', { params: { days } }).then((r) => r.data),
  },
  pricing: {
    list: () => client.get<Pricing[]>('/pricing').then((r) => r.data),
    create: (data: Record<string, unknown>) => client.post<Pricing>('/pricing', data).then((r) => r.data),
    update: (id: number, data: Record<string, unknown>) => client.put<Pricing>(`/pricing/${id}`, data).then((r) => r.data),
    remove: (id: number) => client.delete(`/pricing/${id}`),
  },
  channels: {
    list: () => client.get<Channel[]>('/channels').then((r) => r.data),
    create: (data: Record<string, unknown>) => client.post<Channel>('/channels', data).then((r) => r.data),
    update: (id: number, data: Record<string, unknown>) => client.put<Channel>(`/channels/${id}`, data).then((r) => r.data),
    remove: (id: number) => client.delete(`/channels/${id}`),
  },
  logs: {
    list: (params?: { limit?: number; offset?: number; status?: string }) =>
      client.get<{ items: CallLogItem[]; limit: number; offset: number }>('/logs', { params }).then((r) => r.data),
  },
  alerts: {
    list: (params?: { unresolved?: boolean; limit?: number }) =>
      client.get<AlertListResp>('/alerts', { params }).then((r) => r.data),
    resolve: (id: number) => client.post<AlertItem>(`/alerts/${id}/resolve`).then((r) => r.data),
  },
  settings: {
    get: () => client.get<{ gateway_token: string }>('/settings').then((r) => r.data),
  },
  gateway: {
    chat: (body: Record<string, unknown>, token: string) =>
      client.post('/v1/chat/completions', body, {
        headers: { Authorization: `Bearer ${token}` },
        timeout: 120000,
      }).then((r) => r.data),
    models: (token: string) =>
      client.get('/v1/models', { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.data),
  },
}
