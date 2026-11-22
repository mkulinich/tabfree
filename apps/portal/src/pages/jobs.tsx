/**
 * Subscriptions, alerts and scheduled jobs (P6.1-P6.4): yours, or the whole site's for administrators.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { enc, get, send, when } from "../api.js";
import { Problem, useSite } from "../layout.js";

interface Subscription {
  id: string;
  dashboard_id: string;
  subject: string;
  status: string;
  schedule_id: string | null;
  after_refresh_of: string | null;
  last_sent_at: string | null;
  owner: string;
}

interface Alert {
  id: string;
  title: string;
  dashboard_id: string;
  firing: boolean;
  last_checked_at: string | null;
  owner: string;
}

interface Job {
  id: number;
  kind: string;
  status: string;
  attempts: number;
  created_by: string | null;
  created_at: string;
  finished_at: string | null;
  error: string | null;
}

interface Schedule {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  next_run_at: string | null;
}

const STATUS: Record<string, string> = {
  active: "Active",
  paused: "Paused",
  pending_confirmation: "Waiting for you to confirm external recipients",
};

export function JobsPage() {
  const client = useQueryClient();
  const site = useSite();
  const refresh = () => void client.invalidateQueries();
  const subs = useQuery({ queryKey: ["subscriptions"], queryFn: () => get<Subscription[]>("/api/subscriptions") });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => get<Alert[]>("/api/alerts") });
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => get<{ jobs: Job[]; counts: Record<string, number> }>("/api/jobs", { limit: "50" }),
    refetchInterval: 10_000,
  });
  const act = useMutation({
    mutationFn: ({ method, path }: { method: "POST" | "DELETE"; path: string }) => send(method, path),
    onSuccess: refresh,
  });
  const error = subs.error ?? alerts.error ?? jobs.error ?? act.error;
  return (
    <div className="page">
      <h1>Subscriptions and alerts</h1>
      {error ? <Problem error={error} /> : null}
      <section aria-label="Subscriptions">
        <h2>Subscriptions</h2>
        {subs.data?.length === 0 ? <p className="muted">None yet: use Subscribe… on a dashboard.</p> : null}
        <table className="items">
          <tbody>
            {(subs.data ?? []).map((s) => (
              <tr key={s.id}>
                <td>
                  <Link to="/dashboards/$id" params={{ id: s.dashboard_id }}>
                    {s.subject}
                  </Link>
                  <div className="muted small">
                    {STATUS[s.status] ?? s.status} · {s.schedule_id ? "on a schedule" : `after ${s.after_refresh_of} refreshes`} · last sent{" "}
                    {s.last_sent_at ? when(s.last_sent_at) : "never"}
                  </div>
                </td>
                <td className="toolbar">
                  {s.status === "pending_confirmation" ? (
                    <button type="button" onClick={() => act.mutate({ method: "POST", path: `/api/subscriptions/${enc(s.id)}/confirm` })}>
                      Confirm external recipients
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => act.mutate({ method: "POST", path: `/api/subscriptions/${enc(s.id)}/${s.status === "paused" ? "resume" : "pause"}` })}
                    >
                      {s.status === "paused" ? "Resume" : "Pause"}
                    </button>
                  )}
                  <button type="button" onClick={() => act.mutate({ method: "POST", path: `/api/subscriptions/${enc(s.id)}/send` })}>
                    Send now
                  </button>
                  <button type="button" onClick={() => act.mutate({ method: "DELETE", path: `/api/subscriptions/${enc(s.id)}` })}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section aria-label="Alerts">
        <h2>Data alerts</h2>
        {alerts.data?.length === 0 ? <p className="muted">None yet: use Alert… on a dashboard.</p> : null}
        <table className="items">
          <tbody>
            {(alerts.data ?? []).map((a) => (
              <tr key={a.id}>
                <td>
                  <Link to="/dashboards/$id" params={{ id: a.dashboard_id }}>
                    {a.title}
                  </Link>
                  <div className="muted small">
                    {a.firing ? "Condition met" : "Condition not met"} · checked {a.last_checked_at ? when(a.last_checked_at) : "never"}
                  </div>
                </td>
                <td className="toolbar">
                  <button type="button" onClick={() => act.mutate({ method: "POST", path: `/api/alerts/${enc(a.id)}/check` })}>
                    Check now
                  </button>
                  <button type="button" onClick={() => act.mutate({ method: "DELETE", path: `/api/alerts/${enc(a.id)}` })}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section aria-label="Job history">
        <h2>Job history</h2>
        <table className="items">
          <thead>
            <tr>
              <th scope="col">Job</th>
              <th scope="col">Status</th>
              <th scope="col">Started by</th>
              <th scope="col">When</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {(jobs.data?.jobs ?? []).map((j) => (
              <tr key={j.id}>
                <td>
                  #{j.id} {j.kind}
                  {j.error ? <div className="small problem">{j.error}</div> : null}
                </td>
                <td>
                  {j.status}
                  {j.attempts > 1 ? ` (${j.attempts} attempts)` : ""}
                </td>
                <td>{j.created_by ?? "–"}</td>
                <td>{when(j.finished_at ?? j.created_at)}</td>
                <td>
                  {["failed", "cancelled", "succeeded"].includes(j.status) ? (
                    <button type="button" onClick={() => act.mutate({ method: "POST", path: `/api/jobs/${j.id}/retry` })}>
                      Run again
                    </button>
                  ) : j.status === "queued" ? (
                    <button type="button" onClick={() => act.mutate({ method: "POST", path: `/api/jobs/${j.id}/cancel` })}>
                      Cancel
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {site.data?.admin ? <Schedules /> : null}
    </div>
  );
}

function Schedules() {
  const client = useQueryClient();
  const schedules = useQuery({ queryKey: ["schedules"], queryFn: () => get<Schedule[]>("/api/schedules") });
  const [name, setName] = useState("");
  const [frequency, setFrequency] = useState("daily");
  const [at, setAt] = useState("06:00");
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const create = useMutation({
    mutationFn: () =>
      send("POST", "/api/schedules", {
        name,
        spec: frequency === "weekly" ? { frequency, at, timezone, weekdays: ["mon"] } : { frequency, at, timezone },
      }),
    onSuccess: () => {
      setName("");
      void client.invalidateQueries({ queryKey: ["schedules"] });
    },
  });
  return (
    <section aria-label="Schedules">
      <h2>Schedules</h2>
      {create.error ? <Problem error={create.error} /> : null}
      <ul className="plain">
        {(schedules.data ?? []).map((s) => (
          <li key={s.id}>
            <strong>{s.name}</strong> <span className="muted small">{s.description} · next {s.next_run_at ? when(s.next_run_at) : "–"}</span>
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
          Frequency{" "}
          <select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly (Mondays)</option>
            <option value="monthly">Monthly (1st)</option>
          </select>
        </label>
        <label>
          At <input type="time" value={at} onChange={(e) => setAt(e.target.value)} />
        </label>
        <label>
          Time zone <input value={timezone} onChange={(e) => setTimezone(e.target.value)} />
        </label>
        <button type="submit" disabled={create.isPending}>
          Add schedule
        </button>
      </form>
    </section>
  );
}
