/**
 * A draft with "Edit with Claude" (P4.3): the live preview, the chat panel, the diff and the two-step
 * publish. The preview runs the draft's queries as the signed-in author (with row-level security);
 * nothing anyone else sees changes until the author confirms publishing.
 */
import { type DashboardHost, HostQueryError, type Manifest, mountDashboard } from "@tabfree/runtime/host";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { ApiError, enc, get, send } from "../api.js";
import { Problem } from "../layout.js";
import { draftRoute } from "../router.js";

export interface Draft {
  id: string;
  dashboard: string;
  project: string | null;
  baseRevision: number | null;
  new: boolean;
  status: string;
  publishPending: boolean;
  approvedBy: string | null;
  updatedAt: string;
  files: Record<string, string>;
}

export interface AgentEvent {
  type: string;
  text: string;
  at?: string;
  tool?: string;
  error?: boolean;
  status?: string;
}

interface Check {
  name: string;
  status: "pass" | "warn" | "fail" | "skip";
  messages: string[];
}

interface PublishRequest {
  confirmation: string;
  expires: string;
  validation: { ok: boolean; checks: Check[] };
  diff: string;
}

let frameRuntime: Promise<string> | null = null;
function runtimeSource(): Promise<string> {
  frameRuntime ??= fetch("/runtime/frame.js").then((r) => {
    if (!r.ok) throw new Error("the dashboard runtime is not built (npm run build -w @tabfree/runtime)");
    return r.text();
  });
  return frameRuntime;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  try {
    return await send<T>("POST", path, body);
  } catch (e) {
    if (e instanceof ApiError) throw new HostQueryError(e.status === 404 ? "not_found" : "query_failed", e.message);
    throw e;
  }
}

export function Preview({ id, version }: { id: string; version: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let host: DashboardHost | null = null;
    let cancelled = false;
    setError(null);
    (async () => {
      const bundle = await get<{ manifest: Manifest; html?: string; frameUrl?: string }>(`/api/drafts/${enc(id)}/bundle`);
      const runtime = bundle.frameUrl ? "" : await runtimeSource();
      if (cancelled || !container.current) return;
      host = mountDashboard({
        container: container.current,
        manifest: bundle.manifest,
        html: bundle.html ?? "",
        runtime,
        ...(bundle.frameUrl ? { frameUrl: bundle.frameUrl } : {}),
        theme: { mode: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light" },
        runQuery: (request) => post(`/api/drafts/${enc(id)}/query`, request),
        runDomain: (request) => post(`/api/drafts/${enc(id)}/domain`, request),
        onDashboardError: (message) => setError(message),
      });
    })().catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
      host?.destroy();
    };
  }, [id, version]);
  return (
    <>
      {error ? <Problem error={new Error(error)} /> : null}
      <div ref={container} className="viewer" />
    </>
  );
}

/** One line per event, in plain words; the person's own messages and Claude's replies stand out. */
export function ChatLog({ events }: { events: AgentEvent[] }) {
  const end = useRef<HTMLLIElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [events.length]);
  return (
    <ol className="chat-log" aria-live="polite">
      {events.map((e, i) => (
        <li key={i} className={`chat-${e.type}${e.error ? " chat-error" : ""}`}>
          {e.type === "user" ? <strong>You: </strong> : null}
          {e.type === "assistant" || e.type === "done" ? <strong>Claude: </strong> : null}
          {e.text}
        </li>
      ))}
      <li ref={end} aria-hidden="true" />
    </ol>
  );
}

function Chat({ draft, onChanged }: { draft: string; onChanged: () => void }) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [message, setMessage] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const source = useRef<EventSource | null>(null);
  useEffect(() => {
    return () => source.current?.close();
  }, []);

  function follow(session: string) {
    source.current?.close();
    const stream = new EventSource(`/api/agent-sessions/${enc(session)}/events`);
    source.current = stream;
    const seen = new Set<string>();
    const add = (raw: MessageEvent<string>) => {
      const key = raw.data;
      if (seen.has(key)) return;
      seen.add(key);
      const event = JSON.parse(raw.data) as AgentEvent;
      if (event.type === "user") return; // already shown when sent
      setEvents((all) => [...all, event]);
      if (event.type === "draft" || event.type === "done") onChanged();
    };
    for (const type of ["assistant", "tool", "tool_result", "draft", "status", "error", "done"]) {
      stream.addEventListener(type, add as EventListener);
    }
    stream.addEventListener("end", () => {
      stream.close();
      setRunning(false);
    });
    stream.onerror = () => {
      stream.close();
      setRunning(false);
    };
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text || running) return;
    setError(null);
    setEvents((all) => [...all, { type: "user", text }]);
    setMessage("");
    setRunning(true);
    try {
      const session = await send<{ id: string; mode: string }>("POST", `/api/drafts/${enc(draft)}/chat`, { message: text });
      follow(session.id);
    } catch (err) {
      setRunning(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section className="chat" aria-label="Edit with Claude">
      <h2>Edit with Claude</h2>
      <p className="muted small">
        Claude changes this draft as you, with your permissions. Nothing is published until you confirm.
      </p>
      <ChatLog events={events} />
      {error ? <p className="problem" role="alert">{error}</p> : null}
      <form onSubmit={(e) => void submit(e)} className="chat-form">
        <label className="sr-only" htmlFor="chat-message">
          What should change?
        </label>
        <textarea
          id="chat-message"
          rows={3}
          value={message}
          placeholder="e.g. Make the region chart stacked and add a YoY column"
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit(e);
          }}
        />
        <button type="submit" className="primary" disabled={running || !message.trim()}>
          {running ? "Working…" : "Send"}
        </button>
      </form>
    </section>
  );
}

