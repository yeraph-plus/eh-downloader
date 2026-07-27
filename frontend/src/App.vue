<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { NAlert, NButton, NDataTable, NInput, NInputNumber, NProgress, NRadioButton, NRadioGroup, NSwitch, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { Download, Languages, LogOut, RefreshCw, Settings as SettingsIcon, Upload, UserRound, X } from '@lucide/vue'
import { api, ApiError, clearCsrfToken, setCsrfToken } from './api'
import type { Account, ArchiveType, GuestDownloadMode, SessionState, Settings, Task } from './types'

const copy = {
  adminLogin: ['管理员登录', 'Administrator sign in'], username: ['用户名', 'Username'], password: ['密码', 'Password'], login: ['登录', 'Sign in'],
  guestAccess: ['游客访问', 'Continue as guest'], backToLogin: ['返回登录', 'Back to sign in'],
  downloads: ['下载', 'Downloads'], settings: ['设置', 'Settings'], logout: ['退出', 'Sign out'], guest: ['游客', 'Guest'],
  resample: ['重采样归档', 'Resample archive'], original: ['原始归档', 'Original archive'], addTask: ['添加任务', 'Add tasks'],
  galleryUrls: ['画廊链接，每行一个', 'Gallery URLs, one per line'],
  invalidGalleryUrl: ['画廊链接格式无效，仅支持 EH/ExHentai HTTPS 画廊地址', 'Invalid gallery URL. Only HTTPS EH/ExHentai gallery links are supported'],
  tasks: ['下载任务', 'Download tasks'], items: ['项', 'items'], refresh: ['刷新', 'Refresh'], downloadArchive: ['下载归档', 'Download archive'],
  file: ['文件', 'File'], size: ['大小', 'Size'], status: ['状态', 'Status'], info: ['信息', 'Details'],
  accountPool: ['账户池', 'Account pool'], cookieHidden: ['Cookie 不会回显', 'Cookies are never displayed'], accountName: ['账户名称', 'Account name'],
  chooseCookies: ['选择 cookies.txt', 'Choose cookies.txt'], importAccount: ['导入账户', 'Import account'],
  cookieHelp: ['使用标准 Netscape cookies.txt；可通过 Get cookies.txt LOCALLY 浏览器插件导出。', 'Import a standard Netscape cookies.txt file. You can export one with the Get cookies.txt LOCALLY browser extension.'],
  account: ['账户', 'Account'], unknown: ['未知', 'Unknown'], lastDownload: ['上次下载', 'Last download'], lastUsed: ['上次使用', 'Last used'], enabled: ['启用', 'Enabled'], deleteAccount: ['删除账户', 'Delete account'],
  systemSettings: ['系统设置', 'System settings'], workerReads: ['独立 Worker 会在下一轮读取新设置', 'The worker reads changes on its next cycle'],
  guestCacheAccess: ['游客访问归档列表', 'Guest archive access'], guestCacheAccessDesc: ['公开有效的本地缓存或 EH 远程中转归档；关闭后同时禁止游客创建任务。', 'Expose valid local caches or EH relay archives. Turning this off also disables guest task creation.'],
  guestDownloads: ['游客创建下载', 'Guest downloads'], guestDownloadsDesc: ['所有游客共享一个公共身份；此项仅控制新任务类型。', 'All guests share one public identity. This controls only the type of new tasks.'],
  guestDisabled: ['完全禁止', 'Disabled'],
  enableCache: ['启用本地缓存', 'Local cache'], enableCacheDesc: ['开启时下载并校验 ZIP 后保存；关闭时不落盘，由 API 实时中转 EH 下载。', 'When enabled, verified ZIP files are stored locally. When disabled, the API relays each EH download without storing it.'],
  retention: ['缓存保留天数', 'Cache retention'], retentionDesc: ['归档完成后保留的天数；设为 0 时永久保留。', 'Days to retain completed archives. Set to 0 to keep them permanently.'],
  cacheLimit: ['缓存上限 GiB', 'Cache limit (GiB)'], cacheLimitDesc: ['缓存文件、下载中的临时文件和容量预留共享此上限。', 'Cached files, active temporary downloads, and capacity reservations share this limit.'],
  archiveLimit: ['最大归档大小 MB', 'Maximum archive size (MB)'], archiveLimitDesc: ['在向 EH 提交归档请求前按页面估算大小检查，默认 4096 MB。', 'Checked against the page estimate before submitting the archive request to EH. Default: 4096 MB.'],
  concurrency: ['下载并发', 'Download concurrency'], concurrencyDesc: ['单 Worker 内同时处理的下载数量，允许 1-4。', 'Concurrent downloads in the single worker. Allowed range: 1-4.'],
  saveSettings: ['保存设置', 'Save settings'], saved: ['设置已保存', 'Settings saved'], queued: ['任务已进入队列', 'Task added to queue'], accountImported: ['账户已加入账户池', 'Account added'],
  apiToken: ['API Token', 'API token'], tokenDesc: ['管理员权限；轮换后旧 Token 立即失效', 'Administrator access; rotating immediately revokes the previous token'],
  rotateToken: ['轮换 Token', 'Rotate token'], createToken: ['创建 Token', 'Create token'], shownOnce: ['仅显示一次', 'Shown once'],
  free: ['免费', 'Free'], language: ['切换到 English', 'Switch to Chinese'],
  queuedStatus: ['排队', 'Queued'], checkingCache: ['检查缓存', 'Checking cache'], requestingArchive: ['请求归档', 'Requesting archive'],
  archivePending: ['EH 构建中', 'EH building'], archiveReady: ['归档就绪', 'Archive ready'], downloadingStatus: ['下载中', 'Downloading'],
  waitingDownload: ['等待下载', 'Ready on demand'], downloadCount: ['下载次数', 'Downloads'],
  verifying: ['校验中', 'Verifying'], cached: ['写入缓存', 'Caching'], completed: ['已完成', 'Completed'], failed: ['失败', 'Failed'], expired: ['已过期', 'Expired'],
} as const

type CopyKey = keyof typeof copy
type Locale = 'zh-CN' | 'en-US'
const savedLocale = localStorage.getItem('ehd-locale')
const locale = ref<Locale>(savedLocale === 'en-US' ? 'en-US' : 'zh-CN')
const t = (key: CopyKey) => copy[key][locale.value === 'zh-CN' ? 0 : 1]
function toggleLocale() {
  locale.value = locale.value === 'zh-CN' ? 'en-US' : 'zh-CN'
  localStorage.setItem('ehd-locale', locale.value)
}

const message = useMessage()
const ready = ref(false)
const role = ref<'admin' | 'guest' | 'none'>('none')
const activePage = ref<'downloads' | 'settings'>('downloads')
const error = ref('')
const username = ref('')
const password = ref('')
const loginLoading = ref(false)
const galleryUrls = ref('')
const archiveType = ref<ArchiveType>('resample')
const guestDownloadMode = ref<GuestDownloadMode>('disabled')
const guestCacheAccess = ref(false)
const submitting = ref(false)
const loading = ref(false)
const tasks = ref<Task[]>([])
const accounts = ref<Account[]>([])
const settings = ref<Settings | null>(null)
const accountName = ref('')
const cookieFile = ref<File | null>(null)
const saving = ref(false)
const revealedToken = ref('')
let pollTimer: number | undefined

const isAdmin = computed(() => role.value === 'admin')
const canCreateTask = computed(() => isAdmin.value || guestDownloadMode.value !== 'disabled')
const statusMeta = computed<Record<string, { label: string; type: 'default' | 'info' | 'success' | 'warning' | 'error' }>>(() => ({
  queued: { label: t('queuedStatus'), type: 'default' }, checking_cache: { label: t('checkingCache'), type: 'info' },
  requesting_archive: { label: t('requestingArchive'), type: 'info' }, archive_pending: { label: t('archivePending'), type: 'warning' },
  archive_ready: { label: t('archiveReady'), type: 'info' }, waiting_download: { label: t('waitingDownload'), type: 'success' }, downloading: { label: t('downloadingStatus'), type: 'info' },
  verifying: { label: t('verifying'), type: 'warning' }, cached: { label: t('cached'), type: 'info' },
  completed: { label: t('completed'), type: 'success' }, failed: { label: t('failed'), type: 'error' }, expired: { label: t('expired'), type: 'default' },
}))

function formatBytes(value: number | null): string {
  if (value === null) return '—'
  if (value < 1024) return `${value} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let amount = value / 1024
  let index = 0
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index++ }
  return `${amount.toFixed(amount >= 100 ? 0 : 1)} ${units[index]}`
}

function formatDate(value: string | null): string { return value ? new Date(value).toLocaleString(locale.value) : '—' }
function lastCost(row: Account): string {
  if (!row.last_download_cost_type) return '—'
  if (row.last_download_cost_type === 'free') return t('free')
  return `${(row.last_download_cost || 0).toLocaleString(locale.value)} ${row.last_download_cost_type.toUpperCase()}`
}

const taskColumns = computed<DataTableColumns<Task>>(() => [
  { title: t('file'), key: 'file', minWidth: 320, render: (row) => h('div', { class: 'file-cell' }, [
    h('span', { class: 'file-title' }, row.filename || row.title || row.gallery_url),
    h('a', { class: 'gallery-url mono', href: row.gallery_url, target: '_blank', rel: 'noreferrer' }, row.gallery_url),
    h('span', { class: 'row-meta' }, `${row.archive_type === 'original' ? t('original') : t('resample')} · ${t('downloadCount')} ${row.download_count}`),
  ]) },
  { title: t('size'), key: 'size_bytes', width: 110, render: (row) => formatBytes(row.size_bytes) },
  { title: t('status'), key: 'status', width: 150, render: (row) => {
    const meta = statusMeta.value[row.status] || { label: row.status, type: 'default' as const }
    return h('div', { class: 'status-cell' }, [
      h(NTag, { type: meta.type, bordered: false, size: 'small' }, { default: () => meta.label }),
      row.status === 'downloading' ? h(NProgress, { percentage: row.progress, height: 5, showIndicator: false, borderRadius: 0 }) : null,
    ])
  } },
  { title: t('info'), key: 'message', minWidth: 230, render: (row) => h('span', { class: row.error ? 'error-text' : 'muted' }, row.error || row.wait_reason || formatDate(row.created_at)) },
  { title: '', key: 'action', width: 64, align: 'right', render: (row) => row.download_url
    ? h(NButton, { tag: 'a', href: row.download_url, quaternary: true, circle: true, title: t('downloadArchive') }, { icon: () => h(Download, { size: 18 }) }) : null },
])

const accountColumns = computed<DataTableColumns<Account>>(() => [
  { title: t('account'), key: 'name', minWidth: 180, render: (row) => h('div', { class: 'file-cell' }, [h('span', { class: 'file-title' }, row.name), h('span', { class: 'row-meta mono' }, row.cookie_fingerprint)]) },
  { title: 'GP', key: 'gp', width: 120, render: (row) => row.gp?.toLocaleString(locale.value) ?? t('unknown') },
  { title: 'Credits', key: 'credits', width: 120, render: (row) => row.credits?.toLocaleString(locale.value) ?? t('unknown') },
  { title: t('lastDownload'), key: 'last_download', width: 130, render: lastCost },
  { title: t('lastUsed'), key: 'last_used_at', width: 180, render: (row) => formatDate(row.last_used_at) },
  { title: t('enabled'), key: 'enabled', width: 80, render: (row) => h(NSwitch, { value: row.enabled, onUpdateValue: (value) => updateAccount(row.id, { enabled: value }) }) },
  { title: '', key: 'actions', width: 50, align: 'right', render: (row) => h(NButton, { quaternary: true, circle: true, title: t('deleteAccount'), onClick: () => deleteAccount(row.id) }, { icon: () => h(X, { size: 17 }) }) },
])

async function initialize() {
  try {
    const state = await api<SessionState>('/api/v1/auth/session')
    role.value = state.role
    guestCacheAccess.value = state.guest_cache_access
    guestDownloadMode.value = state.guest_download_mode
    if (state.role === 'guest' && state.guest_download_mode !== 'disabled') archiveType.value = state.guest_download_mode
    if (state.csrf_token) setCsrfToken(state.csrf_token)
    if (state.authenticated) { await refreshAll(); startPolling() }
  } catch (cause) { error.value = (cause as Error).message } finally { ready.value = true }
}

async function enterGuest() {
  role.value = 'guest'
  error.value = ''
  if (guestDownloadMode.value !== 'disabled') archiveType.value = guestDownloadMode.value
  await refreshTasks()
  startPolling()
}

function leaveGuest() {
  stopPolling()
  tasks.value = []
  role.value = 'none'
}

async function login() {
  loginLoading.value = true; error.value = ''
  try {
    const result = await api<{ csrf_token: string }>('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) })
    setCsrfToken(result.csrf_token); role.value = 'admin'; password.value = ''; await refreshAll(); startPolling()
  } catch (cause) { error.value = (cause as Error).message } finally { loginLoading.value = false }
}

async function logout() {
  try { await api('/api/v1/auth/logout', { method: 'POST' }) } finally {
    clearCsrfToken(); tasks.value = []; settings.value = null; stopPolling()
    const state = await api<SessionState>('/api/v1/auth/session')
    role.value = state.role; guestCacheAccess.value = state.guest_cache_access; guestDownloadMode.value = state.guest_download_mode
  }
}

async function refreshTasks() {
  try { tasks.value = await api<Task[]>('/api/v1/tasks') }
  catch (cause) { if (!(cause instanceof ApiError && cause.status === 401)) error.value = (cause as Error).message }
}

async function refreshAll() {
  loading.value = true
  try {
    await refreshTasks()
    if (isAdmin.value) {
      const [rows, current] = await Promise.all([api<Account[]>('/api/v1/accounts'), api<Settings>('/api/v1/settings')])
      accounts.value = rows; settings.value = current
    }
  } finally { loading.value = false }
}

async function createTask() {
  submitting.value = true; error.value = ''
  try {
    const galleryPattern = /^https:\/\/(?:e-hentai\.org|exhentai\.org)\/g\/\d+\/[a-f0-9]+\/?$/i
    const invalidLine = galleryUrls.value.split(/\r?\n/).find((line) => line.trim() && !galleryPattern.test(line.trim()))
    if (invalidLine) throw new Error(t('invalidGalleryUrl'))
    const created = await api<Task[]>('/api/v1/tasks', { method: 'POST', body: JSON.stringify({ gallery_urls: galleryUrls.value, archive_type: archiveType.value }) })
    galleryUrls.value = ''; message.success(`${created.length} ${t('queued')}`); await refreshTasks()
  } catch (cause) { error.value = (cause as Error).message } finally { submitting.value = false }
}

async function importAccount() {
  if (!cookieFile.value || !accountName.value) return
  saving.value = true
  try {
    const cookies_txt = await cookieFile.value.text()
    await api('/api/v1/accounts', { method: 'POST', body: JSON.stringify({ name: accountName.value, cookies_txt, priority: 100 }) })
    accountName.value = ''; cookieFile.value = null; message.success(t('accountImported')); accounts.value = await api<Account[]>('/api/v1/accounts')
  } catch (cause) { error.value = (cause as Error).message } finally { saving.value = false }
}

function selectCookieFile(event: Event) { cookieFile.value = (event.target as HTMLInputElement).files?.[0] || null }
async function updateAccount(id: string, payload: Record<string, unknown>) { await api(`/api/v1/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }); accounts.value = await api<Account[]>('/api/v1/accounts') }
async function deleteAccount(id: string) { try { await api(`/api/v1/accounts/${id}`, { method: 'DELETE' }); accounts.value = await api<Account[]>('/api/v1/accounts') } catch (cause) { error.value = (cause as Error).message } }
async function saveSettings() { if (!settings.value) return; saving.value = true; try { if (!settings.value.guest_cache_access) settings.value.guest_download_mode = 'disabled'; settings.value = await api<Settings>('/api/v1/settings', { method: 'PUT', body: JSON.stringify(settings.value) }); message.success(t('saved')) } catch (cause) { error.value = (cause as Error).message } finally { saving.value = false } }
async function rotateToken() { const result = await api<{ token: string }>('/api/v1/settings/api-token/rotate', { method: 'POST' }); revealedToken.value = result.token }
function startPolling() { stopPolling(); pollTimer = window.setInterval(refreshTasks, 3000) }
function stopPolling() { if (pollTimer) window.clearInterval(pollTimer); pollTimer = undefined }
onMounted(initialize)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div v-if="!ready" class="loading-screen">Eh Downloader</div>
  <main v-else-if="role === 'none'" class="login-shell">
    <n-button class="login-language" quaternary :title="t('language')" @click="toggleLocale"><template #icon><languages :size="17" /></template>{{ locale === 'zh-CN' ? 'EN' : '中文' }}</n-button>
    <section class="login-panel">
      <div class="brand-lockup"><span class="brand-mark">E</span><span>Eh Downloader</span></div>
      <h1>{{ t('adminLogin') }}</h1>
      <form class="login-form" @submit.prevent="login">
        <label>{{ t('username') }}<n-input v-model:value="username" autocomplete="username" /></label>
        <label>{{ t('password') }}<n-input v-model:value="password" type="password" show-password-on="click" autocomplete="current-password" /></label>
        <n-alert v-if="error" type="error" :show-icon="false">{{ error }}</n-alert>
        <n-button type="primary" attr-type="submit" :loading="loginLoading" block>{{ t('login') }}</n-button>
        <n-button secondary attr-type="button" block :disabled="!guestCacheAccess" @click="enterGuest"><template #icon><user-round :size="17" /></template>{{ t('guestAccess') }}</n-button>
      </form>
    </section>
  </main>

  <div v-else class="app-shell">
    <header class="topbar">
      <div class="brand-lockup"><span class="brand-mark">E</span><span>Eh Downloader</span></div>
      <nav>
        <button :class="{ active: activePage === 'downloads' }" @click="activePage = 'downloads'">{{ t('downloads') }}</button>
        <button v-if="isAdmin" :class="{ active: activePage === 'settings' }" @click="activePage = 'settings'"><settings-icon :size="16" />{{ t('settings') }}</button>
      </nav>
      <div class="top-actions">
        <n-button quaternary size="small" :title="t('language')" @click="toggleLocale"><template #icon><languages :size="17" /></template>{{ locale === 'zh-CN' ? 'EN' : '中文' }}</n-button>
        <n-button v-if="isAdmin" quaternary circle :title="t('logout')" @click="logout"><template #icon><log-out :size="18" /></template></n-button>
        <template v-else>
          <span class="guest-label">{{ t('guest') }}</span>
          <n-button quaternary circle :title="t('backToLogin')" @click="leaveGuest"><template #icon><log-out :size="18" /></template></n-button>
        </template>
      </div>
    </header>

    <main class="content">
      <n-alert v-if="error" type="error" closable :show-icon="false" @close="error = ''">{{ error }}</n-alert>
      <template v-if="activePage === 'downloads'">
        <section v-if="canCreateTask" class="submit-band">
          <n-input v-model:value="galleryUrls" type="textarea" :autosize="{ minRows: 3, maxRows: 10 }" :placeholder="`https://e-hentai.org/g/{gid}/{token}/\n${t('galleryUrls')}`" />
          <div class="submit-controls">
            <n-radio-group v-model:value="archiveType" :disabled="!isAdmin" name="archive-type">
              <n-radio-button value="resample">{{ t('resample') }}</n-radio-button>
              <n-radio-button value="original">{{ t('original') }}</n-radio-button>
            </n-radio-group>
            <n-button type="primary" :loading="submitting" :disabled="!galleryUrls.trim()" @click="createTask">{{ t('addTask') }}</n-button>
          </div>
        </section>
        <section class="table-section">
          <div class="section-heading"><div><h1>{{ t('tasks') }}</h1><span>{{ tasks.length }} {{ t('items') }}</span></div><n-button quaternary circle :title="t('refresh')" :loading="loading" @click="refreshAll"><template #icon><refresh-cw :size="18" /></template></n-button></div>
          <n-data-table :columns="taskColumns" :data="tasks" :loading="loading" :row-key="(row: Task) => row.id" :bordered="false" />
        </section>
      </template>

      <template v-else-if="isAdmin && settings">
        <section class="settings-section">
          <div class="section-heading"><div><h1>{{ t('accountPool') }}</h1><span>{{ t('cookieHidden') }}</span></div></div>
          <div class="account-import">
            <n-input v-model:value="accountName" :placeholder="t('accountName')" />
            <label class="file-picker"><upload :size="17" /><span>{{ cookieFile?.name || t('chooseCookies') }}</span><input type="file" accept=".txt,text/plain" @change="selectCookieFile" /></label>
            <n-button type="primary" :disabled="!accountName || !cookieFile" :loading="saving" @click="importAccount">{{ t('importAccount') }}</n-button>
          </div>
          <p class="settings-note">{{ t('cookieHelp') }}</p>
          <n-data-table :columns="accountColumns" :data="accounts" :row-key="(row: Account) => row.id" :bordered="false" />
        </section>
        <section class="settings-section">
          <div class="section-heading"><div><h1>{{ t('systemSettings') }}</h1><span>{{ t('workerReads') }}</span></div></div>
          <div class="settings-list">
            <div class="setting-row"><div><strong>{{ t('enableCache') }}</strong><span>{{ t('enableCacheDesc') }}</span></div><n-switch v-model:value="settings.cache_enabled" /></div>
            <div class="setting-row"><div><strong>{{ t('guestCacheAccess') }}</strong><span>{{ t('guestCacheAccessDesc') }}</span></div><n-switch v-model:value="settings.guest_cache_access" /></div>
            <div class="setting-row"><div><strong>{{ t('guestDownloads') }}</strong><span>{{ t('guestDownloadsDesc') }}</span></div><n-radio-group v-model:value="settings.guest_download_mode" size="small" :disabled="!settings.guest_cache_access"><n-radio-button value="disabled">{{ t('guestDisabled') }}</n-radio-button><n-radio-button value="resample">{{ t('resample') }}</n-radio-button><n-radio-button value="original">{{ t('original') }}</n-radio-button></n-radio-group></div>
            <div class="setting-row"><div><strong>{{ t('retention') }}</strong><span>{{ t('retentionDesc') }}</span></div><n-input-number v-model:value="settings.retention_days" :min="0" :max="3650" :disabled="!settings.cache_enabled" /></div>
            <div class="setting-row"><div><strong>{{ t('cacheLimit') }}</strong><span>{{ t('cacheLimitDesc') }}</span></div><n-input-number :value="Math.round(settings.cache_limit_bytes / 1024 ** 3)" :min="1" :max="10240" :disabled="!settings.cache_enabled" @update:value="(v) => v && (settings!.cache_limit_bytes = v * 1024 ** 3)" /></div>
            <div class="setting-row"><div><strong>{{ t('archiveLimit') }}</strong><span>{{ t('archiveLimitDesc') }}</span></div><n-input-number v-model:value="settings.max_archive_size_mb" :min="1" :max="1048576" /></div>
            <div class="setting-row"><div><strong>{{ t('concurrency') }}</strong><span>{{ t('concurrencyDesc') }}</span></div><n-input-number v-model:value="settings.worker_concurrency" :min="1" :max="4" /></div>
          </div>
          <div class="settings-actions"><n-button type="primary" :loading="saving" @click="saveSettings">{{ t('saveSettings') }}</n-button></div>
        </section>
        <section class="settings-section">
          <div class="section-heading"><div><h1>{{ t('apiToken') }}</h1><span>{{ t('tokenDesc') }}</span></div></div>
          <div class="settings-actions token-action"><n-button @click="rotateToken">{{ settings.api_token_configured ? t('rotateToken') : t('createToken') }}</n-button></div>
          <n-alert v-if="revealedToken" class="token-alert" type="warning" :title="t('shownOnce')"><span class="mono">{{ revealedToken }}</span></n-alert>
        </section>
      </template>
    </main>
  </div>
</template>
