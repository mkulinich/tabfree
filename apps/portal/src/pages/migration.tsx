/**
 * The migration console (P5.6): waves and items, Tableau's picture next to the TabFree draft, the
 * fidelity score, and the reviewer's actions. Site administrators only.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { enc, get, send, when } from "../api.js";
import { Problem } from "../layout.js";
import { migrationItemRoute, migrationRoute } from "../router.js";
import { Preview } from "./draft.js";

export interface Fidelity {
  score: number;
  grade: "pass" | "review" | "fail";
  components: { coverage: number | null; validation: number | null; data: number | null; visual: number | null };
  unsupported: string[];
}

export interface MigrationItem {
  id: string;
  title: string;
  workbook: string;
  project_id: string | null;
  owner: string | null;
  wave: number | null;
  usage: number;
  status: string;
  draft_id: string | null;
  fidelity: Fidelity | null;
  repair_iterations: number;
  reviewer: string | null;
  note: string;
  published_revision: number | null;
  updated_at: string;
}

interface ItemDetail extends MigrationItem {
  report: { unsupported?: string[]; notes?: string[]; coverage?: { sheets: number; converted: number }; views?: string[] };
  events: { action: string; by: string | null; note: string; created_at: string; data: unknown }[];
  has_reference_png: boolean;
  has_render_png: boolean;
  running: boolean;
}

interface Summary {
  waves: Record<string, Record<string, number>>;
  totals: Record<string, number>;
  items: number;
  decisions: number;
  records: Record<string, number>;
  access: { users: number; unexplained: number; ok: boolean } | null;
  repairAvailable: boolean;
  renderAvailable: boolean;
}

export const STATUS_LABELS: Record<string, string> = {
  converted: "Converted",
  manual: "Needs rebuild",
  repairing: "Repairing",
  review: "Ready for review",
  changes_requested: "Changes requested",
  approved: "Published",
  rebuild: "Manual rebuild",
  retired: "Retired",
};

/** The score as a percentage with its grade, in words rather than colour alone. */
export function FidelityBadge({ fidelity }: { fidelity: Fidelity | null }) {
  if (!fidelity) return <span className="muted">–</span>;
  const words = { pass: "pass", review: "review", fail: "fail" }[fidelity.grade];
  return (
    <span className={`fidelity fidelity-${fidelity.grade}`}>
      {Math.round(fidelity.score * 100)}% · {words}
    </span>
  );
}

function pct(value: number | null): string {
  return value === null || value === undefined ? "not measured" : `${Math.round(value * 100)}%`;
}

