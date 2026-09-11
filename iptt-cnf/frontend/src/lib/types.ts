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
