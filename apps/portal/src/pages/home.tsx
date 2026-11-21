import { useQuery } from "@tanstack/react-query";

import { get, type Home } from "../api.js";
import { ItemList } from "../components/items.js";
import { Problem, useMe } from "../layout.js";

export function HomePage() {
  const me = useMe();
  const home = useQuery({ queryKey: ["home"], queryFn: () => get<Home>("/api/home") });
  if (home.error) return <Problem error={home.error} />;
  return (
    <div className="page">
      <h1>Welcome{me.data ? `, ${me.data.fullname}` : ""}</h1>
      <section aria-labelledby="favorites">
        <h2 id="favorites">Favorites</h2>
        <ItemList items={home.data?.favorites ?? []} empty="Star a dashboard or model to keep it here." />
      </section>
      <section aria-labelledby="recents">
        <h2 id="recents">Recently viewed</h2>
        <ItemList items={home.data?.recents ?? []} empty="Nothing viewed yet." />
      </section>
      <section aria-labelledby="updated">
        <h2 id="updated">Recently updated</h2>
        <ItemList items={home.data?.recentlyUpdated ?? []} empty="Nothing updated yet." />
      </section>
    </div>
  );
}
