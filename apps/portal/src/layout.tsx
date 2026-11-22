import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useEffect, useState } from "react";

import { ApiError, get, type Me, ROLE_LABELS, send, type SiteInfo } from "./api.js";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/api/me") });
}

export function useSite() {
  return useQuery({ queryKey: ["site"], queryFn: () => get<SiteInfo>("/api/site") });
}

/** Old Tableau links ("/#/site/x/views/Workbook/Sheet") keep their path in the fragment (P3.9). */
function useTableauLinks(signedIn: boolean) {
  const navigate = useNavigate();
  const [missing, setMissing] = useState<string | null>(null);
  useEffect(() => {
    const hash = location.hash;
    if (!signedIn || !/^#\/(site\/[^/]+\/)?views\//.test(hash)) return;
    get<{ to: string }>("/api/redirects/resolve", { path: hash })
      .then(({ to }) => {
        history.replaceState(null, "", to);
        void navigate({ to });
      })
      .catch(() => setMissing(hash));
  }, [signedIn, navigate]);
  return missing;
}

/** Sharing links ("/s/<id>" → "/?share=<id>"): resolved after sign-in, so View is checked for the viewer. */
function useShareLinks(signedIn: boolean) {
  const navigate = useNavigate();
  const [missing, setMissing] = useState<string | null>(null);
  useEffect(() => {
    const share = new URLSearchParams(location.search).get("share");
    if (!signedIn || !share) return;
    get<{ dashboard: string; state: { filters?: unknown; parameters?: unknown } }>(`/api/share/${encodeURIComponent(share)}`)
      .then(({ dashboard, state }) => {
        const json = JSON.stringify({ filters: state.filters ?? {}, parameters: state.parameters ?? {} });
        const encoded = btoa(json).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
        void navigate({ to: "/dashboards/$id", params: { id: dashboard }, search: { state: encoded }, replace: true });
      })
      .catch(() => setMissing(share));
  }, [signedIn, navigate]);
  return missing;
}

export function Layout() {
  const me = useMe();
  const site = useSite();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const missingLink = useTableauLinks(Boolean(me.data));
  const missingShare = useShareLinks(Boolean(me.data));

  if (me.error instanceof ApiError && me.error.status === 401) {
    return (
      <main className="signin">
        <h1>TabFree</h1>
        <p>
          <a className="button primary" href={`/auth/login?return=${encodeURIComponent(location.pathname)}`}>
            Sign in
          </a>
        </p>
      </main>
    );
  }

  if (new URLSearchParams(location.search).get("print") === "1") {
    return (
      <main id="main" className="print">
        <Outlet />
      </main>
    );
  }

  const search = (event: FormEvent) => {
    event.preventDefault();
    void navigate({ to: "/explore", search: q ? { q } : {} });
  };
  const signOut = async () => {
    await send("POST", "/auth/logout");
    location.href = "/";
  };

  return (
    <div className="app">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="top">
        <Link to="/" className="brand">
          TabFree
        </Link>
        <nav aria-label="Main">
          <Link to="/" activeOptions={{ exact: true }}>
            Home
          </Link>
          <Link to="/explore">Explore</Link>
          <Link to="/jobs">Subscriptions</Link>
          {site.data?.admin ? <Link to="/admin/users">Manage site</Link> : null}
        </nav>
        <form role="search" onSubmit={search} className="search">
          <input
            type="search"
            aria-label="Search content"
            placeholder="Search dashboards and models"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </form>
        <details className="menu">
          <summary>{me.data?.fullname ?? "…"}</summary>
          <div className="menu-body">
            {me.data ? (
              <p className="muted">
                {me.data.username} · {ROLE_LABELS[me.data.siteRole]}
              </p>
            ) : null}
            <Link to="/account">Account and tokens</Link>
            <button type="button" onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        </details>
      </header>
      <main id="main">
        {missingLink ? (
          <p role="alert" className="note page">
            That Tableau link has no dashboard here, or it is not shared with you.
          </p>
        ) : null}
        {missingShare ? (
          <p role="alert" className="note page">
            That link does not exist, or its dashboard is not shared with you.
          </p>
        ) : null}
        <Outlet />
      </main>
    </div>
  );
}

export function Problem({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : "something went wrong";
  return (
    <p role="alert" className="problem">
      {message}
    </p>
  );
}
