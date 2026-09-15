/** Shapes returned by the IPTT API. Kept in one file so a contract change is a
 *  single compile error rather than a runtime surprise. */

export type Role = 'admin' | 'pm' | 'viewer';
export type ExecutionStatus = 'Not Started' | 'In Progress' | 'Completed';

export interface Session {
  id: number;
  username: string;
  role: Role;
  must_change_password: boolean;
  csrf_token: string;
}

export interface Programme {
  id: number;
  name: string;
  status: string;
  project_count: number;
  node_count: number;
}

export interface Project {
  id: number;
  programme_id: number;
  programme_name: string;
  name: string;
  status: string;
  project_start_date: string | null;
  baseline_locked: boolean;
  baseline_version: number;
  node_count: number;
  task_count: number;
}

export interface Kpis {
  project_id: number;
  project_name: string;
  total_nodes: number;
  /** Mean stage weight across nodes, 0-100. */
  health: number;
  trend: string;
  live_nodes: number;
  /** Already a percentage. Do not multiply by 100 - the legacy dashboard did,
   *  and rendered 3.5% as 350%. */
  progress: number;
  at_risk_nodes: number;
  total_delay_days: number;
  stage_mix: Record<string, number>;
}

export interface Stage {
  name: string;
  position: number;
  weight: number;
  is_terminal: boolean;
}

export interface NodeRow {
  scope_id: number;
  node_id: string;
  circle: string;
  facility_name: string;
  num_servers: number;
  stage: string;
  stage_position: number;
  weight: number;
  completed_tasks: number;
  total_tasks: number;
  total_delay_days: number;
  worst_delay_days: number;
  at_risk: boolean;
}

export interface MatrixRow {
  stage: string;
  Total: number;
  [circle: string]: string | number;
}

export interface GovernanceMatrix {
  circles: string[];
  rows: MatrixRow[];
  grand_total: number;
}

export interface HeatmapEntry {
  key: string;
  delayed_tasks: number;
  total_delay_days: number;
}

export interface Heatmap {
  by_circle: HeatmapEntry[];
  by_facility: HeatmapEntry[];
}

export interface TaskRow {
  execution_id: number;
  task_id: number;
  template_task_number: number;
  task_name: string;
  planned_start: string | null;
  planned_finish: string | null;
  actual_start: string | null;
  actual_finish: string | null;
  status: ExecutionStatus;
  delay_days: number;
  delay_reason: string | null;
}

export interface GridNode {
  scope_id: number;
  node_id: string;
  circle: string;
  facility_name: string;
  num_servers: number;
  tasks: TaskRow[];
}

export interface ExecutionGrid {
  page: number;
  page_size: number;
  total_nodes: number;
  nodes: GridNode[];
}

export interface ExecutionUpdate {
  scope_id: number;
  task_id: number;
  actual_start?: string | null;
  actual_finish?: string | null;
  status?: ExecutionStatus;
  delay_reason?: string | null;
}

export interface CircleCount {
  circle: string;
  node_count: number;
}

export interface ImportChange {
  node_id: string;
  template_task_number: number;
  task_name: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
}

export interface ImportReport {
  dry_run: boolean;
  ok: boolean;
  rows_read: number;
  nodes_matched: number;
  activities_considered: number;
  change_count: number;
  changes: ImportChange[];
  changes_truncated: number;
  errors: string[];
  warnings: string[];
}

export interface ScopeRow {
  id: number;
  node_id: string;
  circle: string;
  facility_name: string;
  num_servers: number;
  priority: number;
  status: string;
  task_count: number;
  has_execution_data: boolean;
}

export interface TemplateRow {
  template_task_number: number;
  name: string;
  duration_days: number;
  predecessor_template_number: number | null;
  is_prerequisite: boolean;
  owner_role: string | null;
  node_count: number;
  recorded_dates: number;
}

export type ActionPriority = 'High' | 'Medium' | 'Low';
export type ActionStatus = 'Open' | 'In Progress' | 'Closed';

export interface LeadershipAction {
  id: number;
  project_id: number;
  action_required: string;
  owner: string | null;
  target_date: string | null;
  priority: ActionPriority;
  status: ActionStatus;
  circle: string | null;
  node_id: string | null;
  risk_area: string | null;
  remarks: string | null;
  overdue_days: number;
  escalation: string;
}

export interface ActionInput {
  action_required: string;
  owner?: string | null;
  target_date?: string | null;
  priority: ActionPriority;
  status: ActionStatus;
  circle?: string | null;
  node_id?: string | null;
  risk_area?: string | null;
  remarks?: string | null;
}

export interface AdminUser {
  id: number;
  username: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  is_locked: boolean;
  assigned_project_ids: number[];
}

export interface AuditEntry {
  id: number;
  created_at: string;
  actor_username: string;
  actor_role: string | null;
  action: string;
  source: string;
  project_id: number | null;
  node_id: string | null;
  task_name: string | null;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
}

export interface AuditPage {
  entries: AuditEntry[];
  next_cursor: number | null;
  has_more: boolean;
}

export interface CircleRollup {
  circle: string;
  nodes: number;
  live_nodes: number;
  health: number;
  progress: number;
  at_risk_nodes: number;
  total_delay_days: number;
  status: string;
}

export interface ProjectRollup {
  project_id: number;
  project_name: string;
  nodes: number;
  health: number;
  progress: number;
  live_nodes: number;
  at_risk_nodes: number;
  total_delay_days: number;
  status: string;
  dominant_stage: string;
}

export interface ProgrammeRollup {
  programme_id: number;
  programme_name: string;
  total_projects: number;
  total_nodes: number;
  health: number;
  progress: number;
  live_nodes: number;
  at_risk_nodes: number;
  total_delay_days: number;
  status: string;
  dominant_stage: string;
  projects: ProjectRollup[];
  circles: CircleRollup[];
  stage_mix: Record<string, number>;
  narrative: string;
}

export interface SheetImportSummary {
  dry_run: boolean;
  ok: boolean;
  rows_read?: number;
  to_create?: number | number[];
  to_update?: number | { node_id: string; changes: Record<string, unknown> }[];
  to_remove?: number | number[];
  activities_in_sheet?: number;
  nodes?: number;
  task_rows_affected?: number;
  creates?: string[];
  updates?: { node_id: string; changes: Record<string, unknown> }[];
  removes?: string[];
  errors: string[];
}
