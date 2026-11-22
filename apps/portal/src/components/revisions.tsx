import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { enc, get, type Kind, type Revision, send, when } from "../api.js";
import { Problem } from "../layout.js";

export function RevisionsDialog({ kind, id, onClose }: { kind: Kind; id: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const client = useQueryClient();
  const history = useQuery({
    queryKey: ["revisions", kind, id],
    queryFn: () => get<Revision[]>(`/api/content/${kind}/${enc(id)}/revisions`),
  });
  const restore = useMutation({
    mutationFn: (n: number) => send("POST", `/api/content/${kind}/${enc(id)}/revisions/${n}/restore`),
    onSuccess: () => void client.invalidateQueries(),
  });
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const latest = history.data?.at(-1)?.number;
  return (
    <dialog ref={dialog} className="dialog" onClose={onClose} aria-labelledby="rev-title">
      <header>
        <h2 id="rev-title">Revision history</h2>
        <button type="button" onClick={() => dialog.current?.close()} aria-label="Close">
          ✕
        </button>
      </header>
      {history.error ? <Problem error={history.error} /> : null}
      {restore.error ? <Problem error={restore.error} /> : null}
      <table className="items">
        <thead>
          <tr>
            <th scope="col">Revision</th>
            <th scope="col">When</th>
            <th scope="col">By</th>
            <th scope="col">Message</th>
            <th scope="col">
              <span className="sr-only">Restore</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {[...(history.data ?? [])].reverse().map((r) => (
            <tr key={r.number}>
              <td>{r.number}</td>
              <td>{when(r.createdAt)}</td>
              <td>{r.createdBy ?? "—"}</td>
              <td>{r.message}</td>
              <td>
                {r.number === latest ? (
                  <span className="muted">Current</span>
                ) : (
                  <button type="button" onClick={() => restore.mutate(r.number)} disabled={restore.isPending}>
                    Restore
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </dialog>
  );
}
