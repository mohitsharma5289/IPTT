/** Typed API client.
 *
 *  Everything goes through one function so credentials, the CSRF header and
 *  error shape are handled in exactly one place. The legacy frontend scattered
 *  raw `fetch` calls across 6,000 lines of inline script and surfaced failures
 *  with `alert()`.
 */
import type {
  ActionInput,
  CircleDetail,
  CircleIntelligenceRow,
  FacilityDetail,
  GovernanceRow,
  NodeDetail,
  ProjectForecast,
  BaselineHistoryEntry,
  BaselineReadiness,
  BaselineResult,
  TemplateRowWrite,
  AdminUser,
  AuditPage,
  CircleCount,
  CircleRollup,
  ImportReport,
  LeadershipAction,
  ProgrammeRollup,
  Role,
  ScopeRow,
  SheetImportSummary,
  TemplateRow,
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

/** Streams a file response to a browser download. */
async function download(path: string, fallbackName: string): Promise<void> {
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
  link.download = match?.[1] ?? fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Posts one file. A 422 carries the validation report, which the caller shows. */
async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append('file', file);
  const token = loadCsrfToken();
  const response = await fetch(path, {
    method: 'POST',
    body: form,
    credentials: 'include',
    headers: token ? { 'x-csrf-token': token } : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (payload as { detail?: unknown }).detail;
    if (detail && typeof detail === 'object' && 'errors' in detail) return detail as T;
    throw new ApiError(response.status, describe(detail));
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

  // --- portfolio-wide reports ("Quick Reports") ---------------------------
  governance: () =>
    request<{ programmes: GovernanceRow[]; totals: Record<string, number> }>(
      '/api/reporting/governance',
    ),
  circleIntelligence: () =>
    request<{ circles: CircleIntelligenceRow[]; totals: Record<string, number> }>(
      '/api/reporting/circles',
    ),
  forecast: (projectId: number) =>
    request<ProjectForecast>(`/api/reporting/projects/${projectId}/forecast`),

  // --- drill-downs --------------------------------------------------------
  circleDetail: (projectId: number, circle: string) =>
    request<CircleDetail>(
      `/api/reporting/projects/${projectId}/circles/${encodeURIComponent(circle)}`,
    ),
  facilityDetail: (projectId: number, facility: string) =>
    request<FacilityDetail>(
      `/api/reporting/projects/${projectId}/facilities/${encodeURIComponent(facility)}`,
    ),
  nodeDetail: (scopeId: number) => request<NodeDetail>(`/api/reporting/nodes/${scopeId}`),

  // --- self-registration --------------------------------------------------
  register: (username: string, password: string) =>
    request<{ status: string; detail: string }>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  pendingUsers: () => request<AdminUser[]>('/api/users/pending'),
  approveUser: (id: number, role: string) =>
    request<AdminUser>(`/api/users/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ role }),
    }),
  rejectUser: (id: number) =>
    request<void>(`/api/users/${id}/approve`, { method: 'DELETE' }),

  // --- programme lifecycle ------------------------------------------------
  createProgramme: (body: { name: string; status?: string }) =>
    request<Programme>('/api/programmes', { method: 'POST', body: JSON.stringify(body) }),
  updateProgramme: (id: number, body: { name?: string; status?: string }) =>
    request<Programme>(`/api/programmes/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteProgramme: (id: number) =>
    request<void>(`/api/programmes/${id}`, { method: 'DELETE' }),

  // --- project lifecycle --------------------------------------------------
  createProject: (body: {
    programme_id: number;
    name: string;
    status?: string;
    project_start_date?: string | null;
  }) => request<Project>('/api/projects', { method: 'POST', body: JSON.stringify(body) }),
  updateProject: (
    id: number,
    body: {
      name?: string;
      status?: string;
      project_start_date?: string | null;
      programme_id?: number;
    },
  ) => request<Project>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteProject: (id: number, force = false) =>
    request<void>(`/api/projects/${id}${force ? '?force=true' : ''}`, { method: 'DELETE' }),

  // --- planning -----------------------------------------------------------
  // The plan is generated automatically while no fieldwork has been recorded.
  // Once an actual start exists the project locks and re-baselining - which
  // archives the current state first - is the only way to replan.
  baselineReadiness: (id: number) =>
    request<BaselineReadiness>(`/api/projects/${id}/baseline-readiness`),
  baseline: (id: number, body: { kickoff_date?: string | null; reason?: string } = {}) =>
    request<BaselineResult>(`/api/projects/${id}/baseline`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  rebaseline: (id: number, body: { kickoff_date?: string | null; reason: string }) =>
    request<BaselineResult>(`/api/projects/${id}/rebaseline`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  baselineHistory: (id: number) =>
    request<{ project_id: number; baselines: BaselineHistoryEntry[] }>(
      `/api/projects/${id}/baseline-history`,
    ),

  // --- task template rows -------------------------------------------------
  addTemplateRow: (projectId: number, body: TemplateRowWrite) =>
    request<TemplateRow>(`/api/projects/${projectId}/template`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateTemplateRow: (projectId: number, number: number, body: Partial<TemplateRowWrite>) =>
    request<TemplateRow>(`/api/projects/${projectId}/template/${number}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteTemplateRow: (projectId: number, number: number, force = false) =>
    request<void>(
      `/api/projects/${projectId}/template/${number}${force ? '?force=true' : ''}`,
      { method: 'DELETE' },
    ),

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

  exportWorkbook: (id: number, circle?: string) =>
    download(
      circle
        ? `/api/execution/projects/${id}/export?circle=${encodeURIComponent(circle)}`
        : `/api/execution/projects/${id}/export`,
      `iptt-execution-${id}.xlsx`,
    ),

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

  // --- scope ---------------------------------------------------------------
  scope: (id: number) => request<ScopeRow[]>(`/api/projects/${id}/scope`),
  addScope: (id: number, body: Omit<ScopeRow, 'id' | 'status' | 'task_count' | 'has_execution_data'>) =>
    request<ScopeRow>(`/api/projects/${id}/scope`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  deleteScope: (id: number, scopeId: number, force = false) =>
    request<void>(`/api/projects/${id}/scope/${scopeId}?force=${force}`, {
      method: 'DELETE',
    }),
  importScope: (id: number, file: File, dryRun: boolean, replace = false) =>
    upload<SheetImportSummary>(
      `/api/projects/${id}/scope/import?dry_run=${dryRun}&replace=${replace}`,
      file,
    ),

  // --- task template -------------------------------------------------------
  template: (id: number) => request<TemplateRow[]>(`/api/projects/${id}/template`),
  importTemplate: (id: number, file: File, dryRun: boolean, forceRemove = false) =>
    upload<SheetImportSummary>(
      `/api/projects/${id}/template/import?dry_run=${dryRun}&force_remove=${forceRemove}`,
      file,
    ),

  // --- leadership actions --------------------------------------------------
  actions: (id: number) =>
    request<LeadershipAction[]>(`/api/projects/${id}/actions`),
  createAction: (id: number, body: ActionInput) =>
    request<LeadershipAction>(`/api/projects/${id}/actions`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateAction: (actionId: number, body: ActionInput) =>
    request<LeadershipAction>(`/api/actions/${actionId}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteAction: (actionId: number) =>
    request<void>(`/api/actions/${actionId}`, { method: 'DELETE' }),

  // --- administration ------------------------------------------------------
  users: () => request<AdminUser[]>('/api/users'),
  createUser: (username: string, password: string, role: Role) =>
    request<AdminUser>('/api/users', {
      method: 'POST',
      body: JSON.stringify({ username, password, role }),
    }),
  setUserRole: (userId: number, role: Role) =>
    request<AdminUser>(`/api/users/${userId}/role`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    }),
  setUserActive: (userId: number, isActive: boolean) =>
    request<AdminUser>(`/api/users/${userId}/active`, {
      method: 'PATCH',
      body: JSON.stringify({ is_active: isActive }),
    }),
  resetUserPassword: (userId: number) =>
    request<{ username: string; temporary_password: string; note: string }>(
      `/api/users/${userId}/reset-password`,
      { method: 'POST' },
    ),
  setUserAssignments: (userId: number, projectIds: number[]) =>
    request<AdminUser>(`/api/users/${userId}/assignments`, {
      method: 'PUT',
      body: JSON.stringify({ project_ids: projectIds }),
    }),

  // --- audit ---------------------------------------------------------------
  audit: (params: {
    project_id?: number;
    action?: string;
    source?: string;
    actor?: string;
    cursor?: number;
    limit?: number;
  }) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') query.set(key, String(value));
    }
    return request<AuditPage>(`/api/audit?${query}`);
  },
  auditActions: () => request<string[]>('/api/audit/actions'),

  // --- rollups and PDF -----------------------------------------------------
  narrative: (id: number) =>
    request<{ narrative: string }>(`/api/reporting/projects/${id}/narrative`),
  circleRollup: (id: number) =>
    request<{ circles: CircleRollup[] }>(`/api/reporting/projects/${id}/circles`),
  programmeRollup: (id: number) =>
    request<ProgrammeRollup>(`/api/reporting/programmes/${id}/rollup`),

  downloadProjectPack: (id: number) =>
    download(`/api/reporting/projects/${id}/pack.pdf`, `iptt-project-${id}.pdf`),
  downloadProgrammePack: (id: number) =>
    download(`/api/reporting/programmes/${id}/pack.pdf`, `iptt-programme-${id}.pdf`),
  downloadCirclePack: (id: number, circle: string) =>
    download(
      `/api/reporting/projects/${id}/circles/${encodeURIComponent(circle)}/pack.pdf`,
      `iptt-${circle}-${id}.pdf`,
    ),
  downloadScopeTemplate: (id: number) =>
    download(`/api/projects/${id}/scope/template`, `iptt-scope-template-${id}.xlsx`),
  downloadTemplate: (id: number) =>
    download(`/api/projects/${id}/template/export`, `iptt-template-${id}.xlsx`),
};
