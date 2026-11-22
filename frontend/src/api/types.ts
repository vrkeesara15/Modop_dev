export type Status = "bench" | "light" | "healthy" | "hot" | "over" | "none";

export interface Me {
  id: string;
  email: string;
  name: string;
  roles: string[];
  gl_group_ids: string[];
  sl_group_ids: string[];
  pl_project_ids: string[];
  can_see_rates: boolean;
  person_id: string | null;
}

export interface Skill {
  name: string;
  proficiency: number;
  category?: string | null;
}

export interface PersonRow {
  id: string;
  name: string;
  email: string | null;
  group_id: string | null;
  group_name: string | null;
  gl_name: string | null;
  tech_level: string | null;
  tech_level_id: string | null;
  role_family: string | null;
  location: string | null;
  std_hours_week: number;
  skills: Skill[];
  bill_rate?: number | null;
  can_edit?: boolean;
  util_forecast_4w?: number;
  util_confirmed_4w?: number;
  status_now?: Status;
  source?: string;
  openair_user_id?: string | null;
  manager_name?: string | null;
}

export interface Project {
  id: string;
  name: string;
  client: string | null;
  openair_project_id: string | null;
  pl_user_id: string | null;
  pl_name: string | null;
  status: string;
  billable: boolean;
  start_date: string | null;
  end_date: string | null;
  source: string;
  can_edit?: boolean;
  demand_lines?: number;
  open_lines?: number;
  min_confidence?: number | null;
  people?: number;
}

export interface DemandLine {
  id: string;
  project_id: string;
  role_family: string | null;
  min_tech_level_id: string | null;
  min_tech_level: string | null;
  skills: string[];
  hours_per_week: number;
  weighted_hours_per_week: number;
  start_week: string;
  end_week: string;
  confidence: number;
  requested_person_id: string | null;
  requested_person_name: string | null;
  status: string;
  notes: string | null;
  people_attached: number;
  requested_rate?: number | null;
  fill_by_week: Record<string, { required: number; confirmed: number; pending: number; unfilled: number }>;
  at_risk: boolean;
  release_prompt?: { count: number; people: { person_id: string; name: string }[] };
}

export interface AvailabilityPerson {
  person_id: string;
  name: string;
  email: string | null;
  group_id: string | null;
  group_name: string | null;
  tech_level: string | null;
  tech_level_rank: number | null;
  role_family: string | null;
  location: string | null;
  skills: Skill[];
  free_hours_avg: number;
  free_hours_min: number;
  free_by_week: Record<string, number>;
  first_free_week: string | null;
  status: Status;
  flags: { from_date: string; hours_per_week: number; note: string | null }[];
  bill_rate?: number | null;
  score?: number;
  skill_hits?: string[];
  continuity?: boolean;
  margin_warning?: number | null;
}

export interface Alert {
  id: string;
  type: string;
  severity: "info" | "warning" | "critical";
  status: "new" | "seen" | "done";
  message: string;
  person_id: string | null;
  person_name: string | null;
  project_id: string | null;
  project_name: string | null;
  week_start: string | null;
  created_at: string;
}

export interface Change {
  id: string;
  at: string;
  entity: string;
  entity_id: string | null;
  entity_key: string | null;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
  actor: string;
  reason: string | null;
  action: string | null;
  run_id: string | null;
}

export interface Driver {
  driver: "project_spike" | "structural_gap" | null;
  top_project_id: string | null;
  top_project_name?: string | null;
  top_project_share: number;
  overload_hours: number;
}

export interface ImportRunSummary {
  id: string;
  status: string;
  source: string;
  report_type: string;
  file_name: string | null;
  started_at: string | null;
  finished_at: string | null;
  triggered_by: string | null;
  error: string | null;
  rows_read: number;
  new: number;
  changed: number;
  removed: number;
  rejected: number;
  conflicts: number;
  counts: Record<string, number | string>;
}
