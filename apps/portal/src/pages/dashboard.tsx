import {
  type DashboardHost,
  type DashboardState,
  type ExportRequest,
  HostQueryError,
  type Manifest,
  mountDashboard,
} from "@tabfree/runtime/host";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";

import { type DashboardInfo, enc, get, send } from "../api.js";
import { FavoriteButton } from "../components/items.js";
import { PermissionsDialog } from "../components/permissions.js";
import { RevisionsDialog } from "../components/revisions.js";
import { Problem } from "../layout.js";
import { dashboard } from "../router.js";

let frameRuntime: Promise<string> | null = null;
/** The frame runtime is inlined into each dashboard's sandboxed srcdoc (the frame has no network). */
function runtimeSource(): Promise<string> {
  frameRuntime ??= fetch("/runtime/frame.js").then((r) => {
    if (!r.ok) throw new Error("the dashboard runtime is not built (npm run build -w @tabfree/runtime)");
    return r.text();
  });
  return frameRuntime;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", "x-tabfree-request": "1" },
    body: JSON.stringify(body),
  });
  const json: unknown = await response.json();
  if (!response.ok) {
    const detail = (json as { detail?: unknown }).detail;
    throw new HostQueryError(response.status === 404 ? "not_found" : "query_failed", String(detail ?? "failed"));
  }
  return json as T;
}

/** Exports run on the server, which checks the download capabilities (P3.7). */
async function startExport(id: string, request: ExportRequest): Promise<void> {
  const response = await fetch(`/api/dashboards/${enc(id)}/export`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-tabfree-request": "1" },
    body: JSON.stringify({
      format: request.format,
      query: request.query,
      state: request.state,
      selections: request.selections,
    }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    alert(body.detail ?? "The export failed.");
    return;
  }
  const blob = await response.blob();
  const name = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "")?.[1] ?? `${id}.${request.format}`;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

/** The filters and parameters passed to the print view (base64url JSON); anything malformed is ignored. */
export function decodeState(encoded: string | undefined): Partial<DashboardState> | undefined {
  if (!encoded) return undefined;
  try {
    const padded = encoded.replace(/-/g, "+").replace(/_/g, "/");
    const value: unknown = JSON.parse(atob(padded));
    if (!value || typeof value !== "object") return undefined;
    const out: Partial<DashboardState> = {};
    const { filters, parameters } = value as Record<string, unknown>;
    if (filters && typeof filters === "object") out.filters = filters as DashboardState["filters"];
    if (parameters && typeof parameters === "object") out.parameters = parameters as DashboardState["parameters"];
    return out;
  } catch {
    return undefined;
  }
}

function Viewer({ id, initial }: { id: string; initial?: Partial<DashboardState> | undefined }) {
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let host: DashboardHost | null = null;
    let cancelled = false;
    setError(null);
    (async () => {
      const bundle = await get<{ manifest: Manifest; html?: string; frameUrl?: string }>(`/api/dashboards/${enc(id)}`);
      // With a usercontent origin the frame loads the bundle from there; otherwise it is inlined.
      const runtime = bundle.frameUrl ? "" : await runtimeSource();
      if (cancelled || !container.current) return;
      const dark = matchMedia("(prefers-color-scheme: dark)").matches;
      host = mountDashboard({
        container: container.current,
        manifest: bundle.manifest,
        html: bundle.html ?? "",
        runtime,
        ...(bundle.frameUrl ? { frameUrl: bundle.frameUrl } : {}),
        ...(initial ? { initialState: initial } : {}),
        onExport: (request) => void startExport(id, request),
        theme: { mode: dark ? "dark" : "light" },
        runQuery: (request) => post(`/api/dashboards/${enc(id)}/query`, request),
        runDomain: (request) => post(`/api/dashboards/${enc(id)}/domain`, request),
        onDashboardError: (message) => setError(message),
      });
    })().catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
      host?.destroy();
    };
  }, [id, initial]);
  return (
    <>
      {error ? <Problem error={new Error(error)} /> : null}
      <div ref={container} className="viewer" />
    </>
  );
}

export function DashboardPage() {
  const { id } = dashboard.useParams();
  const search = dashboard.useSearch();
  const initial = useMemo(() => decodeState(search.state), [search.state]);
  if (search.print === "1") return <Viewer id={id} initial={initial} />;
  return <InteractivePage id={id} />;
}

function InteractivePage({ id }: { id: string }) {
  const [dialog, setDialog] = useState<"permissions" | "revisions" | null>(null);
  const navigate = useNavigate();
  const [editError, setEditError] = useState<string | null>(null);
  const edit = async () => {
    setEditError(null);
    try {
      // Your open draft of this dashboard, or a new one (needs Web Edit).
      const draft = await send<{ id: string }>("POST", `/api/dashboards/${enc(id)}/drafts`);
      await navigate({ to: "/drafts/$id", params: { id: draft.id } });
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    }
  };
  const info = useQuery({
    queryKey: ["dashboard-info", id],
    queryFn: () => get<DashboardInfo>(`/api/dashboards/${enc(id)}/info`),
  });
  if (info.error) return <Problem error={info.error} />;
  const caps = info.data?.capabilities ?? {};
  return (
    <div className="dashboard">
      <div className="viewer-bar">
        {info.data?.projectPath ? (
          <nav aria-label="Breadcrumb" className="crumbs">
            {info.data.project ? (
              <Link to="/explore" search={{ project: info.data.project }}>
                {info.data.projectPath.join(" / ")}
              </Link>
            ) : null}
          </nav>
        ) : null}
        <h1>{info.data?.name ?? id}</h1>
        {info.data?.kind ? (
          <FavoriteButton
            item={{ kind: "dashboard", id, favorite: Boolean(info.data.favorite), name: info.data.name ?? id }}
          />
        ) : null}
        <div className="toolbar">
          {caps["web_edit"] ? (
            <button type="button" className="primary" onClick={() => void edit()}>
              Edit with Claude
            </button>
          ) : null}
          {caps["set_permissions"] ? (
            <button type="button" onClick={() => setDialog("permissions")}>
              Permissions…
            </button>
          ) : null}
          {caps["overwrite"] ? (
            <button type="button" onClick={() => setDialog("revisions")}>
              Revisions…
            </button>
          ) : null}
        </div>
      </div>
      {editError ? (
        <p className="problem" role="alert">
          {editError}
        </p>
      ) : null}
      {info.data && info.data.tabs.length > 1 ? (
        <nav aria-label="Workbook tabs" className="tabs">
          {info.data.tabs.map((t) => (
            <Link key={t.id} to="/dashboards/$id" params={{ id: t.id }} aria-current={t.id === id ? "page" : undefined}>
              {t.name}
            </Link>
          ))}
        </nav>
      ) : null}
      <Viewer id={id} />
      {dialog === "permissions" ? <PermissionsDialog kind="dashboard" id={id} onClose={() => setDialog(null)} /> : null}
      {dialog === "revisions" ? <RevisionsDialog kind="dashboard" id={id} onClose={() => setDialog(null)} /> : null}
    </div>
  );
}
