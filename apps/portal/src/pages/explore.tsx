import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { get, type Item, type ProjectInfo, send } from "../api.js";
import { ItemList } from "../components/items.js";
import { PermissionsDialog } from "../components/permissions.js";
import { Problem, useSite } from "../layout.js";
import { explore } from "../router.js";

const ASSET_LABELS: Record<ProjectInfo["assetPermissions"], string> = {
  customizable: "Customizable",
  locked: "Locked (including nested projects)",
  locked_not_nested: "Locked (not including nested projects)",
};

function ProjectTree({ projects, current }: { projects: ProjectInfo[]; current: string | undefined }) {
  const children = (parent: string | null): ProjectInfo[] => projects.filter((p) => p.parent === parent);
  const known = new Set(projects.map((p) => p.id));
  const roots = projects.filter((p) => p.parent === null || !known.has(p.parent));
  const render = (list: ProjectInfo[]) => (
    <ul>
      {list.map((p) => (
        <li key={p.id}>
          <Link
            to="/explore"
            search={{ project: p.id }}
            aria-current={p.id === current ? "page" : undefined}
            className={p.id === current ? "current" : undefined}
          >
            {p.name}
          </Link>{" "}
          <span className="muted small">{p.items}</span>
          {children(p.id).length ? render(children(p.id)) : null}
        </li>
      ))}
    </ul>
  );
  return (
    <nav aria-label="Projects" className="tree">
      <Link to="/explore" search={{}} className={current ? undefined : "current"}>
        All content
      </Link>
      {render(roots)}
    </nav>
  );
}

function NewProject({ parent, onDone }: { parent: string | undefined; onDone: () => void }) {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => {
      const id = name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 100);
      return send("POST", "/api/projects", { id, name, parent: parent ?? null });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["projects"] });
      onDone();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };
  return (
    <form onSubmit={submit} className="inline-form">
      <label>
        New {parent ? "nested " : ""}project <input value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <button type="submit" disabled={create.isPending}>
        Create
      </button>
      {create.error ? <Problem error={create.error} /> : null}
    </form>
  );
}

function ProjectHeader({ project, onChanged }: { project: ProjectInfo; onChanged: () => void }) {
  const [permissions, setPermissions] = useState(false);
  const [adding, setAdding] = useState(false);
  const client = useQueryClient();
  const setMode = useMutation({
    mutationFn: (mode: ProjectInfo["assetPermissions"]) =>
      send("PATCH", `/api/projects/${encodeURIComponent(project.id)}`, { assetPermissions: mode }),
    onSuccess: () => void client.invalidateQueries(),
  });
  return (
    <div className="project-head">
      <h1>{project.path.join(" / ")}</h1>
      <p className="muted">
        Asset permissions: {ASSET_LABELS[project.assetPermissions]}
        {project.owner ? ` · Owner: ${project.owner}` : ""}
      </p>
      {project.canManage ? (
        <div className="toolbar">
          <button type="button" onClick={() => setPermissions(true)}>
            Permissions…
          </button>
          <label>
            Asset permissions{" "}
            <select
              value={project.assetPermissions}
              onChange={(e) => setMode.mutate(e.target.value as ProjectInfo["assetPermissions"])}
            >
              {Object.entries(ASSET_LABELS).map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => setAdding((v) => !v)}>
            New nested project
          </button>
        </div>
      ) : null}
      {adding ? (
        <NewProject
          parent={project.id}
          onDone={() => {
            setAdding(false);
            onChanged();
          }}
        />
      ) : null}
      {permissions ? <PermissionsDialog kind="project" id={project.id} onClose={() => setPermissions(false)} /> : null}
    </div>
  );
}

export function ExplorePage() {
  const search = explore.useSearch();
  const navigate = useNavigate();
  const site = useSite();
  const [creating, setCreating] = useState(false);
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => get<ProjectInfo[]>("/api/projects") });
  const content = useQuery({
    queryKey: ["content", search],
    queryFn: () => get<Item[]>("/api/content", { project: search.project, q: search.q, kind: search.kind }),
  });
  const project = projects.data?.find((p) => p.id === search.project);
  return (
    <div className="explore">
      <aside>{projects.data ? <ProjectTree projects={projects.data} current={search.project} /> : null}</aside>
      <div className="page">
        {project ? (
          <ProjectHeader project={project} onChanged={() => void projects.refetch()} />
        ) : (
          <h1>{search.q ? `Results for “${search.q}”` : "All content"}</h1>
        )}
        <div className="toolbar">
          <label>
            Show{" "}
            <select
              value={search.kind ?? ""}
              onChange={(e) => {
                const rest = Object.fromEntries(Object.entries(search).filter(([k]) => k !== "kind"));
                void navigate({ to: "/explore", search: e.target.value ? { ...rest, kind: e.target.value } : rest });
              }}
            >
              <option value="">Everything</option>
              <option value="dashboard">Dashboards</option>
              <option value="model">Data models</option>
            </select>
          </label>
          {!project && site.data?.canCreateProjects && site.data.database ? (
            <button type="button" onClick={() => setCreating((v) => !v)}>
              New project
            </button>
          ) : null}
        </div>
        {creating ? <NewProject parent={undefined} onDone={() => setCreating(false)} /> : null}
        {content.error ? <Problem error={content.error} /> : null}
        <ItemList items={content.data ?? []} empty={content.isPending ? "Loading…" : "No content here."} />
      </div>
    </div>
  );
}
