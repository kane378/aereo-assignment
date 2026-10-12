# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Report them privately via GitHub's
"Report a vulnerability" button (Security tab) or by email to the repository owner, with steps to reproduce.

## Threat model

**Assets:** server availability, the host filesystem, uploaded data and the measurement results.

**Attacker:** anyone who can reach the API and send arbitrary files and requests.

**Trust boundary:** every byte of an uploaded file, its file names, archive metadata, XML and CRS definitions
is untrusted. Nothing from an upload is used to build a filesystem path, a SQL string or a shell command.

| Area | Control | Where |
|---|---|---|
| Archive extraction | Path validation, allow-listed extensions, size/ratio/entry caps, symlink + encryption rejection | `app/services/safe_zip.py` |
| XML parsing | `defusedxml`, DTD and entities forbidden before GDAL parses the file | `app/services/kml.py` |
| Upload intake | Header-level size cap, `Content-Length` required, streamed byte cap, filename sanitising | `app/core/security.py`, `app/services/uploads.py` |
| Processing limits | Feature and vertex caps, PROJ network access disabled, only `EPSG:<n>` accepted from users | `app/services/parser.py`, `app/services/crs.py`, `app/main.py` |
| Authentication | `X-API-Key`, constant-time comparison, multiple keys for rotation | `app/core/security.py` |
| Abuse protection | Per-IP sliding-window rate limits (general and upload) | `app/core/security.py` |
| Data access | ORM with bound parameters, UUID identifiers | `app/api/files.py` |
| Response hardening | Security headers, generic 500 errors, request ids, optional CORS and host allow-list | `app/main.py` |
| Runtime | Non-root container, read-only root FS, no capabilities, `no-new-privileges` | `Dockerfile`, `docker-compose.yml` |
| Supply chain | `pip-audit` and Bandit in CI, pinned lower bounds | `.github/workflows/ci.yml` |

## Known limitations (out of scope for this version)

- **Authentication is a shared API key**, not per-user identity. Any holder of a key can read any file.
- **The rate limiter is in-memory and per process.** Behind a reverse proxy, run Uvicorn with
  `--proxy-headers --forwarded-allow-ips=<proxy ip>` so the real client IP is used, and prefer a gateway
  or Redis-based limiter for multi-instance deployments.
- **TLS is not terminated by the app.** Run it behind a reverse proxy (Nginx, Caddy, a cloud load balancer)
  that provides HTTPS and HSTS.
- **No antivirus scanning** of uploads; the service only ever *parses* geospatial formats and never executes
  or serves uploaded content.
- **GDAL is a large native library.** Keep dependencies updated (CI runs `pip-audit`) and keep the container
  unprivileged, which this project does by default.
- Features stored in the database are not encrypted at rest.

## Secure deployment checklist

1. Set `API_KEYS` to long random values (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. Set `ENABLE_DOCS=false` and `ALLOWED_HOSTS=<your domain>`.
3. Put the service behind HTTPS.
4. Use PostgreSQL instead of SQLite for multi-user deployments.
5. Keep `.env` out of version control (it is in `.gitignore`).
