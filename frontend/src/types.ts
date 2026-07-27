export type Role = 'admin' | 'guest' | 'none'
export type ArchiveType = 'original' | 'resample'
export type GuestDownloadMode = 'disabled' | ArchiveType

export interface SessionState {
  authenticated: boolean
  role: Role
  csrf_token: string | null
  guest_cache_access: boolean
  guest_download_mode: GuestDownloadMode
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
  guest_cache_access: boolean
  guest_download_mode: GuestDownloadMode
  cache_enabled: boolean
  retention_days: number
  cache_limit_bytes: number
  max_archive_size_mb: number
  worker_concurrency: number
  api_token_configured: boolean
}
