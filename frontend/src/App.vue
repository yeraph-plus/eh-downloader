<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NDataTable, NInput, NInputNumber, NModal, NProgress, NRadioButton, NRadioGroup, NSwitch, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { BookOpen, ChartColumn, Download, ExternalLink, KeyRound, Languages, Lock, LogIn, LogOut, Plus, RefreshCw, Save, Settings as SettingsIcon, Upload, UserRound, X } from '@lucide/vue'
import { api, ApiError, clearCsrfToken, setCsrfToken } from './api'
import type { AccessMode, Account, ArchiveType, GuestDownloadMode, ServiceStats, SessionState, Settings, Task } from './types'

const copy = {
  adminLogin: ['管理员登录', 'Administrator sign in'], username: ['用户名', 'Username'], password: ['密码', 'Password'], login: ['登录', 'Sign in'],
  loginEntry: ['管理员登录', 'Administrator sign in'],
  guestAccess: ['游客访问', 'Continue as guest'],
  siteLogin: ['回主站登录', 'Sign in on the site'],
  gateSiteLoginHint: ['没有获取到登录凭据，请先：', 'No sign-in credentials detected. First:'],
  downloads: ['下载', 'Downloads'], settings: ['设置', 'Settings'], logout: ['退出', 'Sign out'],
  stats: ['统计', 'Stats'], statsDesc: ['服务运行与资源消耗概览（聚合值，不含账户信息）', 'Service health and resource usage overview (aggregates only, no account details)'],
  statWorker: ['Worker 状态', 'Worker status'], statWorkerDesc: ['独立下载进程的心跳（15 秒上报一次）', 'Heartbeat of the standalone download process (every 15s)'],
  statOnline: ['在线', 'Online'], statOffline: ['离线', 'Offline'],
  statQueue: ['队列 排队/进行中', 'Queue queued/active'], statQueueDesc: ['当前排队与处理中的任务数', 'Tasks currently queued and being processed'],
  statServed: ['累计交付', 'Downloads served'], statServedDesc: ['归档文件被成功下载的总次数', 'Total times an archive was delivered'],
  statTasks: ['任务 完成/失败', 'Tasks completed/failed'], statTasksDesc: ['当前存档中的完成与失败任务数', 'Completed and failed tasks currently on record'],
  statArchives: ['就绪归档', 'Archives ready'], statArchivesDesc: ['缓存中未过期的可用归档数', 'Unexpired archives available in the cache'],
  statTraffic: ['累计流量', 'Traffic pulled'], statTrafficDesc: ['从 EH 成功拉取的字节总数', 'Total bytes successfully pulled from EH'],
  statStorage: ['缓存占用', 'Cache usage'], statStorageDesc: ['缓存目录当前占用的磁盘空间', 'Disk space currently used by the cache directory'],
  statSpent: ['站点积分消耗', 'Site credits spent'], statSpentDesc: ['经站点账本累计扣除的积分', 'Total credits deducted through the site ledger'],
  statEhPool: ['EH 池 合用/总数 · GP · Credits', 'EH pool ready/total · GP · Credits'], statEhPoolDesc: ['账户池的聚合余额，不含任何账户明细', 'Aggregated balances of the account pool, no per-account details'],
  resample: ['重采样归档', 'Resample archive'], original: ['原始归档', 'Original archive'], addTask: ['添加任务', 'Add tasks'],
  galleryUrls: ['画廊链接，每行一个', 'Gallery URLs, one per line'],
  invalidGalleryUrl: ['画廊链接格式无效，仅支持 EH/ExHentai HTTPS 画廊地址', 'Invalid gallery URL. Only HTTPS EH/ExHentai gallery links are supported'],
  tasks: ['下载任务', 'Download tasks'], items: ['项', 'items'], refresh: ['刷新', 'Refresh'], downloadArchive: ['下载归档', 'Download archive'],
  loading: ['加载中…', 'Loading…'],
  file: ['文件', 'File'], size: ['大小', 'Size'], status: ['状态', 'Status'], info: ['信息', 'Details'],
  accountPool: ['账户池', 'Account pool'], cookieHidden: ['Cookie 不会回显', 'Cookies are never displayed'], accountName: ['账户名称', 'Account name'],
  chooseCookies: ['选择 cookies.txt', 'Choose cookies.txt'], importAccount: ['导入账户', 'Import account'],
  cookieHelp: ['使用标准 Netscape cookies.txt；可通过 Get cookies.txt LOCALLY 浏览器插件导出。', 'Import a standard Netscape cookies.txt file. You can export one with the Get cookies.txt LOCALLY browser extension.'],
  account: ['账户', 'Account'], unknown: ['未知', 'Unknown'], lastDownload: ['上次下载', 'Last download'], lastUsed: ['上次使用', 'Last used'], enabled: ['启用', 'Enabled'], deleteAccount: ['删除账户', 'Delete account'],
  systemSettings: ['系统设置', 'System settings'], workerReads: ['独立 Worker 会在下一轮读取新设置', 'The worker reads changes on its next cycle'],
  accessMode: ['访问模式', 'Access mode'],
  accessModeAdmin: ['仅管理员', 'Admin only'],
  accessModeGuest: ['开放游客', 'Open guest desk'],
  accessModeCore: ['AIYA CMS 线上集成', 'AIYA CMS integration'],
  accessModeDesc: ['admin：公开面关闭，仅管理员可用；guest：经典游客下载台（不扣费）；core：同样的游客行为，叠加前台站登录与积分扣费。', 'admin: the public desk is closed; guest: the classic free download desk; core: the same guest behavior with site sign-in and credit billing on top.'],
  accessModeDescNoCore: ['admin：公开面关闭，仅管理员可用；guest：经典游客下载台（不扣费）。', 'admin: the public desk is closed; guest: the classic free download desk.'],
  gateLoginHint: ['请先登录', 'Please sign in first'],
  integrationNotConfigured: ['站点集成未接通（AIYA_CORE_BASE_URL / AIYA_CORE_SERVICE_KEY 未配置），当前按开放游客模式运行。', 'The site integration is not configured (AIYA_CORE_BASE_URL / AIYA_CORE_SERVICE_KEY missing) — running as an open guest desk.'],
  coreSiteUrl: ['认证主站链接', 'Site URL'],
  coreSiteUrlDesc: ['用于「回主站登录」的跳转地址；留空则按钮不可用。', 'The jump target of the "Sign in on the site" button; leave empty to disable it.'],
  billingEndpoint: ['扣费接口', 'Billing endpoint'],
  billingEndpointDesc: ['站点侧需要暴露的积分扣费端点，供本服务在创建/下载时调用。', 'The credit-spend endpoint the site exposes; called on creation and download.'],
  apiDocs: ['接口文档', 'API docs'],
  apiDocsDesc: ['本服务的 OpenAPI 文档（Swagger UI），始终可用。', 'The OpenAPI docs of this service (Swagger UI), always available.'],
  guestDownloads: ['游客创建下载', 'Guest downloads'], guestDownloadsDesc: ['所有游客共享一个公共身份；此项仅控制新任务类型。', 'All guests share one public identity. This controls only the type of new tasks.'],
  guestDisabled: ['完全禁止', 'Disabled'],
  enableCache: ['启用本地缓存', 'Local cache'], enableCacheDesc: ['开启时下载并校验 ZIP 后保存；关闭时不落盘，由 API 实时中转 EH 下载。', 'When enabled, verified ZIP files are stored locally. When disabled, the API relays each EH download without storing it.'],
  retention: ['缓存保留天数', 'Cache retention'], retentionDesc: ['归档完成后保留的天数；设为 0 时永久保留。', 'Days to retain completed archives. Set to 0 to keep them permanently.'],
  cacheLimit: ['缓存上限 GB', 'Cache limit (GB)'], cacheLimitDesc: ['缓存文件、下载中的临时文件和容量预留共享此上限；设为 0 时不限制容量。', 'Cached files, active temporary downloads, and capacity reservations share this limit. Set to 0 for no limit.'],
  archiveLimit: ['最大归档大小 MB', 'Maximum archive size (MB)'], archiveLimitDesc: ['在向 EH 提交归档请求前按页面估算大小检查；设为 0 时不限制大小。', 'Checked against the page estimate before submitting the archive request to EH. Set to 0 for no limit.'],
  concurrency: ['下载并发', 'Download concurrency'], concurrencyDesc: ['单 Worker 内同时处理的下载数量，允许 1-4。', 'Concurrent downloads in the single worker. Allowed range: 1-4.'],
  taskMaxRetries: ['任务最大重试次数', 'Max retries per task'], taskMaxRetriesDesc: ['可重试的网络类错误在失败前的最大尝试次数，0 为不重试。', 'How many times a retryable network error may be retried before the task fails. 0 disables retries.'],
  ehProxy: ['EH 代理', 'EH proxy'], ehProxyDesc: ['EH 请求使用的 HTTP/SOCKS 代理（http:// 或 socks5://），留空直连。', 'HTTP/SOCKS proxy (http:// or socks5://) for EH requests. Leave empty for direct access.'],
  priceCreateOriginal: ['原始归档创建单价', 'Create price (original)'],
  priceCreateResample: ['重采样归档创建单价', 'Create price (resample)'],
  priceDownloadOriginal: ['原始归档下载单价', 'Download price (original)'],
  priceDownloadResample: ['重采样归档下载单价', 'Download price (resample)'],
  priceUnit: ['站点积分', 'site credits'],
  priceTitle: ['下载会消耗你的站点积分，当前单价：', 'Downloads consume your site credits. Current unit prices:'],
  priceOriginalCreate: ['原始归档创建任务', 'Original archive creation'],
  priceResampleCreate: ['重采样归档创建任务', 'Resample archive creation'],
  priceOriginalDownload: ['原始归档下载', 'Original archive download'],
  priceResampleDownload: ['重采样归档下载', 'Resample archive download'],
  pricePerUse: ['积分/次', 'credits/use'],
  saveSettings: ['保存设置', 'Save settings'], saved: ['设置已保存', 'Settings saved'], queued: ['任务已进入队列', 'Task added to queue'], accountImported: ['账户已加入账户池', 'Account added'],
  apiToken: ['API远程调用', 'Remote API'], tokenDesc: ['管理员权限；轮换后旧 Token 立即失效', 'Administrator access; rotating immediately revokes the previous token'],
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
const role = ref<'admin' | 'guest'>('guest')
const guestEntered = ref(false)
const activePage = ref<'downloads' | 'stats' | 'settings'>('downloads')
const error = ref('')
const username = ref('')
const password = ref('')
const loginLoading = ref(false)
const showLoginModal = ref(false)
const galleryUrls = ref('')
const archiveType = ref<ArchiveType>('resample')
const guestDownloadMode = ref<GuestDownloadMode>('disabled')
const accessMode = ref<AccessMode>('admin')
const siteIdentity = ref(false)
const prices = ref<SessionState['prices']>({ create_original: 0, create_resample: 0, download_original: 0, download_resample: 0 })
const coreConfigured = ref(false)
const coreLoginUrl = ref<string | null>(null)
const submitting = ref(false)
const loading = ref(false)
const tasks = ref<Task[]>([])
const accounts = ref<Account[]>([])
const settings = ref<Settings | null>(null)
const stats = ref<ServiceStats | null>(null)
const statsLoading = ref(false)
const accountName = ref('')
const cookieFile = ref<File | null>(null)
const saving = ref(false)
const revealedToken = ref('')
let pollTimer: number | undefined

const isAdmin = computed(() => role.value === 'admin')
const canCreateTask = computed(() =>
  isAdmin.value ||
  (accessMode.value !== 'admin' && guestDownloadMode.value !== 'disabled' && (accessMode.value !== 'core' || siteIdentity.value)),
)
const integrationDegraded = computed(() => settings.value?.access_mode === 'core' && !coreConfigured.value)
/** 线上集成模式的计价横幅：付费项橙色徽章、免费项绿色徽章（0 积分）。 */
function priceBadge(value: number): string {
  return value > 0 ? `${value} ${t('pricePerUse')}` : t('free')
}
function priceBadgeType(value: number): 'warning' | 'success' {
  return value > 0 ? 'warning' : 'success'
}
const apiDocsUrl = computed(() => {
  const pathname = window.location.pathname
  const base = pathname.endsWith('/') ? pathname : pathname + '/'
  return base + 'docs'
})

/** 前置门禁：加载态之后、应用壳之前单独执行。admin 模式强制登录（首页
 *  不加载）；core 模式先做 cookie 状态检测，未登录（含封禁）挡在门禁显示
 *  「回主站登录」；guest 模式点「游客访问」进入；管理员会话始终直通。 */
const showGate = computed(() => {
  if (!ready.value || isAdmin.value) return false
  // guest 模式直接进主页；admin 模式强制登录（首页不加载）；core 模式
  // 先做 cookie 检测，未登录挡在门禁显示「回主站登录」。
  if (accessMode.value === 'guest') return false
  if (accessMode.value === 'admin') return true
  return !siteIdentity.value
})
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
  const units = ['KB', 'MB', 'GB', 'TB']
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

function applySession(state: SessionState) {
  role.value = state.admin ? 'admin' : 'guest'
  accessMode.value = state.mode
  guestDownloadMode.value = state.guest_download_mode
  siteIdentity.value = state.site_identity
  coreConfigured.value = state.core_configured
  coreLoginUrl.value = state.core_login_url
  prices.value = state.prices
  if (state.csrf_token) setCsrfToken(state.csrf_token)
  syncArchiveType()
}

function syncPolling() {
  // 门禁挡在前面时不打任务列表接口（admin 模式下它是 401）。
  if (ready.value && !showGate.value) startPolling()
  else stopPolling()
}

async function initialize() {
  try {
    const state = await api<SessionState>('/api/v1/auth/session')
    applySession(state)
    syncArchiveType()
    if (isAdmin.value) {
      const [rows, current] = await Promise.all([api<Account[]>('/api/v1/accounts'), api<Settings>('/api/v1/settings')])
      accounts.value = rows; settings.value = current
    } else {
      await refreshTasks()
    }
  } catch (cause) { error.value = (cause as Error).message } finally {
    ready.value = true
    syncPolling()
  }
}

/** 非 admin 的归档类型跟随访问模式锁（档位配置为 original 时不同步则每次提交都 403）。 */
function syncArchiveType() {
  if (!isAdmin.value && guestDownloadMode.value !== 'disabled') {
    archiveType.value = guestDownloadMode.value
  }
}

async function refreshTasks() {
  try { tasks.value = await api<Task[]>('/api/v1/tasks') }
  catch (cause) { if (!(cause instanceof ApiError && (cause.status === 401 || cause.status === 403))) error.value = (cause as Error).message }
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

function enterGuest() {
  guestEntered.value = true
  refreshTasks()
  syncPolling()
}

async function login() {
  loginLoading.value = true; error.value = ''
  try {
    const result = await api<{ username: string; csrf_token: string }>('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) })
    setCsrfToken(result.csrf_token); role.value = 'admin'; password.value = ''; showLoginModal.value = false
    accessMode.value = 'admin'
    guestEntered.value = true
    const [rows, current] = await Promise.all([api<Account[]>('/api/v1/accounts'), api<Settings>('/api/v1/settings')])
    accounts.value = rows; settings.value = current
    syncPolling()
  } catch (cause) { error.value = (cause as Error).message } finally { loginLoading.value = false }
}

async function logout() {
  try { await api('/api/v1/auth/logout', { method: 'POST' }) } finally {
    clearCsrfToken(); tasks.value = []; settings.value = null; accounts.value = []
    activePage.value = 'downloads'
    guestEntered.value = false
    const state = await api<SessionState>('/api/v1/auth/session')
    applySession(state)
    syncPolling()
  }
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
async function refreshStats() {
  statsLoading.value = true
  try {
    stats.value = await api<ServiceStats>('/api/v1/stats')
  } catch (cause) {
    if (!(cause instanceof ApiError && (cause.status === 401 || cause.status === 403))) error.value = (cause as Error).message
  } finally { statsLoading.value = false }
}
async function saveSettings() { if (!settings.value) return; saving.value = true; try { settings.value = await api<Settings>('/api/v1/settings', { method: 'PUT', body: JSON.stringify(settings.value) }); message.success(t('saved')) } catch (cause) { error.value = (cause as Error).message } finally { saving.value = false } }
async function rotateToken() { const result = await api<{ token: string }>('/api/v1/settings/api-token/rotate', { method: 'POST' }); revealedToken.value = result.token }
function startPolling() { stopPolling(); pollTimer = window.setInterval(refreshTasks, 3000) }
function stopPolling() { if (pollTimer) window.clearInterval(pollTimer); pollTimer = undefined }
onMounted(initialize)
onBeforeUnmount(stopPolling)

// 门禁恢复时设置入口一并隐藏：非管理员回到下载页
watch(isAdmin, (value) => { if (!value) activePage.value = 'downloads' })
</script>

<template>
  <div v-if="!ready" class="loading-screen">Eh Downloader</div>

  <!-- 前置门禁：全屏居中，按访问模式切换内容 -->
  <main v-else-if="showGate" class="login-shell">
    <div class="login-topbar">
      <n-button quaternary circle :title="t('loginEntry')" @click="showLoginModal = true"><template #icon><log-in :size="18" /></template></n-button>
      <n-button class="login-language" quaternary :title="t('language')" @click="toggleLocale"><template #icon><languages :size="17" /></template>{{ locale === 'zh-CN' ? 'EN' : '中文' }}</n-button>
    </div>
    <section class="login-panel">
      <div class="brand-lockup"><span class="brand-mark">E</span><span>Eh Downloader</span></div>
      <!-- 三模式互斥：只渲染当前模式对应的按钮/提示，管理员经右上角图标弹窗登录 -->
      <div v-if="accessMode === 'admin'" class="gate-hint"><lock :size="15" />{{ t('gateLoginHint') }}</div>
      <template v-else-if="accessMode === 'core'">
        <div class="gate-hint gate-hint-left">{{ t('gateSiteLoginHint') }}</div>
        <n-button tag="a" :href="coreLoginUrl || undefined" :disabled="!coreLoginUrl" secondary attr-type="button" block><template #icon><external-link :size="16" /></template>{{ t('siteLogin') }}</n-button>
      </template>
      <n-button v-else-if="accessMode === 'guest'" secondary attr-type="button" block @click="enterGuest"><template #icon><user-round :size="16" /></template>{{ t('guestAccess') }}</n-button>
    </section>
  </main>

  <div v-else class="app-shell">
    <header class="topbar">
      <div class="brand-lockup"><span class="brand-mark">E</span><span>Eh Downloader</span></div>
      <nav>
        <button :class="{ active: activePage === 'downloads' }" @click="activePage = 'downloads'">{{ t('downloads') }}</button>
        <button :class="{ active: activePage === 'stats' }" @click="activePage = 'stats'; refreshStats()"><chart-column :size="16" />{{ t('stats') }}</button>
        <button v-if="isAdmin" :class="{ active: activePage === 'settings' }" @click="activePage = 'settings'"><settings-icon :size="16" />{{ t('settings') }}</button>
      </nav>
      <div class="top-actions">
        <n-button quaternary size="small" :title="t('language')" @click="toggleLocale"><template #icon><languages :size="17" /></template>{{ locale === 'zh-CN' ? 'EN' : '中文' }}</n-button>
        <n-button v-if="isAdmin" quaternary circle :title="t('logout')" @click="logout"><template #icon><log-out :size="18" /></template></n-button>
        <n-button v-else quaternary circle :title="t('loginEntry')" @click="showLoginModal = true"><template #icon><log-in :size="18" /></template></n-button>
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
            <n-button type="primary" :loading="submitting" :disabled="!galleryUrls.trim()" @click="createTask"><template #icon><plus :size="16" /></template>{{ t('addTask') }}</n-button>
          </div>
        </section>
        <div v-if="accessMode === 'core'" class="price-banner">
          <div class="price-title">{{ t('priceTitle') }}</div>
          <ul class="price-list">
            <li><span>{{ t('priceOriginalCreate') }}</span><n-tag :type="priceBadgeType(prices.create_original)" size="small" :bordered="false">{{ priceBadge(prices.create_original) }}</n-tag></li>
            <li><span>{{ t('priceResampleCreate') }}</span><n-tag :type="priceBadgeType(prices.create_resample)" size="small" :bordered="false">{{ priceBadge(prices.create_resample) }}</n-tag></li>
            <li><span>{{ t('priceOriginalDownload') }}</span><n-tag :type="priceBadgeType(prices.download_original)" size="small" :bordered="false">{{ priceBadge(prices.download_original) }}</n-tag></li>
            <li><span>{{ t('priceResampleDownload') }}</span><n-tag :type="priceBadgeType(prices.download_resample)" size="small" :bordered="false">{{ priceBadge(prices.download_resample) }}</n-tag></li>
          </ul>
        </div>
        <section class="table-section">
          <div class="section-heading"><div><h1>{{ t('tasks') }}</h1><span>{{ tasks.length }} {{ t('items') }}</span></div><n-button quaternary circle :title="t('refresh')" :loading="loading" @click="refreshAll"><template #icon><refresh-cw :size="18" /></template></n-button></div>
          <n-data-table :columns="taskColumns" :data="tasks" :loading="loading" :row-key="(row: Task) => row.id" :bordered="false" />
        </section>
      </template>

      <template v-else-if="activePage === 'stats'">
        <section class="table-section">
          <div class="section-heading"><div><h1>{{ t('stats') }}</h1><span>{{ t('statsDesc') }}</span></div><n-button quaternary circle :title="t('refresh')" :loading="statsLoading" @click="refreshStats"><template #icon><refresh-cw :size="18" /></template></n-button></div>
          <div class="settings-list" v-if="stats">
            <div class="setting-row"><div><strong>{{ t('statWorker') }}</strong><span>{{ t('statWorkerDesc') }}</span></div><span :class="stats.worker_alive ? 'stat-ok' : 'stat-bad'">{{ stats.worker_alive ? t('statOnline') : t('statOffline') }}</span></div>
            <div class="setting-row"><div><strong>{{ t('statQueue') }}</strong><span>{{ t('statQueueDesc') }}</span></div><span>{{ stats.queue.queued }} / {{ stats.queue.active }}</span></div>
            <div class="setting-row"><div><strong>{{ t('statServed') }}</strong><span>{{ t('statServedDesc') }}</span></div><span>{{ stats.downloads.served_total }}</span></div>
            <div class="setting-row"><div><strong>{{ t('statTasks') }}</strong><span>{{ t('statTasksDesc') }}</span></div><span>{{ stats.downloads.tasks_completed }} / {{ stats.downloads.tasks_failed }}</span></div>
            <div class="setting-row"><div><strong>{{ t('statArchives') }}</strong><span>{{ t('statArchivesDesc') }}</span></div><span>{{ stats.downloads.archives_ready }}</span></div>
            <div class="setting-row"><div><strong>{{ t('statTraffic') }}</strong><span>{{ t('statTrafficDesc') }}</span></div><span>{{ formatBytes(stats.traffic.bytes_downloaded_total) }}</span></div>
            <div class="setting-row"><div><strong>{{ t('statStorage') }}</strong><span>{{ t('statStorageDesc') }}</span></div><span>{{ formatBytes(stats.traffic.cache_used_bytes) }}</span></div>
            <div class="setting-row"><div><strong>{{ t('statSpent') }}</strong><span>{{ t('statSpentDesc') }}</span></div><span>{{ stats.credits.total_spent }}</span></div>
            <div class="setting-row"><div><strong>{{ t('statEhPool') }}</strong><span>{{ t('statEhPoolDesc') }}</span></div><span>{{ stats.eh_pool.accounts_ready }}/{{ stats.eh_pool.accounts_total }} · GP {{ stats.eh_pool.gp ?? '—' }} · {{ stats.eh_pool.credits ?? '—' }}</span></div>
          </div>
          <div v-else class="loading-screen">{{ t('loading') }}</div>
        </section>
      </template>

      <template v-else-if="activePage === 'settings' && isAdmin && settings">

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
            <div class="setting-row"><div><strong>{{ t('retention') }}</strong><span>{{ t('retentionDesc') }}</span></div><n-input-number v-model:value="settings.retention_days" :min="0" :max="3650" :disabled="!settings.cache_enabled" /></div>
            <div class="setting-row"><div><strong>{{ t('cacheLimit') }}</strong><span>{{ t('cacheLimitDesc') }}</span></div><n-input-number :value="Math.round(settings.cache_limit_bytes / 1024 ** 3)" :min="0" :max="10240" :disabled="!settings.cache_enabled" @update:value="(v) => v !== null && (settings!.cache_limit_bytes = v * 1024 ** 3)" /></div>
            <div class="setting-row"><div><strong>{{ t('archiveLimit') }}</strong><span>{{ t('archiveLimitDesc') }}</span></div><n-input-number v-model:value="settings.max_archive_size_mb" :min="0" :max="1048576" /></div>
            <div class="setting-row"><div><strong>{{ t('concurrency') }}</strong><span>{{ t('concurrencyDesc') }}</span></div><n-input-number v-model:value="settings.worker_concurrency" :min="1" :max="4" /></div>
            <div class="setting-row"><div><strong>{{ t('taskMaxRetries') }}</strong><span>{{ t('taskMaxRetriesDesc') }}</span></div><n-input-number v-model:value="settings.task_max_retries" :min="0" :max="5" /></div>
            <div class="setting-row"><div><strong>{{ t('ehProxy') }}</strong><span>{{ t('ehProxyDesc') }}</span></div><n-input v-model:value="settings.eh_proxy_url" placeholder="http:// 或 socks5://" :maxlength="255" /></div>
          </div>
          <div class="settings-actions"><n-button type="primary" :loading="saving" @click="saveSettings"><template #icon><save :size="16" /></template>{{ t('saveSettings') }}</n-button></div>
        </section>

        <section class="settings-section">
          <div class="section-heading"><div><h1>{{ t('apiToken') }}</h1><span>{{ t('tokenDesc') }}</span></div></div>
          <div class="settings-list">
            <div class="setting-row"><div><strong>{{ t('apiDocs') }}</strong><span>{{ t('apiDocsDesc') }}</span></div><n-button tag="a" :href="apiDocsUrl" target="_blank" rel="noreferrer" secondary><template #icon><book-open :size="16" /></template>{{ t('apiDocs') }}</n-button></div>
          </div>
          <div class="settings-actions token-action"><n-button @click="rotateToken"><template #icon><key-round :size="16" /></template>{{ settings.api_token_configured ? t('rotateToken') : t('createToken') }}</n-button></div>
          <n-alert v-if="revealedToken" class="token-alert" type="warning" :title="t('shownOnce')"><span class="mono">{{ revealedToken }}</span></n-alert>
        </section>

        <section class="settings-section">
          <div class="section-heading"><div><h1>{{ t('accessMode') }}</h1><span>{{ settings.core_enabled ? t('accessModeDesc') : t('accessModeDescNoCore') }}</span></div></div>
          <n-alert v-if="integrationDegraded" type="warning" :show-icon="false">{{ t('integrationNotConfigured') }}</n-alert>
          <div class="settings-list">
            <div class="setting-row"><div><strong>{{ t('accessMode') }}</strong></div><n-radio-group v-model:value="settings.access_mode" name="access-mode"><n-radio-button value="admin">{{ t('accessModeAdmin') }}</n-radio-button><n-radio-button value="guest">{{ t('accessModeGuest') }}</n-radio-button><n-radio-button v-if="settings.core_enabled" value="core">{{ t('accessModeCore') }}</n-radio-button></n-radio-group></div>
            <template v-if="settings.core_enabled">
              <div class="setting-row"><div><strong>{{ t('coreSiteUrl') }}</strong><span>{{ t('coreSiteUrlDesc') }}</span></div><n-input v-model:value="settings.core_site_url" placeholder="https://" :maxlength="255" :disabled="settings.access_mode !== 'core'" /></div>
              <div class="setting-row"><div><strong>{{ t('billingEndpoint') }}</strong><span>{{ t('billingEndpointDesc') }}</span></div><span class="mono" style="word-break: break-all;">{{ settings.core_base_url ? settings.core_base_url + '/wp-json/aiya/integrations/v1/credits/spend' : '—' }}</span></div>
            </template>
            <div class="setting-row"><div><strong>{{ t('guestDownloads') }}</strong><span>{{ t('guestDownloadsDesc') }}</span></div><n-radio-group v-model:value="settings.guest_download_mode" size="small" :disabled="settings.access_mode === 'admin'"><n-radio-button value="disabled">{{ t('guestDisabled') }}</n-radio-button><n-radio-button value="resample">{{ t('resample') }}</n-radio-button><n-radio-button value="original">{{ t('original') }}</n-radio-button></n-radio-group></div>
            <template v-if="settings.core_enabled">
              <div class="setting-row"><div><strong>{{ t('priceCreateOriginal') }}</strong><span>{{ t('priceUnit') }}</span></div><n-input-number v-model:value="settings.price_create_original" :min="0" :max="1000000" /></div>
              <div class="setting-row"><div><strong>{{ t('priceCreateResample') }}</strong><span>{{ t('priceUnit') }}</span></div><n-input-number v-model:value="settings.price_create_resample" :min="0" :max="1000000" /></div>
              <div class="setting-row"><div><strong>{{ t('priceDownloadOriginal') }}</strong><span>{{ t('priceUnit') }}</span></div><n-input-number v-model:value="settings.price_download_original" :min="0" :max="1000000" /></div>
              <div class="setting-row"><div><strong>{{ t('priceDownloadResample') }}</strong><span>{{ t('priceUnit') }}</span></div><n-input-number v-model:value="settings.price_download_resample" :min="0" :max="1000000" /></div>
            </template>
          </div>
          <div class="settings-actions"><n-button type="primary" :loading="saving" @click="saveSettings"><template #icon><save :size="16" /></template>{{ t('saveSettings') }}</n-button></div>
        </section>
      </template>
    </main>

  </div>

  <!-- 登录模态框挂模板根级：门禁页与应用主页的右上角图标共用 -->
  <n-modal v-model:show="showLoginModal">
    <div class="login-panel">
      <div class="brand-lockup"><span class="brand-mark"><log-in :size="15" /></span><span>{{ t('adminLogin') }}</span></div>
      <form class="login-form" @submit.prevent="login">
        <label>{{ t('username') }}<n-input v-model:value="username" autocomplete="username" /></label>
        <label>{{ t('password') }}<n-input v-model:value="password" type="password" show-password-on="click" autocomplete="current-password" /></label>
        <n-alert v-if="error" type="error" :show-icon="false">{{ error }}</n-alert>
        <n-button type="primary" attr-type="submit" :loading="loginLoading" block><template #icon><log-in :size="16" /></template>{{ t('login') }}</n-button>
      </form>
    </div>
  </n-modal>
</template>
