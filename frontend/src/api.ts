let csrfToken = sessionStorage.getItem('csrf-token') || ''

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// HTTPException answers a string detail; request-validation errors (422)
// answer a list of {loc, msg} objects instead — flatten that into readable
// text so callers never surface "[object Object]".
export function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const entry = (item ?? {}) as { loc?: unknown[]; msg?: unknown }
        const path = Array.isArray(entry.loc) ? entry.loc.filter((part) => part !== 'body').join('.') : ''
        return typeof entry.msg === 'string' ? (path ? `${path}: ${entry.msg}` : entry.msg) : ''
      })
      .filter(Boolean)
      .join('; ')
  }
  return ''
}

export function setCsrfToken(value: string) {
  csrfToken = value
  sessionStorage.setItem('csrf-token', value)
}

export function clearCsrfToken() {
  csrfToken = ''
  sessionStorage.removeItem('csrf-token')
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (init.method && !['GET', 'HEAD', 'OPTIONS'].includes(init.method.toUpperCase()) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(path, { ...init, headers, credentials: 'include' })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const payload = await response.json()
      const text = detailText(payload.detail)
      if (text) message = text
    } catch {
      // Keep the HTTP fallback message.
    }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
