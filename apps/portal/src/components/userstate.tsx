/**
 * Phase 6 on the dashboard page: custom views, sharing links, subscriptions, alerts, comments,
 * certification and data quality warnings (P6.3-P6.6). Every action is checked again by the server.
 */
import type { DashboardState } from "@tabfree/runtime/host";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { enc, get, send, when } from "../api.js";
import { Problem } from "../layout.js";

export interface Quality {
  certified: boolean;
  certificationNote: string;
  certifiedBy: string | null;
  warning: { type: string; message: string; by: string } | null;
}

interface CustomView {
  id: string;
  name: string;
  owner: string;
  shared: boolean;
  state: Partial<DashboardState>;
}

interface Schedule {
  id: string;
  name: string;
  description: string;
}

interface Comment {
  id: string;
  author: string;
  body: string;
  created_at: string;
}

/** base64url JSON, as the print view and links read it. */
export function encodeState(state: Partial<DashboardState>): string {
  const json = JSON.stringify({ filters: state.filters ?? {}, parameters: state.parameters ?? {} });
  return btoa(json).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const id = `dlg-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <dialog ref={dialog} className="dialog" onClose={onClose} aria-labelledby={id}>
      <header>
        <h2 id={id}>{title}</h2>
        <button type="button" onClick={() => dialog.current?.close()} aria-label="Close">
          ✕
        </button>
      </header>
      {children}
    </dialog>
  );
}

const WARNING_LABELS: Record<string, string> = {
  warning: "Warning",
  deprecated: "Deprecated",
  stale: "Stale data",
  under_maintenance: "Under maintenance",
  sensitive: "Sensitive data",
};

export function QualityBanner({ quality }: { quality: Quality | undefined }) {
  if (!quality) return null;
  return (
    <>
      {quality.certified ? (
        <span className="pill certified" title={quality.certificationNote || undefined}>
          ✓ Certified{quality.certifiedBy ? ` by ${quality.certifiedBy}` : ""}
        </span>
      ) : null}
      {quality.warning ? (
        <p className="quality-warning" role="status">
          <strong>{WARNING_LABELS[quality.warning.type] ?? "Warning"}:</strong> {quality.warning.message}
        </p>
      ) : null}
    </>
  );
}

export function ViewsDialog({
  id,
  current,
  canShare,
  onApply,
  onClose,
}: {
  id: string;
  current: Partial<DashboardState>;
  canShare: boolean;
  onApply: (state: Partial<DashboardState>) => void;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const views = useQuery({
    queryKey: ["views", id],
    queryFn: () => get<{ views: CustomView[]; default: string | null }>(`/api/dashboards/${enc(id)}/views`),
  });
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [makeDefault, setMakeDefault] = useState(false);
  const refresh = () => void client.invalidateQueries({ queryKey: ["views", id] });
  const save = useMutation({
    mutationFn: () =>
      send("POST", `/api/dashboards/${enc(id)}/views`, {
        name,
        state: { filters: current.filters ?? {}, parameters: current.parameters ?? {} },
        shared,
        default: makeDefault,
      }),
    onSuccess: () => {
      setName("");
      refresh();
    },
  });
  const act = useMutation({
    mutationFn: ({ method, path }: { method: "PUT" | "DELETE"; path: string }) => send(method, path),
    onSuccess: refresh,
  });
  return (
    <Modal title="Custom views" onClose={onClose}>
      {views.error ? <Problem error={views.error} /> : null}
      {save.error ? <Problem error={save.error} /> : null}
      <ul className="plain">
        {(views.data?.views ?? []).map((v) => (
          <li key={v.id}>
            <button type="button" className="link" onClick={() => onApply(v.state)}>
              {v.name}
            </button>{" "}
            <span className="muted small">
              {v.shared ? `shared by ${v.owner}` : "private"}
              {views.data?.default === v.id ? " · your default" : ""}
            </span>{" "}
            {views.data?.default === v.id ? (
              <button type="button" onClick={() => act.mutate({ method: "DELETE", path: `/api/dashboards/${enc(id)}/views/default` })}>
                Clear default
              </button>
            ) : (
              <button type="button" onClick={() => act.mutate({ method: "PUT", path: `/api/custom-views/${enc(v.id)}/default` })}>
                Make default
              </button>
            )}{" "}
            <button type="button" onClick={() => act.mutate({ method: "DELETE", path: `/api/custom-views/${enc(v.id)}` })}>
              Delete
            </button>
          </li>
        ))}
      </ul>
      <form
        className="inline-form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <label>
          Save the current filters as{" "}
          <input value={name} required maxLength={200} onChange={(e) => setName(e.target.value)} />
        </label>
        {canShare ? (
          <label>
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} /> Share with everyone who can
            view it
          </label>
        ) : null}
        <label>
          <input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} /> My default
        </label>
        <button type="submit" className="primary" disabled={save.isPending}>
          Save view
        </button>
      </form>
    </Modal>
  );
}

export function ShareDialog({ id, current, onClose }: { id: string; current: Partial<DashboardState>; onClose: () => void }) {
  const share = useMutation({
    mutationFn: () =>
      send<{ url: string }>("POST", `/api/dashboards/${enc(id)}/share`, {
        state: { filters: current.filters ?? {}, parameters: current.parameters ?? {} },
      }),
  });
  useEffect(() => {
    share.mutate();
  }, []);
  return (
    <Modal title="Share" onClose={onClose}>
      {share.error ? <Problem error={share.error} /> : null}
      <p className="muted small">The link opens this dashboard with the current filters. People still need permission to view it.</p>
      {share.data ? (
        <p>
          <input readOnly value={share.data.url} aria-label="Link" onFocus={(e) => e.target.select()} size={48} />{" "}
          <button type="button" onClick={() => void navigator.clipboard?.writeText(share.data.url)}>
            Copy
          </button>
        </p>
      ) : null}
    </Modal>
  );
}

function ScheduleSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const schedules = useQuery({ queryKey: ["schedules"], queryFn: () => get<Schedule[]>("/api/schedules") });
  return (
    <label>
      When{" "}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">After the data refreshes</option>
        {(schedules.data ?? []).map((s) => (
          <option key={s.id} value={s.id}>
            {s.name} ({s.description})
          </option>
        ))}
      </select>
    </label>
  );
}

export function SubscribeDialog({
  id,
  title,
  model,
  current,
  onClose,
}: {
  id: string;
  title: string;
  model: string | undefined;
  current: Partial<DashboardState>;
  onClose: () => void;
}) {
  const [subject, setSubject] = useState(title);
  const [schedule, setSchedule] = useState("");
  const [format, setFormat] = useState<"png" | "pdf">("png");
  const [onlyIfData, setOnlyIfData] = useState(false);
  const create = useMutation({
    mutationFn: () =>
      send<{ status: string }>("POST", "/api/subscriptions", {
        dashboard: id,
        subject,
        format,
        onlyIfData,
        state: { filters: current.filters ?? {}, parameters: current.parameters ?? {} },
        ...(schedule ? { schedule } : { afterRefreshOf: model }),
      }),
  });
  return (
    <Modal title="Subscribe" onClose={onClose}>
      <p className="muted small">An email with this dashboard as you see it, with the current filters.</p>
      {create.error ? <Problem error={create.error} /> : null}
      {create.data ? (
        <p role="status">Subscribed.</p>
      ) : (
        <form
          className="stack"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <label>
            Subject <input value={subject} required maxLength={255} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <ScheduleSelect value={schedule} onChange={setSchedule} />
          <label>
            Format{" "}
            <select value={format} onChange={(e) => setFormat(e.target.value as "png" | "pdf")}>
              <option value="png">Image</option>
              <option value="pdf">PDF</option>
            </select>
          </label>
          <label>
            <input type="checkbox" checked={onlyIfData} onChange={(e) => setOnlyIfData(e.target.checked)} /> Only if there is data
          </label>
          <button type="submit" className="primary" disabled={create.isPending}>
            Subscribe
          </button>
        </form>
      )}
    </Modal>
  );
}

export function AlertDialog({
  id,
  queries,
  model,
  onClose,
}: {
  id: string;
  queries: string[];
  model: string | undefined;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(queries[0] ?? "");
  const [measure, setMeasure] = useState("");
  const [operator, setOperator] = useState(">");
  const [threshold, setThreshold] = useState("0");
  const [title, setTitle] = useState("");
  const [schedule, setSchedule] = useState("");
  const create = useMutation({
    mutationFn: () =>
      send("POST", "/api/alerts", {
        dashboard: id,
        title,
        query,
        measure,
        condition: { operator, threshold: Number(threshold) },
        ...(schedule ? { schedule } : { afterRefreshOf: model }),
      }),
  });
  return (
    <Modal title="Data alert" onClose={onClose}>
      <p className="muted small">Email me when a value crosses a threshold. Checked with your permissions.</p>
      {create.error ? <Problem error={create.error} /> : null}
      {create.data ? (
        <p role="status">Alert created.</p>
      ) : (
        <form
          className="stack"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <label>
            Name <input value={title} required maxLength={255} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            Query{" "}
            <select value={query} onChange={(e) => setQuery(e.target.value)}>
              {queries.map((q) => (
                <option key={q}>{q}</option>
              ))}
            </select>
          </label>
          <label>
            Measure (column) <input value={measure} required maxLength={256} onChange={(e) => setMeasure(e.target.value)} />
          </label>
          <label>
            Condition{" "}
            <select value={operator} onChange={(e) => setOperator(e.target.value)} aria-label="Operator">
              {[">", ">=", "<", "<=", "=", "!="].map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>{" "}
            <input type="number" step="any" value={threshold} onChange={(e) => setThreshold(e.target.value)} aria-label="Threshold" />
          </label>
          <ScheduleSelect value={schedule} onChange={setSchedule} />
          <button type="submit" className="primary" disabled={create.isPending}>
            Create alert
          </button>
        </form>
      )}
    </Modal>
  );
}

export function CommentsPanel({ id, canAdd }: { id: string; canAdd: boolean }) {
  const client = useQueryClient();
  const comments = useQuery({ queryKey: ["comments", id], queryFn: () => get<Comment[]>(`/api/dashboards/${enc(id)}/comments`) });
  const [body, setBody] = useState("");
  const add = useMutation({
    mutationFn: () => send("POST", `/api/dashboards/${enc(id)}/comments`, { body }),
    onSuccess: () => {
      setBody("");
      void client.invalidateQueries({ queryKey: ["comments", id] });
    },
  });
  return (
    <section className="comments" aria-label="Comments">
      <h2>Comments</h2>
      {comments.error ? <Problem error={comments.error} /> : null}
      <ol className="plain">
        {(comments.data ?? []).map((c) => (
          <li key={c.id}>
            <strong>{c.author}</strong> <span className="muted small">{when(c.created_at)}</span>
            <p>{c.body}</p>
          </li>
        ))}
      </ol>
      {canAdd ? (
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <label className="sr-only" htmlFor="comment">
            Comment
          </label>
          <textarea id="comment" rows={2} maxLength={4000} value={body} placeholder="Add a comment; @name mentions someone" onChange={(e) => setBody(e.target.value)} />
          <button type="submit" disabled={!body.trim() || add.isPending}>
            Post
          </button>
        </form>
      ) : null}
    </section>
  );
}
