import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { enc, get, type ModelField, type ModelInfo } from "../api.js";
import { PermissionsDialog } from "../components/permissions.js";
import { RevisionsDialog } from "../components/revisions.js";
import { Problem } from "../layout.js";
import { model } from "../router.js";

function FieldTable({ fields }: { fields: ModelField[] }) {
  return (
    <table className="items">
      <thead>
        <tr>
          <th scope="col">Field</th>
          <th scope="col">Role</th>
          <th scope="col">Type</th>
          <th scope="col">Description or formula</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => (
          <tr key={f.name}>
            <th scope="row">{f.name}</th>
            <td>
              <span className={`pill ${f.role}`}>{f.role === "measure" ? "Measure" : "Dimension"}</span>
              {f.defaultAggregation ? <span className="muted small"> {f.defaultAggregation}</span> : null}
            </td>
            <td>{f.type}</td>
            <td>
              {f.description ? <div>{f.description}</div> : null}
              {f.formula ? <code className="formula">{f.formula}</code> : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ModelPage() {
  const { id } = model.useParams();
  const [filter, setFilter] = useState("");
  const [dialog, setDialog] = useState<"permissions" | "revisions" | null>(null);
  const info = useQuery({ queryKey: ["model", id], queryFn: () => get<ModelInfo>(`/api/models/${enc(id)}`) });
  const folders = useMemo(() => {
    const out = new Map<string, ModelField[]>();
    const needle = filter.toLowerCase();
    for (const f of info.data?.fields ?? []) {
      if (needle && !`${f.name} ${f.description ?? ""}`.toLowerCase().includes(needle)) continue;
      const key = f.folder ?? (f.role === "measure" ? "Measures" : "Dimensions");
      out.set(key, [...(out.get(key) ?? []), f]);
    }
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [info.data, filter]);
  if (info.error) return <Problem error={info.error} />;
  const data = info.data;
  if (!data) return <p className="page muted">Loading…</p>;
  return (
    <div className="page">
      <h1>{data.title}</h1>
      {data.description ? <p>{data.description}</p> : null}
      <p className="muted">
        Engine: {data.engine ?? "—"} · {data.fields.length} fields
        {data.dataVersion ? ` · Data version ${data.dataVersion}` : ""}
      </p>
      <div className="toolbar">
        {data.capabilities["set_permissions"] ? (
          <button type="button" onClick={() => setDialog("permissions")}>
            Permissions…
          </button>
        ) : null}
        {data.capabilities["overwrite"] ? (
          <button type="button" onClick={() => setDialog("revisions")}>
            Revisions…
          </button>
        ) : null}
        <label>
          Find a field <input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </label>
      </div>
      {folders.map(([folder, fields]) => (
        <section key={folder}>
          <h2>{folder}</h2>
          <FieldTable fields={fields} />
        </section>
      ))}
      {data.hierarchies.length ? (
        <section>
          <h2>Hierarchies</h2>
          <ul>
            {data.hierarchies.map((h) => (
              <li key={h.name}>
                <strong>{h.name}</strong>: {h.levels.join(" › ")}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {data.parameters.length ? (
        <section>
          <h2>Parameters</h2>
          <ul>
            {data.parameters.map((p) => (
              <li key={p.name}>
                <strong>{p.name}</strong> ({p.type}){p.default !== undefined ? `, default ${String(p.default)}` : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {data.rls?.length ? (
        <section>
          <h2>Row-level security</h2>
          <ul>
            {data.rls.map((p) => (
              <li key={p.id}>
                <code>{p.id}</code> {p.description}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {dialog === "permissions" ? <PermissionsDialog kind="model" id={id} onClose={() => setDialog(null)} /> : null}
      {dialog === "revisions" ? <RevisionsDialog kind="model" id={id} onClose={() => setDialog(null)} /> : null}
    </div>
  );
}
