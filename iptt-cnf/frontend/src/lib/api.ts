/** Typed API client.
 *
 *  Everything goes through one function so credentials, the CSRF header and
 *  error shape are handled in exactly one place. The legacy frontend scattered
 *  raw `fetch` calls across 6,000 lines of inline script and surfaced failures
 *  with `alert()`.
 */
import type {
  CircleCount,
  ImportReport,
  ExecutionGrid,
  ExecutionUpdate,
  GovernanceMatrix,
  Heatmap,
  Kpis,
  NodeRow,
  Programme,
  Project,
  Session,
  Stage,
} from './types';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  get isAuth() {
    return this.status === 401;
  }
  get isForbidden() {
    return this.status === 403;
  }
}

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
  if (typeof window !== 'undefined') {
    try {
      if (token) window.sessionStorage.setItem('iptt_csrf', token);
      else window.sessionStorage.removeItem('iptt_csrf');
    } catch {
      /* private mode - the token still lives in memory for this tab */
    }
  }
}

export function loadCsrfToken(): string | null {
  if (csrfToken) return csrfToken;
  if (typeof window !== 'undefined') {
    try {
      csrfToken = window.sessionStorage.getItem('iptt_csrf');
    } catch {
      csrfToken = null;
    }
  }
  return csrfToken;
}

function describe(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) =>
        typeof d === 'object' && d !== null && 'message' in d
          ? `${(d as { field?: string }).field ?? 'value'}: ${(d as { message: string }).message}`
          : String(d),
      )
      .join('; ');
  }
  return 'Request failed';
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');

  if (method !== 'GET' && method !== 'HEAD') {
    const token = loadCsrfToken();
    if (token) headers.set('x-csrf-token', token);
  }

  const response = await fetch(path, {
    ...init,
    headers,
    credentials: 'include',
    cache: 'no-store',
  });

  if (response.status === 204) return undefined as T;

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(
      response.status,
      describe((payload as { detail?: unknown }).detail),
      (payload as { request_id?: string }).request_id,
    );
  }
  return payload as T;
}

export const api = {
  // --- auth ---------------------------------------------------------------
  async login(username: string, password: string): Promise<Session> {
    const session = await request<Session>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    setCsrfToken(session.csrf_token);
    return session;
  },

  async logout(): Promise<void> {
    await request<void>('/api/auth/logout', { method: 'POST' });
    setCsrfToken(null);
  },

  async me(): Promise<Session> {
    const session = await request<Session>('/api/auth/me');
    if (session.csrf_token) setCsrfToken(session.csrf_token);
    return session;
  },

  changePassword(current_password: string, new_password: string): Promise<void> {
    return request<void>('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    });
  },

  // --- portfolio ----------------------------------------------------------
  programmes: () => request<Programme[]>('/api/programmes'),
  projects: (programmeId?: number) =>
    request<Project[]>(
      programmeId ? `/api/projects?programme_id=${programmeId}` : '/api/projects',
    ),
  project: (id: number) => request<Project>(`/api/projects/${id}`),
  circles: (id: number) =>
    request<{ circles: CircleCount[] }>(`/api/projects/${id}/circles`),

  // --- reporting ----------------------------------------------------------
  kpis: (id: number) => request<Kpis>(`/api/reporting/projects/${id}/kpis`),
  nodes: (id: number) =>
    request<{ nodes: NodeRow[] }>(`/api/reporting/projects/${id}/nodes`),
  matrix: (id: number) =>
    request<GovernanceMatrix>(`/api/reporting/projects/${id}/governance-matrix`),
  heatmap: (id: number) =>
    request<Heatmap>(`/api/reporting/projects/${id}/delay-heatmap`),
  stages: () => request<{ stages: Stage[] }>('/api/reporting/stages'),

  // --- execution ----------------------------------------------------------
  grid: (id: number, page = 1, pageSize = 10, circle?: string) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (circle) params.set('circle', circle);
    return request<ExecutionGrid>(
      `/api/execution/projects/${id}/grid?${params}`,
    );
  },

  /** Streams the workbook straight to a download. */
  async exportWorkbook(id: number, circle?: string): Promise<void> {
    const path = circle
      ? `/api/execution/projects/${id}/export?circle=${encodeURIComponent(circle)}`
      : `/api/execution/projects/${id}/export`;
    const response = await fetch(path, { credentials: 'include', cache: 'no-store' });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new ApiError(response.status, describe((payload as { detail?: unknown }).detail));
    }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') ?? '';
    const match = /filename="?([^";]+)"?/.exec(disposition);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = match?.[1] ?? `iptt-execution-${id}.xlsx`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },

  async importWorkbook(
    id: number,
    file: File,
    dryRun: boolean,
  ): Promise<ImportReport> {
    const form = new FormData();
    form.append('file', file);
    const token = loadCsrfToken();
    const response = await fetch(
      `/api/execution/projects/${id}/import?dry_run=${dryRun}`,
      {
        method: 'POST',
        body: form,
        credentials: 'include',
        headers: token ? { 'x-csrf-token': token } : undefined,
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      // A 422 carries the full report, which is what the user needs to see.
      const detail = (payload as { detail?: unknown }).detail;
      if (detail && typeof detail === 'object' && 'errors' in detail) {
        return detail as ImportReport;
      }
      throw new ApiError(response.status, describe(detail));
    }
    return payload as ImportReport;
  },

  bulkUpdate: (updates: ExecutionUpdate[]) =>
    request<{ applied: number }>('/api/execution/bulk-update', {
      method: 'PUT',
      body: JSON.stringify({ updates }),
    }),
};
