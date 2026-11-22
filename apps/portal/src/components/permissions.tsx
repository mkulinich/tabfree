import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";

import {
  type ContentPermissions,
  type Decision,
  type Effective,
  enc,
  get,
  label,
  type ProjectPermissions,
  type RuleJson,
  send,
} from "../api.js";
import { Problem } from "../layout.js";

type Target = "project" | "dashboard" | "model";
const CONTENT_TYPES: { key: string; title: string }[] = [
  { key: "project", title: "Project" },
  { key: "dashboard", title: "Dashboards" },
  { key: "model", title: "Data models" },
];
const NEXT: Record<string, Decision | undefined> = { none: "allow", allow: "deny", deny: undefined };

function grantee(rule: RuleJson): string {
  return rule.user ? `user:${rule.user}` : `group:${rule.group ?? ""}`;
}

/** The rules grid: one row per user or group, one tri-state cell (unspecified/allowed/denied) per capability. */
export function RulesGrid({
  rules,
  capabilities,
  onChange,
  readOnly,
}: {
  rules: RuleJson[];
  capabilities: string[];
  onChange: (rules: RuleJson[]) => void;
  readOnly: boolean;
}) {
  const [who, setWho] = useState("");
  const [isUser, setIsUser] = useState(false);
  const cycle = (index: number, capability: string) => {
    const next = rules.map((r, i) => {
      if (i !== index) return r;
      const value = NEXT[r.capabilities[capability] ?? "none"];
      const caps = Object.fromEntries(Object.entries(r.capabilities).filter(([c]) => c !== capability));
      return { ...r, capabilities: value ? { ...caps, [capability]: value } : caps };
    });
    onChange(next);
  };
  const add = (event: FormEvent) => {
    event.preventDefault();
    const name = who.trim();
    if (!name) return;
    const rule: RuleJson = isUser
      ? { user: name, group: null, capabilities: {} }
      : { user: null, group: name, capabilities: {} };
    if (!rules.some((r) => grantee(r) === grantee(rule))) onChange([...rules, rule]);
    setWho("");
  };
  return (
    <>
      <div className="grid-scroll">
        <table className="rules">
          <thead>
            <tr>
              <th scope="col">User or group</th>
              {capabilities.map((c) => (
                <th scope="col" key={c}>
                  <span className="vertical">{label(c)}</span>
                </th>
              ))}
              {readOnly ? null : (
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {rules.map((rule, i) => (
              <tr key={grantee(rule)}>
                <th scope="row">{rule.user ? `👤 ${rule.user}` : `👥 ${rule.group}`}</th>
                {capabilities.map((c) => {
                  const value = rule.capabilities[c];
                  const text = value === "allow" ? "Allowed" : value === "deny" ? "Denied" : "Unspecified";
                  return (
                    <td key={c}>
                      <button
                        type="button"
                        className={`cell ${value ?? "none"}`}
                        disabled={readOnly}
                        aria-label={`${rule.user ?? rule.group}: ${label(c)} ${text}`}
                        title={text}
                        onClick={() => cycle(i, c)}
                      >
                        {value === "allow" ? "✓" : value === "deny" ? "✕" : "·"}
                      </button>
                    </td>
                  );
                })}
                {readOnly ? null : (
                  <td>
                    <button type="button" onClick={() => onChange(rules.filter((_, j) => j !== i))}>
                      Remove
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {readOnly ? null : (
        <form onSubmit={add} className="inline-form">
          <select value={isUser ? "user" : "group"} onChange={(e) => setIsUser(e.target.value === "user")} aria-label="Grantee type">
            <option value="group">Group</option>
            <option value="user">User</option>
          </select>
          <input value={who} onChange={(e) => setWho(e.target.value)} aria-label="User or group name" placeholder="Name" />
          <button type="submit">Add rule</button>
        </form>
      )}
    </>
  );
}

function EffectiveCheck({ kind, id }: { kind: Target; id: string }) {
  const [user, setUser] = useState("");
  const [asked, setAsked] = useState("");
  const result = useQuery({
    queryKey: ["effective", kind, id, asked],
    queryFn: () => get<Effective>(`/api/permissions/${kind}/${enc(id)}/effective`, { user: asked }),
    enabled: asked !== "",
  });
  return (
    <section aria-labelledby="effective" className="effective">
      <h3 id="effective">Effective permissions</h3>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          setAsked(user.trim());
        }}
      >
        <input aria-label="Username" placeholder="Username" value={user} onChange={(e) => setUser(e.target.value)} />
        <button type="submit">Check</button>
      </form>
      {result.error ? <Problem error={result.error} /> : null}
      {result.data ? (
        <table className="effective-table">
          <caption>
            {result.data.user} ({result.data.siteRole})
          </caption>
          <tbody>
            {Object.entries(result.data.capabilities).map(([c, v]) => (
              <tr key={c}>
                <th scope="row">{label(c)}</th>
                <td className={v.allowed ? "allowed" : "denied"}>{v.allowed ? "Allowed" : "Denied"}</td>
                <td className="muted small">{v.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

export function PermissionsDialog({ kind, id, onClose }: { kind: Target; id: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const client = useQueryClient();
  const [contentType, setContentType] = useState<string>(kind === "project" ? "dashboard" : kind);
  const [draft, setDraft] = useState<RuleJson[] | null>(null);
  const data = useQuery({
    queryKey: ["permissions", kind, id],
    queryFn: () => get<ContentPermissions | ProjectPermissions>(`/api/permissions/${kind}/${enc(id)}`),
  });
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => setDraft(null), [contentType]);
  const save = useMutation({
    mutationFn: (rules: RuleJson[]) =>
      send("PUT", `/api/permissions/${kind}/${enc(id)}`, kind === "project" ? { rules, contentType } : { rules }),
    onSuccess: () => {
      setDraft(null);
      void client.invalidateQueries();
    },
  });
  const value = data.data;
  const current: RuleJson[] =
    draft ??
    (value
      ? value.kind === "project"
        ? (value.defaults[contentType] ?? [])
        : value.rules
      : []);
  const caps = value
    ? [
        ...(value.capabilities[kind === "project" ? contentType : kind] ?? []),
        ...(kind === "project" && contentType === "project" ? ["project_leader"] : []),
      ]
    : [];
  const locked = value && value.kind !== "project" ? value.lockedBy : null;
  return (
    <dialog ref={dialog} className="dialog" onClose={onClose} aria-labelledby="perm-title">
      <header>
        <h2 id="perm-title">Permissions: {id}</h2>
        <button type="button" onClick={() => dialog.current?.close()} aria-label="Close">
          ✕
        </button>
      </header>
      {data.error ? <Problem error={data.error} /> : null}
      {locked ? (
        <p className="note">
          Locked to project <strong>{locked}</strong>: its default rules apply. Change them on the project.
        </p>
      ) : null}
      {kind === "project" ? (
        <div role="tablist" className="tabs">
          {CONTENT_TYPES.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={contentType === t.key}
              onClick={() => setContentType(t.key)}
            >
              {t.title}
            </button>
          ))}
        </div>
      ) : null}
      {value ? <RulesGrid rules={current} capabilities={caps} onChange={setDraft} readOnly={Boolean(locked)} /> : null}
      {save.error ? <Problem error={save.error} /> : null}
      <div className="toolbar end">
        <button type="button" onClick={() => setDraft(null)} disabled={!draft}>
          Discard changes
        </button>
        <button type="button" className="primary" disabled={!draft || save.isPending} onClick={() => draft && save.mutate(draft)}>
          Save
        </button>
      </div>
      <EffectiveCheck kind={kind} id={id} />
    </dialog>
  );
}
