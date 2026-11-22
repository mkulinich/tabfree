# Release checklist

For maintainers cutting a release (GOVERNANCE.md). v1.0 is the first public release; it waits on the
public name (P0.7) and the items marked **v1.0** below.

## Before v1.0 only

- [ ] **v1.0** The public name has cleared trademark review. Rename mechanically (`tabfree`, `tf-`,
      `TABFREE_` identifiers), then set the repository variable `PUBLIC_NAME_CLEARED=true` so the release
      and image workflows can publish.
- [ ] **v1.0** Make the repository public; turn on private vulnerability reporting, branch protection for
      `main` (required checks: ci, security; two reviews for the areas in GOVERNANCE.md), and secret
      scanning push protection.
- [ ] **v1.0** Name the maintainers in MAINTAINERS.md and the security contact in SECURITY.md.
- [ ] **v1.0** Penetration test done ([scope](../security/pentest-scope.md)); findings fixed or accepted
      with a recorded reason.
- [ ] **v1.0** Load test on staging meets the targets ([load testing](load-test.md)); restore drill
      done ([backup and restore](backup-restore.md)).
- [ ] **v1.0** A migration wave of real dashboards approved through the console, and the cutover
      rehearsed on staging ([cutover](cutover.md)).
- [ ] **v1.0** Licence check: `uv export` and `npm ls` show no AGPL, SSPL, BSL or other non-OSI licences
      in shipped code; vendored files keep their notices (`third_party/`).

## Every release

- [ ] `main` is green: `make check`, `make infra-validate`, the security workflow, TDVT conformance.
- [ ] Database migrations upgrade a copy of the previous release's database, and the backup format
      restores into it (`python -m tabfree_api.backup`).
- [ ] Schema versions: any breaking change bumped its `schema` string, with examples and an upgrade path.
- [ ] CHANGELOG.md: move *Unreleased* under the new version and date.
- [ ] Version strings bumped (`apps/api/pyproject.toml`, packages, `infra/helm/tabfree/Chart.yaml`).
- [ ] Tag `vX.Y.Z`: the images workflow builds, signs (cosign, keyless), attaches the SBOM and provenance,
      and scans the image. Tag `compiler-vX.Y.Z` and `tablecalc-vX.Y.Z` when those packages changed: the
      release workflow publishes them to PyPI and npm.
- [ ] Verify the signature and the SBOM of the published image:
      `cosign verify ghcr.io/<owner>/tabfree-api@<digest> --certificate-identity-regexp ... --certificate-oidc-issuer https://token.actions.githubusercontent.com`
- [ ] Docs site rebuilt (`make docs`) with the new API reference (`make openapi`).
- [ ] Release notes: highlights, upgrade steps, schema changes, known issues.
