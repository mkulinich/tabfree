import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { enc, get, send, type Token, when } from "../api.js";
import { Problem, useMe, useSite } from "../layout.js";

export function AccountPage() {
  const me = useMe();
  const site = useSite();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const tokens = useQuery({
    queryKey: ["tokens"],
    queryFn: () => get<Token[]>("/api/tokens"),
    enabled: Boolean(site.data?.database),
  });
  const create = useMutation({
    mutationFn: () => send<{ token: string }>("POST", "/api/tokens", { name }),
    onSuccess: (result) => {
      setCreated(result.token);
      setName("");
      void client.invalidateQueries({ queryKey: ["tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => send("DELETE", `/api/tokens/${enc(id)}`),
    onSettled: () => void client.invalidateQueries({ queryKey: ["tokens"] }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <div className="page">
      <h1>Account</h1>
      {me.data ? (
        <dl className="facts">
          <dt>Username</dt>
          <dd>{me.data.username}</dd>
          <dt>Groups</dt>
          <dd>{me.data.groups.join(", ") || "—"}</dd>
        </dl>
      ) : null}
      <h2>Personal access tokens</h2>
      <p className="muted">
        For scripts and the MCP server: send <code>Authorization: Bearer &lt;token&gt;</code>. A token stops working
        after 15 days without use.
      </p>
      {site.data && !site.data.database ? (
        <p className="note">Tokens need the metadata database.</p>
      ) : (
        <>
          <form onSubmit={submit} className="inline-form">
            <label>
              Token name <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
            </label>
            <button type="submit" disabled={create.isPending}>
              Create token
            </button>
          </form>
          {create.error ? <Problem error={create.error} /> : null}
          {created ? (
            <div className="note" role="status">
              <p>Copy this token now; it is not shown again.</p>
              <code className="token">{created}</code>
            </div>
          ) : null}
          <table className="items">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Created</th>
                <th scope="col">Last used</th>
                <th scope="col">Expires</th>
                <th scope="col">
                  <span className="sr-only">Revoke</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {(tokens.data ?? []).map((t) => (
                <tr key={t.id} className={t.revoked ? "disabled" : undefined}>
                  <th scope="row">{t.name}</th>
                  <td>{when(t.createdAt)}</td>
                  <td>{when(t.lastUsedAt)}</td>
                  <td>{when(t.expiresAt)}</td>
                  <td>
                    {t.revoked ? (
                      "Revoked"
                    ) : (
                      <button type="button" onClick={() => revoke.mutate(t.id)}>
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
