# PulseBoard Production Setup

## 1) Required Render environment variables

Set these in Render service `Environment`:

- `DJANGO_ENV=production`
- `DJANGO_SECRET_KEY=<long-random-value>`
- `DJANGO_DEBUG=false`
- `DJANGO_ALLOWED_HOSTS=.onrender.com,127.0.0.1,localhost`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://*.onrender.com`
- `DJANGO_SECURE_SSL_REDIRECT=true`
- `DJANGO_SESSION_COOKIE_SECURE=true`
- `DJANGO_CSRF_COOKIE_SECURE=true`
- `DJANGO_SECURE_HSTS_SECONDS=31536000`
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=true`
- `DJANGO_SECURE_HSTS_PRELOAD=true`
- `TWEET_DATA_DIR=/var/data/tweets`
- `SQLITE_DB_PATH=/var/data/pulseboard/db.sqlite3`
- `TWEET_EXAMPLE_DATA_DIR=/opt/render/project/src/data` (optional, default already points here)

Optional if you want in-app collection from hosted site:

- `X_BEARER_TOKEN=<your-x-api-bearer-token>`

New user behavior:

- Every authenticated user gets a separate workspace under `TWEET_USER_SPACES_DIR` (default: `<TWEET_DATA_DIR>/__userspaces__`).
- On first login, workspace is auto-seeded from `TWEET_EXAMPLE_DATA_DIR` so old data appears as examples.

## 2) Create Django admin user on Render

Open a shell in Render for this service and run:

```bash
python manage.py createsuperuser
```

Then log in at:

- `https://<your-render-url>/admin/`

## 3) Create admin user locally (optional)

From project root:

```powershell
cd d:\files\tweet_dashboard
..\.venv\Scripts\python.exe manage.py createsuperuser
```

## 4) Make data persistent on Render

Your current screenshot shows `Free` plan. Render persistent disks are not available on free web services.  
To persist tweets and SQLite DB across restarts/redeploys:

1. Upgrade service plan to one that supports disks.
2. In Render service, go to `Disk` -> `Add Disk`.
3. Use:
   - Mount path: `/var/data`
   - Size: 1 GB (or higher)
4. Confirm env vars are set:
   - `TWEET_DATA_DIR=/var/data/tweets`
   - `SQLITE_DB_PATH=/var/data/pulseboard/db.sqlite3`
5. Deploy latest commit.

After deploy, dashboard `Data root` should show `/var/data/tweets`.
