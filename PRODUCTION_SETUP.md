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

Optional if you want in-app collection from hosted site:

- `X_BEARER_TOKEN=<your-x-api-bearer-token>`

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