function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="checks">
      {checks.map((c) => (
        <li key={c.name} className={`check-${c.status}`}>
          <strong>{c.name}</strong>: {c.status}
          {c.messages.length ? (
            <ul>
              {c.messages.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function PublishPanel({ draft, onDone }: { draft: Draft; onDone: (revision: number) => void }) {
  const [message, setMessage] = useState("");
  const [request, setRequest] = useState<PublishRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="publish" aria-label="Publish">
      <h2>Publish</h2>
      {request === null ? (
        <div className="inline-form">
          <label>
            Note for the revision history{" "}
            <input value={message} maxLength={500} onChange={(e) => setMessage(e.target.value)} />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void act(async () =>
                setRequest(await send<PublishRequest>("POST", `/api/drafts/${enc(draft.id)}/publish-request`, { message })),
              )
            }
          >
            Check and review…
          </button>
        </div>
      ) : (
        <div className="card">
          <p>
            {draft.new ? "This publishes a new dashboard." : `This replaces revision ${draft.baseRevision} for everyone who can see it.`}{" "}
            Review the changes, then confirm.
          </p>
          <Checks checks={request.validation.checks} />
          <pre className="diff">{request.diff || "(no changes)"}</pre>
          <div className="toolbar">
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  const out = await send<{ revision: number }>("POST", `/api/drafts/${enc(draft.id)}/publish`, {
                    confirmation: request.confirmation,
                  });
                  onDone(out.revision);
                })
              }
            >
              Confirm and publish
            </button>
            <button type="button" onClick={() => setRequest(null)}>
              Back
            </button>
          </div>
        </div>
      )}
      {error ? (
        <p className="problem" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

export function DraftPage() {
  const { id } = draftRoute.useParams();
  const search = draftRoute.useSearch();
  const client = useQueryClient();
  const [tab, setTab] = useState<"preview" | "changes" | "files">("preview");
  const [published, setPublished] = useState<number | null>(null);
  const draft = useQuery({ queryKey: ["draft", id], queryFn: () => get<Draft>(`/api/drafts/${enc(id)}`) });
  const diff = useQuery({
    queryKey: ["draft-diff", id, draft.data?.updatedAt],
    queryFn: () => get<{ diff: string }>(`/api/drafts/${enc(id)}/diff`),
    enabled: tab === "changes" && Boolean(draft.data),
  });
  if (draft.error) return <Problem error={draft.error} />;
  if (!draft.data) return <p className="page muted">Loading…</p>;
  const d = draft.data;
  if (search.print === "1") return <Preview id={id} version={d.updatedAt} />;
  const refresh = () => void client.invalidateQueries({ queryKey: ["draft", id] });
  if (published !== null || d.status === "published") {
    return (
      <div className="page">
        <h1>Published</h1>
        <p>
          {published !== null ? `Revision ${published} is live. ` : ""}
          <Link to="/dashboards/$id" params={{ id: d.dashboard }}>
            Open the dashboard
          </Link>
        </p>
      </div>
    );
  }
  return (
    <div className="draft">
      <div className="draft-main">
        <div className="viewer-bar">
          <h1>Draft of {d.dashboard}</h1>
          <span className="pill">{d.new ? "new dashboard" : `from revision ${d.baseRevision}`}</span>
          <div className="toolbar">
            {!d.new ? (
              <Link to="/dashboards/$id" params={{ id: d.dashboard }}>
                Published version
              </Link>
            ) : null}
            <button
              type="button"
              onClick={() => {
                if (!confirm("Discard this draft? Published revisions are not affected.")) return;
                void send("DELETE", `/api/drafts/${enc(id)}`).then(refresh);
              }}
            >
              Discard
            </button>
          </div>
        </div>
        <div className="tabs" role="tablist">
          {(["preview", "changes", "files"] as const).map((t) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
              {t === "preview" ? "Preview" : t === "changes" ? "Changes" : "Files"}
            </button>
          ))}
        </div>
        {tab === "preview" ? <Preview id={id} version={d.updatedAt} /> : null}
        {tab === "changes" ? <pre className="diff">{diff.data?.diff || (diff.isLoading ? "…" : "(no changes)")}</pre> : null}
        {tab === "files"
          ? Object.entries(d.files).map(([path, text]) => (
              <details key={path} open={path === "index.html"}>
                <summary>
                  <code>{path}</code>
                </summary>
                <pre className="diff">{text}</pre>
              </details>
            ))
          : null}
        <PublishPanel draft={d} onDone={setPublished} />
      </div>
      <Chat draft={id} onChanged={refresh} />
    </div>
  );
}
