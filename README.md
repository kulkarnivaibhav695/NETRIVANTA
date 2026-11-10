# NETRIVANTA — PS-014 Internet Quality Observatory

NETRIVANTA is an authenticated pilot platform for measuring and comparing Internet quality across Fiber, Cellular, Fixed Wireless and Satellite access media. This release preserves the original Python measurement agent and controlled probe endpoints while adding separate user and administrator workspaces, role-based authorization and multi-page navigation.

## Included functionality

### Authentication and privacy
- Public login and signup; all research pages require a valid signed, HttpOnly session cookie.
- Passwords are stored using PBKDF2-HMAC-SHA256 (310,000 rounds with per-user random salts).
- New registrations are always regular users; users cannot self-assign administrator privileges.
- Admin role is bootstrapped only from deployment environment variables when the database has no administrator.
- Backend authorization protects every dashboard, volunteer, measurement and admin data endpoint.
- Regular users receive anonymized measurement rows and aggregated volunteer counts; names, emails, device labels and individual endpoint details are admin-only.

### User / volunteer workspace
- `/dashboard` — pilot overview and statistics.
- `/comparison` — comparison based only on completed measurements attached to verified access-medium records.
- `/measurements` — anonymized measurement history.
- `/volunteers` — anonymized participation distribution.
- `/agent` — authenticated endpoint registration, scheduled browser measurements, session history, daily averages and multi-day analysis.
- `/my-measurements` — personal endpoint measurement history.
- `/campaigns` — active campaigns and join action.
- `/account` — profile editing.
- `/notifications` — user notifications.
- `/help` — metric explanations and pilot documentation.

### Administrator workspace
- `/admin/login` and `/admin/dashboard` — administrator sign-in and control centre.
- `/admin/volunteers` — participant records and endpoint status.
- `/admin/measurements` — complete measurement records and review status.
- `/admin/verification` — human review of declared access media.
- `/admin/campaigns` — create and manage research campaigns.
- `/admin/analytics` — verified-data research analytics.
- `/admin/users` — account and role overview.
- `/admin/agents` — endpoint activity and last-seen status.
- `/admin/data-quality` — duplicates, missing latency, partial tests and pending review indicators.
- `/admin/reports` — authorized CSV export.
- `/admin/audit-logs` — recorded privileged actions.
- `/admin/settings` — service status and deployment security guidance.

The `/forgot-password` page explains that automated reset email delivery is not configured in this pilot. An administrator must handle account recovery securely.

## Requirements

- Python 3.10+ (recommended Python 3.12)
- Network access between volunteer agents and the hosted NETRIVANTA API

