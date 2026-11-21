import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { ApiError, get, type Me, ROLE_LABELS, send, type SiteInfo } from "./api.js";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/api/me") });
}

export function useSite() {
  return useQuery({ queryKey: ["site"], queryFn: () => get<SiteInfo>("/api/site") });
}

export function Layout() {
  const me = useMe();
  const site = useSite();
  const navigate = useNavigate();
  const [q, setQ] = useState("");

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
