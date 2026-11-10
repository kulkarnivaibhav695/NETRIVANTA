# NETRIVANTA deployment checklist

This package is configured for Render + Neon PostgreSQL. Local development and Docker Compose can use SQLite.

## 1. Create the database

1. Create a new PostgreSQL project in Neon.
2. Copy its connection string (use the SSL-enabled connection string).
3. Keep the connection string private. Do not put it in GitHub or this ZIP.

## 2. Create the GitHub repository

1. Create a new empty repository named `NETRIVANTA`.
2. Extract this ZIP and upload/push the contents of the `NETRIVANTA` folder to the repository root.
3. Confirm `render.yaml`, `requirements.txt`, `app/`, `agent/` and `tests/` are at the repository root.

## 3. Deploy on Render

1. In Render, choose **New → Blueprint** and connect the new `NETRIVANTA` repository, or create a Python Web Service manually.
2. The included `render.yaml` defines the service name, Python runtime, build command, start command and `/api/health` health check.
3. Set the prompted environment variables:
   - `NETRIVANTA_ADMIN_EMAIL`
   - `NETRIVANTA_ADMIN_PASSWORD`
   - `NETRIVANTA_DATABASE_URL` (Neon connection string)
4. Keep `APP_ENV=production`, `COOKIE_SECURE=true`, and the generated `NETRIVANTA_SECRET_KEY`.
5. Deploy. Check `https://YOUR-SERVICE.onrender.com/api/health`; it should return status `ok`, service `NETRIVANTA`, version `3.0.0` and database `connected`.

## 4. First-use verification

1. Open `/admin/login` and sign in using the bootstrap administrator email/password.
2. Open `/signup` in a private/incognito window and create a volunteer account.
3. Sign in as the volunteer, open **Measurement Agent**, and register the endpoint once.
4. Press **Start Measurement** only during 07:00–10:00, 13:00–16:00 or 19:00–22:00 IST.
5. Confirm the session appears in personal history and the daily-average table.
6. Repeat on a later day to check the two-/three-day analysis.
7. In the administrator account, verify the participant's access-medium evidence before including it in verified comparisons.

## Operational notes

- Browser measurements use authenticated requests to the NETRIVANTA controlled HTTP probe. They report controlled HTTP response time, jitter and download/upload throughput. They do not claim to measure ICMP packet loss or the volunteer's local DNS resolver delay.
- A successful endpoint can submit at most one completed measurement in each scheduled session per IST calendar day. Failed probe attempts do not consume the session.
- SQLite is for local development or a persistent Docker volume. For Render + Neon, set `NETRIVANTA_DATABASE_URL`; do not set that variable to a local file path.
- Render's free web service may sleep when idle. It is suitable for a pilot demonstration, not a claim of 100,000-concurrent-volunteer capacity.
- Run `python -m pytest -q` before deployment. The included automated tests cover SQLite workflows and PostgreSQL SQL-adapter translation. A live Neon connection test must still be performed with your own database credentials before inviting volunteers.
