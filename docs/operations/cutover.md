# Cutover from Tableau Server (BUILD_PLAN P7.7)

This runbook moves the last people and dashboards off Tableau Server once the waves (Phase 5) are
approved. Migration tooling stays **read-only** against Tableau throughout; the freeze is a change in
Tableau that a Tableau administrator makes.

| When | Step | Owner |
|---|---|---|
| T−10 days | Announce the date, the freeze window and the new address | Migration lead |
| T−3 days | Freeze changes on Tableau | Tableau administrator |
| T−2 days | Final crawl and delta migration | Migration engineer |
| T−1 day | Review and approve the reopened dashboards | Reviewers |
| T | Switch DNS and turn on URL redirects | Platform engineer |
| T to T+4 weeks | Hypercare | Support rota |
| T+4 weeks | Archive Tableau content and ground truth to S3 | Migration engineer |
| T+6 weeks | Decommission Tableau Server | Tableau administrator |

## 1. Communications (T−10 days)

Send to every Tableau user (the inventory's `users` with a sign-in in the last 183 days):

- the date, and that Tableau becomes read-only from T−3;
- the new address, and that old links keep working (they redirect);
- what changes for them: favorites, custom views and subscriptions came across; alerts whose
  condition could not be decoded need the owner to confirm them (the migration console lists them);
- where to get help during hypercare (a chat channel and office hours).

Send owners of content that is **retired** (no views in the window) the list, and the date it will be
archived. Repeat the message at T−1 and on the day.

## 2. Freeze (T−3 days)

In Tableau, for every project: lock content permissions and remove the *Publish* capability from all
groups except administrators; pause extract refreshes that only feed migrated dashboards; post the
freeze banner. Record the freeze time (UTC) — it is the `since` for the final delta below.

## 3. Final crawl and delta migration (T−2 days)

Crawl again so the inventory has each workbook's `updatedAt`, then download and capture only what
changed (both commands skip unchanged items), and run the factory with `since` set to the time of the
**previous** factory run:

```bash
uv run tabfree-migrate crawl
uv run tabfree-migrate download
uv run tabfree-migrate capture
```

```yaml
# factory.yaml
schema: tabfree/factory@0
since: "2027-08-01T00:00:00Z"   # the last factory run; workbooks updated after it are converted again
...
```

```bash
uv run tabfree-migrate factory factory.yaml --out factory-delta/
uv run python -m tabfree_api.migration import factory-delta/ --by admin --delta
```

The factory converts only workbooks changed after `since` (`summary.delta.unchanged` counts the rest)
and still writes the whole governance plan, so permissions, users and redirects changed in Tableau
come across too. Each report records `workbookUpdatedAt`.

With `--delta`, the importer treats approved dashboards differently from a normal import, which skips
them: each one in the delta folder gets a new draft and goes back to **review** with a
`changed_in_tableau` event. The approved revision stays published until a reviewer approves the new
one (a new revision; the old one can be restored). Run repair and verification on the reopened items
as for a wave:

```bash
uv run python -m tabfree_api.migration repair --by admin
uv run python -m tabfree_api.migration verify --by admin
```

Exit: no item in `review`, `changes_requested` or `manual` that a wave plan marked as required.

## 4. DNS and URL redirects (T)

1. Lower the TTL of the Tableau host name to 60 seconds a day ahead.
2. Point the Tableau host name (for example `tableau.corp.example`) at the TabFree load balancer, and add it
   to the ACM certificate. Add it to `TABFREE_SITE_HOSTS` when several sites are served.
3. Redirects are already loaded: approving an item registers its legacy view URLs
   (`#/views/<workbook>/<sheet>`, `/views/<workbook>/<sheet>`, `/t/<site>/views/...`). The portal
   resolves them through `/api/redirects/resolve`. A legacy URL with no match returns no target, so
   the portal shows its not-found page; list such URLs from the load balancer logs during hypercare.
4. Check: a sample of 50 legacy URLs from the usage window (most viewed first) each lands on its dashboard,
   `tabfree-migrate smoke` against Tableau still works (read-only), and embedded dashboards on
   intranet pages were switched to `/embed.js` (owners were told in the communications).

Rollback: point DNS back at Tableau (still read-only, still serving). Nothing on Tableau has changed.

## 5. Hypercare (T to T+4 weeks)

- A daily 15-minute stand-up on the migration console's open items, failing jobs and alerts.
- Watch the usage page: people from the Tableau inventory who have not signed in yet get a reminder in week 2.
- Watch query p95 and cache hit rate against the load-test targets (`docs/operations/load-test.md`).
- Fix conversion bugs with a corpus case (CLAUDE.md), re-run the factory for the affected workbooks
  and import with `--delta`.
- Exit: two weeks with no severity-1 or severity-2 issue and fewer than 5 open migration items.

## 6. Archive (T+4 weeks)

Copy every workbook, data source download, extract conversion report and ground-truth capture to the
archive bucket (S3 Object Lock in compliance mode, retention per records policy). Retired dashboards
are archived by the factory already (`archive/<dashboard>/archive.json`); for the rest:

```bash
aws s3 sync s3://<raw zone>/blobs/ s3://<archive bucket>/tableau/blobs/
aws s3 sync s3://<raw zone>/ground_truth/ s3://<archive bucket>/tableau/ground_truth/
aws s3 cp inventory.db s3://<archive bucket>/tableau/inventory.db
```

Export the Tableau repository's audit tables for the retention period as well (`harvest-repository`
with `--since-days` covering it).

## 7. Decommission (T+6 weeks)

1. Confirm the archive (object counts against the download manifest) and a TabFree logical backup.
2. Stop Tableau Server, keep its last backup (`tsm maintenance backup`) in the archive bucket.
3. Revoke the migration's Tableau personal access tokens and the repository read-only user.
4. Remove the Tableau licences, hosts and DNS records that are left.
5. Record the decommission in the migration console's notes; Milestone M7.
