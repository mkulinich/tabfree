/** Typed calls to the TabFree API (apps/api). Same origin; the session cookie authenticates. */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const JSON_HEADERS = { "content-type": "application/json", "x-tabfree-request": "1" };

async function parse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : null;
    throw new ApiError(response.status, typeof detail === "string" ? detail : `request failed (${response.status})`);
  }
  return body as T;
}

export async function get<T>(path: string, params?: Record<string, string | undefined>): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined && v !== "") query.set(k, v);
  const suffix = query.size ? `?${query}` : "";
  return parse<T>(await fetch(`${path}${suffix}`, { credentials: "same-origin" }));
}

export async function send<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: JSON_HEADERS, credentials: "same-origin" };
  if (body !== undefined) init.body = JSON.stringify(body);
  return parse<T>(await fetch(path, init));
}

export const enc = encodeURIComponent;

// ---- shapes --------------------------------------------------------------------------------------------

export type SiteRole = "unlicensed" | "viewer" | "explorer" | "explorer_can_publish" | "creator" | "site_administrator";
export const SITE_ROLES: SiteRole[] = [
  "unlicensed",
  "viewer",
  "explorer",
  "explorer_can_publish",
  "creator",
  "site_administrator",
];
export const ROLE_LABELS: Record<SiteRole, string> = {
  unlicensed: "Unlicensed",
  viewer: "Viewer",
  explorer: "Explorer",
  explorer_can_publish: "Explorer (can publish)",
  creator: "Creator",
  site_administrator: "Site Administrator",
};

export interface Me {
  username: string;
  fullname: string;
  groups: string[];
  siteRole: SiteRole;
}

export interface SiteInfo {
  id: string;
  name: string;
  database: boolean;
  admin: boolean;
  canCreateProjects: boolean;
}

export type Kind = "dashboard" | "model";

export interface Item {
  kind: Kind;
  id: string;
  name: string;
  description: string;
  project: string | null;
  projectPath: string[];
  owner: string | null;
  updatedAt: string | null;
  favorite: boolean;
  workbook: string | null;
}

export interface ProjectInfo {
  id: string;
  name: string;
  parent: string | null;
  path: string[];
  assetPermissions: "customizable" | "locked" | "locked_not_nested";
  owner: string | null;
  items: number;
  canPublish: boolean;
  canManage: boolean;
}

export interface Home {
  favorites: Item[];
  recents: Item[];
  recentlyUpdated: Item[];
}

export interface DashboardInfo extends Partial<Item> {
  capabilities: Record<string, boolean>;
  locked: boolean;
  tabs: { id: string; name: string }[];
}

export interface ModelField {
  name: string;
  role: "dimension" | "measure";
  type: string;
  description?: string;
  folder?: string;
  formula?: string;
  defaultAggregation?: string;
  format?: string;
}

export interface ModelInfo {
  id: string;
  title: string;
  description: string;
  engine: string | null;
  fields: ModelField[];
  hierarchies: { name: string; levels: string[] }[];
  parameters: { name: string; type: string; default?: unknown }[];
  groups: string[];
  sets: string[];
  capabilities: Record<string, boolean>;
  dataVersion: string;
  rls?: { id: string; description: string }[];
}

export type Decision = "allow" | "deny";

export interface RuleJson {
  user: string | null;
  group: string | null;
  capabilities: Record<string, Decision>;
}

export interface ContentPermissions {
  kind: Kind;
  id: string;
  rules: RuleJson[];
  lockedBy: string | null;
  capabilities: Record<string, string[]>;
}

export interface ProjectPermissions {
  kind: "project";
  id: string;
  assetPermissions: ProjectInfo["assetPermissions"];
  defaults: Record<string, RuleJson[]>;
  capabilities: Record<string, string[]>;
}

export interface Effective {
  user: string;
  siteRole: SiteRole;
  capabilities: Record<string, { allowed: boolean; reason: string }>;
}

export interface Revision {
  number: number;
  message: string;
  createdBy: string | null;
  createdAt: string;
  sha256: string;
}

export interface AdminUser {
  username: string;
  fullname: string;
  email: string | null;
  siteRole: SiteRole;
  effectiveSiteRole: SiteRole;
  authSource: "idp" | "local";
  disabled: boolean;
  groups: string[];
  attributes: Record<string, string>;
  lastSignInAt: string | null;
}

export interface AdminGroup {
  name: string;
  source: "local" | "idp";
  minimumSiteRole: SiteRole | null;
  members: string[];
}

export interface AdminSite {
  id: string;
  name: string;
  defaultSiteRole: SiteRole;
  settings: Record<string, string | number>;
}

export interface AuditEvent {
  id: number;
  at: string;
  actor: string | null;
  action: string;
  resourceKind: string | null;
  resourceId: string | null;
  outcome: string;
  detail: Record<string, unknown>;
}

export interface Token {
  id: string;
  name: string;
  createdAt: string;
  expiresAt: string;
  lastUsedAt: string | null;
  revoked: boolean;
}

/** Tableau's capability names, for labels. */
export const CAPABILITY_LABELS: Record<string, string> = {
  view: "View",
  filter: "Filter",
  view_comments: "View Comments",
  add_comments: "Add Comments",
  download_image_pdf: "Download Image/PDF",
  download_summary_data: "Download Summary Data",
  share_customized: "Share Customized",
  download_full_data: "Download Full Data",
  web_edit: "Web Edit",
  download_workbook: "Download Bundle",
  overwrite: "Overwrite",
  move: "Move",
  delete: "Delete",
  set_permissions: "Set Permissions",
  connect: "Connect",
  download_data_source: "Download Data Source",
  refresh_extracts: "Refresh Extracts",
  publish: "Publish",
  project_leader: "Project Leader",
};

export function label(capability: string): string {
  return CAPABILITY_LABELS[capability] ?? capability;
}

export function when(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}
