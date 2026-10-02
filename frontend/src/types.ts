export type AccessMode = 'admin' | 'guest' | 'core'
export type ArchiveType = 'original' | 'resample'
export type GuestDownloadMode = 'disabled' | ArchiveType

export interface SessionPrices {
  create_original: number
  create_resample: number
  download_original: number
  download_resample: number
}

export interface SessionState {
  admin: boolean
  mode: AccessMode
  guest_download_mode: GuestDownloadMode
  site_identity: boolean
  core_configured: boolean
  core_login_url: string | null
  prices: SessionPrices
  credit_balance: number | null
  csrf_token: string | null
}

export interface ServiceStats {
  worker_alive: boolean
  worker_last_beat_at: number | null
  cache_enabled: boolean
  queue: { queued: number; active: number }
  downloads: { archives_ready: number; tasks_completed: number; tasks_failed: number; served_total: number }
  traffic: { bytes_downloaded_total: number; cache_used_bytes: number; cache_stored_bytes: number }
  credits: { total_spent: number }
  eh_pool: { gp: number | null; credits: number | null; accounts_ready: number; accounts_total: number }
}

export interface Task {
  id: string
  gallery_url: string
  gid: number
  archive_type: ArchiveType
  title: string | null
  status: string
  progress: number
  download_count: number
  size_bytes: number | null
  filename: string | null
  error: string | null
  wait_reason: string | null
  retry_count: number
  created_at: string
  updated_at: string
  expired_at: string
  completed_at: string | null
  download_url: string | null
}

export interface Account {
  id: string
  name: string
  cookie_fingerprint: string
  gp: number | null
  credits: number | null
  enabled: boolean
  priority: number
  cooldown_reason: string | null
  cooldown_until: string | null
  last_used_at: string | null
  last_download_cost: number | null
  last_download_cost_type: string | null
  last_error: string | null
  created_at: string
}

export interface Settings {
  access_mode: AccessMode
  core_enabled: boolean
  core_site_url: string
  core_base_url: string
  guest_download_mode: GuestDownloadMode
  cache_enabled: boolean
  retention_days: number
  cache_limit_bytes: number
  max_archive_size_mb: number
  worker_concurrency: number
  task_max_retries: number
  eh_proxy_url: string
  api_token_configured: boolean
  price_create_original: number
  price_create_resample: number
  price_download_original: number
  price_download_resample: number
}