export function MigrationPage() {
  const search = migrationRoute.useSearch();
  const [wave, setWave] = useState<string>(search.wave ?? "");
  const [status, setStatus] = useState<string>("");
  const client = useQueryClient();
  const summary = useQuery({ queryKey: ["migration"], queryFn: () => get<Summary>("/api/migration") });
  const items = useQuery({
    queryKey: ["migration-items", wave, status],
    queryFn: () => get<MigrationItem[]>("/api/migration/items", { wave: wave || undefined, status: status || undefined }),
  });
  const access = useMutation({
    mutationFn: () => send<{ unexplained?: number; users?: number; reason?: string }>("POST", "/api/migration/access-parity"),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["migration"] }),
  });
  if (summary.error) return <Problem error={summary.error} />;
  const s = summary.data;
  return (
    <div className="page">
      <h1>Migration from Tableau</h1>
      {s ? (
        <>
          <p className="muted">
            {s.items} dashboards · {s.totals["approved"] ?? 0} published · {s.decisions} data-source decisions waiting ·{" "}
            {s.records["job"] ?? 0} subscriptions and refresh jobs, {s.records["alert"] ?? 0} alerts kept for later
          </p>
          <table className="waves">
            <caption className="sr-only">Dashboards per wave and status</caption>
            <thead>
              <tr>
                <th scope="col">Wave</th>
                {Object.keys(STATUS_LABELS).map((k) => (
                  <th scope="col" key={k}>
                    {STATUS_LABELS[k]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(s.waves).map(([w, counts]) => (
                <tr key={w}>
                  <th scope="row">
                    <button type="button" className="link" onClick={() => setWave(w === "retired" ? "" : w)}>
                      {w === "retired" ? "Not in a wave" : `Wave ${w}`}
                    </button>
                  </th>
                  {Object.keys(STATUS_LABELS).map((k) => (
                    <td key={k}>{counts[k] ?? 0}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="toolbar">
            <button type="button" disabled={access.isPending} onClick={() => access.mutate()}>
              Check access parity
            </button>
            {s.access ? (
              <span role="status">
                Access parity: {s.access.users} users, {s.access.unexplained} unexplained difference
                {s.access.unexplained === 1 ? "" : "s"}
              </span>
            ) : null}
            {access.data?.reason ? <span role="status">{access.data.reason}</span> : null}
            {!s.repairAvailable ? <span className="muted">The repair agent needs AI to be on.</span> : null}
          </div>
        </>
      ) : null}
      <div className="inline-form">
        <label>
          Wave{" "}
          <select value={wave} onChange={(e) => setWave(e.target.value)}>
            <option value="">All</option>
            {Object.keys(s?.waves ?? {})
              .filter((w) => w !== "retired")
              .map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
          </select>
        </label>
        <label>
          Status{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {Object.entries(STATUS_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </div>
      {items.error ? <Problem error={items.error} /> : null}
      <table className="migration-items">
        <thead>
          <tr>
            <th scope="col">Dashboard</th>
            <th scope="col">Workbook</th>
            <th scope="col">Wave</th>
            <th scope="col">Views</th>
            <th scope="col">Status</th>
            <th scope="col">Fidelity</th>
          </tr>
        </thead>
        <tbody>
          {(items.data ?? []).map((item) => (
            <tr key={item.id}>
              <td>
                <Link to="/migration/$id" params={{ id: item.id }}>
                  {item.title}
                </Link>
              </td>
              <td>{item.workbook}</td>
              <td>{item.wave ?? "–"}</td>
              <td>{item.usage}</td>
              <td>{STATUS_LABELS[item.status] ?? item.status}</td>
              <td>
                <FidelityBadge fidelity={item.fidelity} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MigrationItemPage() {
  const { id } = migrationItemRoute.useParams();
  const client = useQueryClient();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const item = useQuery({
    queryKey: ["migration-item", id],
    queryFn: () => get<ItemDetail>(`/api/migration/items/${enc(id)}`),
    refetchInterval: (q) => (q.state.data?.running || q.state.data?.status === "repairing" ? 3000 : false),
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["migration-item", id] });
    void client.invalidateQueries({ queryKey: ["migration"] });
  };
  const act = useMutation({
    mutationFn: (action: string) => send("POST", `/api/migration/items/${enc(id)}/actions`, { action, note }),
    onSuccess: () => {
      setNote("");
      refresh();
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });
  const repair = useMutation({
    mutationFn: () => send("POST", `/api/migration/items/${enc(id)}/repair`, { instructions: note }),
    onSuccess: refresh,
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });
  const verify = useMutation({
    mutationFn: () => send("POST", `/api/migration/items/${enc(id)}/verify`),
    onSuccess: refresh,
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });
  if (item.error) return <Problem error={item.error} />;
  if (!item.data) return <p className="page muted">Loading…</p>;
  const d = item.data;
  const f = d.fidelity;
  const open = ["converted", "review", "changes_requested"].includes(d.status);
  return (
    <div className="page migration-item">
      <p>
        <Link to="/migration">← Migration</Link>
      </p>
      <h1>{d.title}</h1>
      <p className="muted">
        From the Tableau workbook <strong>{d.workbook}</strong> · wave {d.wave ?? "–"} · {d.usage} views ·{" "}
        {STATUS_LABELS[d.status] ?? d.status}
        {d.published_revision ? (
          <>
            {" · "}
            <Link to="/dashboards/$id" params={{ id: d.id }}>
              published (revision {d.published_revision})
            </Link>
          </>
        ) : null}
      </p>
      <section aria-label="Fidelity" className="card">
        <h2>
          Fidelity <FidelityBadge fidelity={f} />
        </h2>
        {f ? (
          <dl className="fidelity-components">
            <dt>Worksheets converted</dt>
            <dd>{pct(f.components.coverage)}</dd>
            <dt>Validation</dt>
            <dd>{pct(f.components.validation)}</dd>
            <dt>Data parity</dt>
            <dd>{pct(f.components.data)}</dd>
            <dt>Visual similarity</dt>
            <dd>{pct(f.components.visual)}</dd>
          </dl>
        ) : null}
      </section>
      <div className="side-by-side">
        <figure>
          <figcaption>Tableau (captured)</figcaption>
          {d.has_reference_png ? (
            <img src={`/api/migration/items/${enc(d.id)}/reference.png`} alt={`Tableau's rendering of ${d.title}`} />
          ) : (
            <p className="muted">No Tableau image was captured for this view.</p>
          )}
        </figure>
        <figure>
          <figcaption>TabFree (live draft)</figcaption>
          {d.draft_id && d.status !== "approved" ? (
            <Preview id={d.draft_id} version={d.updated_at} />
          ) : d.has_render_png ? (
            <img src={`/api/migration/items/${enc(d.id)}/render.png`} alt={`TabFree's rendering of ${d.title}`} />
          ) : (
            <p className="muted">No converted bundle: this dashboard needs a manual rebuild.</p>
          )}
        </figure>
      </div>
      <section aria-label="Review" className="card">
        <h2>Review</h2>
        <label htmlFor="review-note">Note, or instructions for the repair agent</label>
        <textarea id="review-note" rows={3} value={note} maxLength={4000} onChange={(e) => setNote(e.target.value)} />
        <div className="toolbar">
          <button
            type="button"
            className="primary"
            disabled={!open || act.isPending || d.running}
            onClick={() => {
              if (confirm("Publish this dashboard for everyone its Tableau permissions allow?")) act.mutate("approve");
            }}
          >
            Approve and publish
          </button>
          <button type="button" disabled={!open || act.isPending || d.running || !note.trim()} onClick={() => act.mutate("request_changes")}>
            Request changes
          </button>
          <button type="button" disabled={!open || repair.isPending || d.running} onClick={() => repair.mutate()}>
            {d.running || d.status === "repairing" ? "Repairing…" : "Run the repair agent"}
          </button>
          <button type="button" disabled={!d.draft_id || verify.isPending} onClick={() => verify.mutate()}>
            {verify.isPending ? "Checking…" : "Verify data and visuals"}
          </button>
          <button type="button" disabled={act.isPending || d.status === "approved"} onClick={() => act.mutate("rebuild")}>
            Mark for manual rebuild
          </button>
          <button type="button" disabled={act.isPending || d.status === "approved"} onClick={() => act.mutate("retire")}>
            Retire
          </button>
          {["rebuild", "retired", "changes_requested"].includes(d.status) ? (
            <button type="button" onClick={() => act.mutate("reopen")}>
              Reopen
            </button>
          ) : null}
        </div>
        {error ? (
          <p className="problem" role="alert">
            {error}
          </p>
        ) : null}
      </section>
      {d.report.unsupported?.length ? (
        <section aria-label="Not converted">
          <h2>Not converted</h2>
          <ul>
            {d.report.unsupported.map((u, i) => (
              <li key={i}>{u}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {d.report.notes?.length ? (
        <details>
          <summary>Converter notes ({d.report.notes.length})</summary>
          <ul>
            {d.report.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </details>
      ) : null}
      <section aria-label="History">
        <h2>History</h2>
        <ol className="history">
          {d.events.map((e, i) => (
            <li key={i}>
              <strong>{e.action.replace(/_/g, " ")}</strong> {e.by ? `by ${e.by}` : ""} · {when(e.created_at)}
              {e.note ? <div className="small">{e.note}</div> : null}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
