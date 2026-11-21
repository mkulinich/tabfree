import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { enc, type Item, send, when } from "../api.js";

export function FavoriteButton({ item }: { item: Pick<Item, "kind" | "id" | "favorite" | "name"> }) {
  const client = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => send(item.favorite ? "DELETE" : "PUT", `/api/favorites/${item.kind}/${enc(item.id)}`),
    onSettled: () => void client.invalidateQueries(),
  });
  return (
    <button
      type="button"
      className={`star${item.favorite ? " on" : ""}`}
      aria-pressed={item.favorite}
      aria-label={item.favorite ? `Remove ${item.name} from favorites` : `Add ${item.name} to favorites`}
      onClick={() => toggle.mutate()}
      disabled={toggle.isPending}
    >
      {item.favorite ? "★" : "☆"}
    </button>
  );
}

export function ItemLink({ item }: { item: Item }) {
  return item.kind === "dashboard" ? (
    <Link to="/dashboards/$id" params={{ id: item.id }}>
      {item.name}
    </Link>
  ) : (
    <Link to="/models/$id" params={{ id: item.id }}>
      {item.name}
    </Link>
  );
}

export function ItemList({ items, empty, favorites = true }: { items: Item[]; empty: string; favorites?: boolean }) {
  if (!items.length) return <p className="muted">{empty}</p>;
  return (
    <table className="items">
      <thead>
        <tr>
          {favorites ? (
            <th scope="col">
              <span className="sr-only">Favorite</span>
            </th>
          ) : null}
          <th scope="col">Name</th>
          <th scope="col">Type</th>
          <th scope="col">Project</th>
          <th scope="col">Owner</th>
          <th scope="col">Updated</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={`${item.kind}:${item.id}`}>
            {favorites ? (
              <td>
                <FavoriteButton item={item} />
              </td>
            ) : null}
            <td>
              <ItemLink item={item} />
              {item.description ? <div className="muted small">{item.description}</div> : null}
            </td>
            <td>{item.kind === "dashboard" ? "Dashboard" : "Data model"}</td>
            <td>{item.projectPath.join(" / ") || "—"}</td>
            <td>{item.owner ?? "—"}</td>
            <td>{when(item.updatedAt)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