## Run locally (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:NETRIVANTA_SECRET_KEY = "replace-with-a-long-random-secret-at-least-32-characters"
$env:NETRIVANTA_ADMIN_NAME = "NETRIVANTA Administrator"
$env:NETRIVANTA_ADMIN_EMAIL = "admin@example.com"
$env:NETRIVANTA_ADMIN_PASSWORD = "replace-with-a-strong-password"
$env:COOKIE_SECURE = "false"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000/login`. Sign in to the administrator account configured above; the first admin account is created automatically on the first startup if no admin exists. Sign up creates regular user accounts only. Keep the secret and admin password private.

For macOS/Linux, create and activate a virtual environment with `python3 -m venv .venv` and `source .venv/bin/activate`, then use the same environment variables and Uvicorn command.

## Deploy publicly (Render + Neon PostgreSQL)

1. Create a PostgreSQL database in Neon and copy its pooled or direct connection string. Keep it private.
2. Create a Render Web Service from this repository. Use Python 3, the repository root, build command `pip install -r requirements.txt`, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, and health check path `/api/health`.
3. Set `APP_ENV=production`, `NETRIVANTA_SECRET_KEY` (a persistent random value of at least 32 characters), `NETRIVANTA_ADMIN_NAME`, `NETRIVANTA_ADMIN_EMAIL`, `NETRIVANTA_ADMIN_PASSWORD`, `COOKIE_SECURE=true`, and `NETRIVANTA_DATABASE_URL` (the Neon PostgreSQL connection string) in Render environment settings.
4. Keep the database URL and admin credentials private. The application uses PostgreSQL when `NETRIVANTA_DATABASE_URL` is set; SQLite is supported for local development and Docker with a persistent volume.
5. Deploy and open `/admin/login`. The first administrator is bootstrapped only if the database does not already contain an administrator. Use a unique strong password and change it after first access.
6. Test signup, endpoint registration, browser measurement, user history and admin analytics on the public URL before inviting participants.

For local SQLite development, set `NETRIVANTA_DATABASE=./netrivanta.db`. For Docker Compose, copy `.env.example` to `.env`, replace placeholders, then run `docker compose up --build -d`. The Compose service persists SQLite data in a named volume. Do not commit `.env`, agent config files, database files or real API keys.

## Register an endpoint and collect browser measurements

1. Sign up and log in as a volunteer.
2. Open **Measurement Agent** and register the device's city, ISP, access medium and Wi-Fi/Ethernet/mobile-data context. Registration is a one-time action for the account.
3. During an open collection window, press **Start Measurement**. The three windows are 07:00–10:00, 13:00–16:00 and 19:00–22:00 India Standard Time.
4. The browser measures controlled HTTP response time, jitter between samples, and download/upload throughput. The signed-in account session is used; volunteers do not copy an API key or install Python for this workflow.
5. The server verifies that the endpoint belongs to the logged-in account, enforces the IST window, and prevents a second successful result for the same endpoint/session/day. Failed attempts do not create a completed session record.
6. Review the raw session records, daily averages and three-day analysis. Each metric is averaged independently; missing sessions are not treated as zero.

The browser HTTP workflow does not claim to measure ICMP packet loss or the participant's local DNS resolver delay; those fields remain unavailable rather than being fabricated. The optional Python agent remains available for participants who want its additional network diagnostics and continues to authenticate using `X-API-Key`.

## API access rules

| Endpoint group | Access |
|---|---|
| `/api/health`, `/api/probe/*` | Public probe/health endpoints |
| `/api/auth/signup`, `/api/auth/login`, `/api/auth/logout` | Public authentication actions |
| `/api/measurements` POST | Endpoint API key required (optional Python agent) |
| `/api/browser/measurements` POST | Signed-in user session and owned endpoint |
| `/api/auth/me`, `/api/dashboard/*`, `/api/measurements` GET, `/api/campaigns`, `/api/profile` | Authenticated user |
| `/api/volunteers` POST | Authenticated user; creates endpoint tied to own account |
| `/api/volunteers` GET, `/api/admin/*`, medium review | Administrator only |

## Tests

```bash
pytest -q
```

The test suite covers public/private API boundaries, signup/login, user-vs-admin access, endpoint registration, API-key submissions, raw browser upload handling, scheduled browser measurements, endpoint ownership, duplicate-session protection, daily averages, probe limits and frontend deep-link routing. PostgreSQL support is implemented through psycopg2 and must be verified against the Neon database URL used for deployment.

## PS-014 interpretation notes and limitations

- Access medium is participant-declared until an administrator reviews supporting evidence. Comparisons exclude records whose access-medium status is not `verified`.
- Wi-Fi context is stored separately to avoid conflating local Wi-Fi conditions with the access technology.
- Measurement results are observational and context-dependent; comparisons should include sample counts, ISP, city/site and Wi-Fi context. Do not present pilot averages as causal or nationwide rankings.
- The existing throughput probes are deliberately capped at 1 MiB per direction per run. Use only with informed participant consent and reasonable scheduling intervals.
- The agent's HTTP round-trip time is not ICMP ping. Packet loss represents failed controlled HTTP probes (five attempts). DNS delay is the local resolver's lookup time for the configured target. Loaded jitter is derived from controlled endpoint response times while a capped download runs.
- This is a pilot application, not a production-scale public measurement network. Before wider use, add a managed email/password-reset provider, login rate limiting, stronger operational monitoring, backup/restore drills, formal retention/deletion workflows, and independent security review.
