import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, type ReactNode, useState } from "react";

import {
  type AdminGroup,
  type AdminSite,
  type AdminUser,
  type AuditEvent,
  enc,
  get,
  ROLE_LABELS,
  send,
  SITE_ROLES,
  type SiteRole,
  when,
} from "../api.js";
import { Problem } from "../layout.js";

function AdminShell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="page">
      <nav aria-label="Site administration" className="tabs">
        <Link to="/admin/users">Users</Link>
        <Link to="/admin/groups">Groups</Link>
        <Link to="/admin/site">Settings</Link>
        <Link to="/admin/audit">Audit log</Link>
        <Link to="/admin/usage">Usage</Link>
        <Link to="/admin/recycle-bin">Recycle bin</Link>
        <Link to="/admin/webhooks">Webhooks</Link>
        <Link to="/migration">Migration</Link>
      </nav>
      <h1>{title}</h1>
      {children}
    </div>
  );
}

function RoleSelect({ value, onChange, label }: { value: SiteRole; onChange: (r: SiteRole) => void; label: string }) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value as SiteRole)}>
      {SITE_ROLES.map((r) => (
        <option key={r} value={r}>
          {ROLE_LABELS[r]}
        </option>
      ))}
    </select>
  );
}

export function AdminUsers() {
  const client = useQueryClient();
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => get<AdminUser[]>("/api/admin/users") });
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<SiteRole>("viewer");
  const done = () => void client.invalidateQueries({ queryKey: ["admin-users"] });
  const add = useMutation({
    mutationFn: () => send("POST", "/api/admin/users", { username, siteRole: role }),
    onSuccess: () => {
      setUsername("");
      done();
    },
  });
  const update = useMutation({
    mutationFn: ({ user, patch }: { user: string; patch: Partial<Pick<AdminUser, "siteRole" | "disabled">> }) =>
      send("PATCH", `/api/admin/users/${enc(user)}`, patch),
    onSettled: done,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    add.mutate();
  };
  return (
    <AdminShell title="Users">
      <form onSubmit={submit} className="inline-form">
        <label>
          Username <input value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <RoleSelect value={role} onChange={setRole} label="Site role for the new user" />
        <button type="submit" disabled={add.isPending}>
          Add user
        </button>
      </form>
      {add.error ? <Problem error={add.error} /> : null}
      {update.error ? <Problem error={update.error} /> : null}
      {users.error ? <Problem error={users.error} /> : null}
      <table className="items">
        <thead>
          <tr>
            <th scope="col">User</th>
            <th scope="col">Site role</th>
            <th scope="col">Effective role</th>
            <th scope="col">Groups</th>
            <th scope="col">Sign-in</th>
            <th scope="col">Last sign-in</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {(users.data ?? []).map((u) => (
            <tr key={u.username} className={u.disabled ? "disabled" : undefined}>
              <th scope="row">
                {u.fullname || u.username}
                <div className="muted small">{u.username}</div>
              </th>
              <td>
                <RoleSelect
                  value={u.siteRole}
                  label={`Site role of ${u.username}`}
                  onChange={(siteRole) => update.mutate({ user: u.username, patch: { siteRole } })}
                />
              </td>
              <td>{ROLE_LABELS[u.effectiveSiteRole]}</td>
              <td>{u.groups.join(", ") || "—"}</td>
              <td>{u.authSource === "local" ? "Local account" : "Identity provider"}</td>
              <td>{when(u.lastSignInAt)}</td>
              <td>
                <button
                  type="button"
                  onClick={() => update.mutate({ user: u.username, patch: { disabled: !u.disabled } })}
                >
                  {u.disabled ? "Enable" : "Disable"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </AdminShell>
  );
}

function GroupEditor({ group, onDone }: { group: AdminGroup | null; onDone: () => void }) {
  const [name, setName] = useState(group?.name ?? "");
  const [members, setMembers] = useState((group?.members ?? []).join("\n"));
  const [minimum, setMinimum] = useState<SiteRole | "">(group?.minimumSiteRole ?? "");
  const save = useMutation({
    mutationFn: () =>
      send("PUT", `/api/admin/groups/${enc(name)}`, {
        members: members
          .split(/[\n,]/)
          .map((m) => m.trim())
          .filter(Boolean),
        minimumSiteRole: minimum || null,
      }),
    onSuccess: onDone,
  });
  return (
    <form
      className="card"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label>
        Group name <input value={name} onChange={(e) => setName(e.target.value)} required disabled={group !== null} />
      </label>
      <label>
        Members (one username per line)
        <textarea rows={5} value={members} onChange={(e) => setMembers(e.target.value)} />
      </label>
      <label>
        Grant role on sign-in{" "}
        <select value={minimum} onChange={(e) => setMinimum(e.target.value as SiteRole | "")}>
          <option value="">None</option>
          {SITE_ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </select>
      </label>
      {save.error ? <Problem error={save.error} /> : null}
      <div className="toolbar">
        <button type="submit" className="primary" disabled={save.isPending}>
          Save group
        </button>
        <button type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export function AdminGroups() {
  const client = useQueryClient();
  const groups = useQuery({ queryKey: ["admin-groups"], queryFn: () => get<AdminGroup[]>("/api/admin/groups") });
  const [editing, setEditing] = useState<AdminGroup | null | "new">(null);
  const refresh = () => {
    setEditing(null);
    void client.invalidateQueries();
  };
  const remove = useMutation({
    mutationFn: (name: string) => send("DELETE", `/api/admin/groups/${enc(name)}`),
    onSettled: refresh,
  });
  return (
    <AdminShell title="Groups">
      {editing ? (
        <GroupEditor group={editing === "new" ? null : editing} onDone={refresh} />
      ) : (
        <button type="button" onClick={() => setEditing("new")}>
          New group
        </button>
      )}
      {groups.error ? <Problem error={groups.error} /> : null}
      <table className="items">
        <thead>
          <tr>
            <th scope="col">Group</th>
            <th scope="col">Members</th>
            <th scope="col">Grant role on sign-in</th>
            <th scope="col">Source</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {(groups.data ?? []).map((g) => (
            <tr key={g.name}>
              <th scope="row">{g.name}</th>
              <td>{g.members.length ? g.members.join(", ") : "—"}</td>
              <td>{g.minimumSiteRole ? ROLE_LABELS[g.minimumSiteRole] : "—"}</td>
              <td>{g.source === "idp" ? "Identity provider" : "Local"}</td>
              <td className="toolbar">
                <button type="button" onClick={() => setEditing(g)}>
                  Edit
                </button>
                <button type="button" onClick={() => remove.mutate(g.name)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </AdminShell>
  );
}

export function AdminSite() {
  const client = useQueryClient();
  const site = useQuery({ queryKey: ["admin-site"], queryFn: () => get<AdminSite>("/api/admin/site") });
  const save = useMutation({
    mutationFn: (patch: Partial<AdminSite>) => send("PATCH", "/api/admin/site", patch),
    onSuccess: () => void client.invalidateQueries(),
  });
  if (site.error) return <AdminShell title="Settings"><Problem error={site.error} /></AdminShell>;
  const data = site.data;
  if (!data) return <AdminShell title="Settings"><p className="muted">Loading…</p></AdminShell>;
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const settings: Record<string, string | number> = {};
    const workgroup = String(form.get("athenaWorkgroup") ?? "").trim();
    const budget = String(form.get("dailyScanBudgetBytes") ?? "").trim();
    const concurrent = String(form.get("maxConcurrentQueries") ?? "").trim();
    if (workgroup) settings["athenaWorkgroup"] = workgroup;
    if (budget) settings["dailyScanBudgetBytes"] = Number(budget);
    if (concurrent) settings["maxConcurrentQueries"] = Number(concurrent);
    save.mutate({
      name: String(form.get("name")),
      defaultSiteRole: form.get("defaultSiteRole") as SiteRole,
      settings,
    });
  };
  return (
    <AdminShell title="Settings">
      <form className="card" onSubmit={submit}>
        <label>
          Site name <input name="name" defaultValue={data.name} required />
        </label>
        <label>
          Role for new users{" "}
          <select name="defaultSiteRole" defaultValue={data.defaultSiteRole}>
            {SITE_ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
        </label>
        <fieldset>
          <legend>Query service</legend>
          <label>
            Athena workgroup <input name="athenaWorkgroup" defaultValue={String(data.settings["athenaWorkgroup"] ?? "")} />
          </label>
          <label>
            Daily bytes-scanned budget{" "}
            <input
              name="dailyScanBudgetBytes"
              inputMode="numeric"
              defaultValue={String(data.settings["dailyScanBudgetBytes"] ?? "")}
            />
          </label>
          <label>
            Concurrent queries{" "}
            <input
              name="maxConcurrentQueries"
              inputMode="numeric"
              defaultValue={String(data.settings["maxConcurrentQueries"] ?? "")}
            />
          </label>
        </fieldset>
        {save.error ? <Problem error={save.error} /> : null}
        {save.isSuccess ? <p role="status">Saved.</p> : null}
        <button type="submit" className="primary" disabled={save.isPending}>
          Save settings
        </button>
      </form>
    </AdminShell>
  );
}

export function AdminAudit() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const events = useQuery({
    queryKey: ["audit", actor, action],
    queryFn: () => get<AuditEvent[]>("/api/admin/audit", { actor, action, limit: "200" }),
  });
  return (
    <AdminShell title="Audit log">
      <div className="toolbar">
        <label>
          User <input value={actor} onChange={(e) => setActor(e.target.value)} />
        </label>
        <label>
          Action{" "}
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">Any</option>
            {[
              "sign_in",
              "sign_out",
              "view",
              "query",
              "explore",
              "export",
              "permission_change",
              "user_update",
              "group_update",
              "project_create",
              "content_restore",
              "token_create",
            ].map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
      </div>
      {events.error ? <Problem error={events.error} /> : null}
      <table className="items">
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">User</th>
            <th scope="col">Action</th>
            <th scope="col">On</th>
            <th scope="col">Outcome</th>
            <th scope="col">Detail</th>
          </tr>
        </thead>
        <tbody>
          {(events.data ?? []).map((e) => (
            <tr key={e.id} className={e.outcome === "deny" ? "denied-row" : undefined}>
              <td>{when(e.at)}</td>
              <td>{e.actor ?? "—"}</td>
              <td>{e.action}</td>
              <td>{e.resourceId ? `${e.resourceKind ?? ""} ${e.resourceId}` : "—"}</td>
              <td>{e.outcome}</td>
              <td>
                <code className="small">{Object.keys(e.detail).length ? JSON.stringify(e.detail) : ""}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </AdminShell>
  );
}

interface Deleted {
  kind: "dashboard" | "model";
  id: string;
  name: string;
  owner: string | null;
  deletedAt: string;
  purgeAfter: string;
}

/** Deleted dashboards and models (P6.6): restore them, or purge them before their 30 days are up. */
export function AdminRecycleBin() {
  const client = useQueryClient();
  const bin = useQuery({ queryKey: ["recycle-bin"], queryFn: () => get<Deleted[]>("/api/recycle-bin") });
  const act = useMutation({
    mutationFn: ({ item, purge }: { item: Deleted; purge: boolean }) =>
      send(purge ? "DELETE" : "POST", `/api/recycle-bin/${item.kind}/${enc(item.id)}${purge ? "" : "/restore"}`),
    onSuccess: () => void client.invalidateQueries(),
  });
  return (
    <AdminShell title="Recycle bin">
      {bin.error ? <Problem error={bin.error} /> : null}
      {act.error ? <Problem error={act.error} /> : null}
      {bin.data?.length === 0 ? <p className="muted">Nothing has been deleted.</p> : null}
      <table className="items">
        <tbody>
          {(bin.data ?? []).map((item) => (
            <tr key={`${item.kind}:${item.id}`}>
              <td>
                {item.name} <span className="muted small">({item.kind})</span>
                <div className="muted small">
                  deleted {when(item.deletedAt)} · owner {item.owner ?? "–"} · purged after {when(item.purgeAfter)}
                </div>
              </td>
              <td className="toolbar">
                <button type="button" onClick={() => act.mutate({ item, purge: false })}>
                  Restore
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (confirm(`Delete ${item.name} for good? This cannot be undone.`)) act.mutate({ item, purge: true });
                  }}
                >
                  Delete for good
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </AdminShell>
  );
}

interface Webhook {
  id: string;
  name: string;
  url: string;
  events: string[];
}

const EVENTS = ["*", "content.published", "content.deleted", "content.restored", "model.refreshed", "job.failed", "comment.created"];

/** Outgoing webhooks (P6.9). The signing secret is shown once, when the webhook is created. */
export function AdminWebhooks() {
  const client = useQueryClient();
  const hooks = useQuery({ queryKey: ["webhooks"], queryFn: () => get<Webhook[]>("/api/webhooks") });
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [event, setEvent] = useState("*");
  const [secret, setSecret] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => send<{ secret: string }>("POST", "/api/webhooks", { name, url, events: [event] }),
    onSuccess: (out) => {
      setSecret(out.secret);
      setName("");
      setUrl("");
      void client.invalidateQueries({ queryKey: ["webhooks"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => send("DELETE", `/api/webhooks/${enc(id)}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["webhooks"] }),
  });
  return (
    <AdminShell title="Webhooks">
      {hooks.error ? <Problem error={hooks.error} /> : null}
      {create.error ? <Problem error={create.error} /> : null}
      {secret ? (
        <p role="status" className="note">
          Signing secret (shown once): <code>{secret}</code>. Verify <code>X-TabFree-Signature</code> with it.
        </p>
      ) : null}
      <ul className="plain">
        {(hooks.data ?? []).map((h) => (
          <li key={h.id}>
            <strong>{h.name}</strong> <span className="muted small">{h.url} · {h.events.join(", ")}</span>{" "}
            <button type="button" onClick={() => remove.mutate(h.id)}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <form
        className="inline-form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <label>
          Name <input value={name} required maxLength={200} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          URL <input type="url" value={url} required placeholder="https://" onChange={(e) => setUrl(e.target.value)} />
        </label>
        <label>
          Event{" "}
          <select value={event} onChange={(e) => setEvent(e.target.value)}>
            {EVENTS.map((ev) => (
              <option key={ev} value={ev}>
                {ev === "*" ? "All events" : ev}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={create.isPending}>
          Add webhook
        </button>
      </form>
    </AdminShell>
  );
}

interface Usage {
  days: number;
  viewsPerDay: { day: string; views: number; viewers: number }[];
  topDashboards: { dashboard: string; title: string; views: number; viewers: number; p50Ms: number | null; p95Ms: number | null }[];
  activeUsers: number;
  exports: number;
  queries: {
    count: number;
    p50Ms: number | null;
    p95Ms: number | null;
    cacheHitRate: number | null;
    scannedBytes: number;
    slowest: { resource: string; p95Ms: number; queries: number }[];
  };
  jobs: { kind: string; status: string; count: number }[];
  jobFailures: { id: number; kind: string; error: string; at: string | null }[];
  storage: Record<string, number>;
}

const ms = (v: number | null) => (v === null ? "–" : `${Math.round(v)} ms`);

/** Usage and performance (P6.7): views, people, query latency, cache, jobs and storage. */
export function AdminUsage() {
  const [days, setDays] = useState(30);
  const usage = useQuery({ queryKey: ["usage", days], queryFn: () => get<Usage>("/api/admin/usage", { days: String(days) }) });
  const u = usage.data;
  const peak = Math.max(1, ...(u?.viewsPerDay ?? []).map((d) => d.views));
  return (
    <AdminShell title="Usage">
      <label>
        Period{" "}
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </label>
      {usage.error ? <Problem error={usage.error} /> : null}
      {u ? (
        <>
          <dl className="stats">
            <div>
              <dt>Active people</dt>
              <dd>{u.activeUsers}</dd>
            </div>
            <div>
              <dt>Queries</dt>
              <dd>{u.queries.count}</dd>
            </div>
            <div>
              <dt>Median / 95th percentile</dt>
              <dd>
                {ms(u.queries.p50Ms)} / {ms(u.queries.p95Ms)}
              </dd>
            </div>
            <div>
              <dt>Cache hit rate</dt>
              <dd>{u.queries.cacheHitRate === null ? "–" : `${Math.round(u.queries.cacheHitRate * 100)}%`}</dd>
            </div>
            <div>
              <dt>Data scanned</dt>
              <dd>{(u.queries.scannedBytes / 1e9).toFixed(2)} GB</dd>
            </div>
            <div>
              <dt>Exports</dt>
              <dd>{u.exports}</dd>
            </div>
          </dl>
          <h2>Views per day</h2>
          <table className="items bars">
            <tbody>
              {u.viewsPerDay.map((d) => (
                <tr key={d.day}>
                  <th scope="row">{d.day}</th>
                  <td>
                    <span className="bar" style={{ width: `${(100 * d.views) / peak}%` }} aria-hidden="true" />
                    {d.views} views, {d.viewers} people
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <h2>Most used dashboards</h2>
          <table className="items">
            <thead>
              <tr>
                <th scope="col">Dashboard</th>
                <th scope="col">Views</th>
                <th scope="col">People</th>
                <th scope="col">Median query</th>
                <th scope="col">95th percentile</th>
              </tr>
            </thead>
            <tbody>
              {u.topDashboards.map((d) => (
                <tr key={d.dashboard}>
                  <td>
                    <Link to="/dashboards/$id" params={{ id: d.dashboard }}>
                      {d.title}
                    </Link>
                  </td>
                  <td>{d.views}</td>
                  <td>{d.viewers}</td>
                  <td>{ms(d.p50Ms)}</td>
                  <td>{ms(d.p95Ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h2>Jobs</h2>
          <table className="items">
            <tbody>
              {u.jobs.map((j) => (
                <tr key={`${j.kind}:${j.status}`}>
                  <td>{j.kind}</td>
                  <td>{j.status}</td>
                  <td>{j.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {u.jobFailures.length ? (
            <ul className="plain">
              {u.jobFailures.map((f) => (
                <li key={f.id} className="small">
                  #{f.id} {f.kind}: {f.error}
                </li>
              ))}
            </ul>
          ) : null}
          <h2>Storage</h2>
          <p className="muted">
            {u.storage["dashboards"]} dashboards, {u.storage["models"]} models, {u.storage["revisions"]} revisions,{" "}
            {u.storage["openDrafts"]} open drafts, {u.storage["recycleBin"]} in the recycle bin
          </p>
        </>
      ) : null}
    </AdminShell>
  );
}
