import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";

import { Layout } from "./layout.js";
import { AccountPage } from "./pages/account.js";
import { AdminAudit, AdminGroups, AdminSite, AdminUsers } from "./pages/admin.js";
import { DashboardPage } from "./pages/dashboard.js";
import { DraftPage } from "./pages/draft.js";
import { ExplorePage } from "./pages/explore.js";
import { HomePage } from "./pages/home.js";
import { MigrationItemPage, MigrationPage } from "./pages/migration.js";
import { ModelPage } from "./pages/model.js";

const text = (value: unknown): string | undefined => (typeof value === "string" && value ? value : undefined);

const root = createRootRoute({ component: Layout });
const home = createRoute({ getParentRoute: () => root, path: "/", component: HomePage });
export const explore = createRoute({
  getParentRoute: () => root,
  path: "/explore",
  validateSearch: (s: Record<string, unknown>): { project?: string; q?: string; kind?: string } => {
    const out: { project?: string; q?: string; kind?: string } = {};
    const project = text(s["project"]);
    const q = text(s["q"]);
    const kind = text(s["kind"]);
    if (project) out.project = project;
    if (q) out.q = q;
    if (kind === "dashboard" || kind === "model") out.kind = kind;
    return out;
  },
  component: ExplorePage,
});
export const dashboard = createRoute({
  getParentRoute: () => root,
  path: "/dashboards/$id",
  // print=1&state=<base64url JSON>: the export renderer's view (P3.7), no portal chrome.
  validateSearch: (s: Record<string, unknown>): { print?: string; state?: string } => {
    const out: { print?: string; state?: string } = {};
    const print = text(s["print"] === 1 ? "1" : s["print"]);
    const state = text(s["state"]);
    if (print) out.print = print;
    if (state) out.state = state;
    return out;
  },
  component: DashboardPage,
});
export const draftRoute = createRoute({
  getParentRoute: () => root,
  path: "/drafts/$id",
  // print=1: only the live preview, for the renderer (validation screenshots, migration repair).
  validateSearch: (s: Record<string, unknown>): { print?: string } => {
    const print = text(s["print"] === 1 ? "1" : s["print"]);
    return print ? { print } : {};
  },
  component: DraftPage,
});
export const migrationRoute = createRoute({
  getParentRoute: () => root,
  path: "/migration",
  validateSearch: (s: Record<string, unknown>): { wave?: string } => {
    const wave = text(typeof s["wave"] === "number" ? String(s["wave"]) : s["wave"]);
    return wave ? { wave } : {};
  },
  component: MigrationPage,
});
export const migrationItemRoute = createRoute({
  getParentRoute: () => root,
  path: "/migration/$id",
  component: MigrationItemPage,
});
export const model = createRoute({ getParentRoute: () => root, path: "/models/$id", component: ModelPage });
const account = createRoute({ getParentRoute: () => root, path: "/account", component: AccountPage });
const adminUsers = createRoute({ getParentRoute: () => root, path: "/admin/users", component: AdminUsers });
const adminGroups = createRoute({ getParentRoute: () => root, path: "/admin/groups", component: AdminGroups });
const adminSite = createRoute({ getParentRoute: () => root, path: "/admin/site", component: AdminSite });
const adminAudit = createRoute({ getParentRoute: () => root, path: "/admin/audit", component: AdminAudit });

const tree = root.addChildren([
  home,
  explore,
  dashboard,
  draftRoute,
  model,
  account,
  adminUsers,
  adminGroups,
  adminSite,
  adminAudit,
  migrationRoute,
  migrationItemRoute,
]);

export const router = createRouter({ routeTree: tree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
