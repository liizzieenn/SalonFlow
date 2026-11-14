# Deploying SalonFlow to a VPS

One small server runs the whole production stack with Docker Compose:

```
Internet ─► Caddy :80/:443 (automatic HTTPS) ─► web (gunicorn, Django)
                                                  ├─ db     PostgreSQL 16 + pgvector
                                                  ├─ redis  broker, cache, rate limits
                                                  ├─ celery worker (confirmations, reminders, re-embedding)
                                                  └─ beat   (queues due reminders every 5 min)
```

All of it is defined in [`docker-compose.prod.yml`](../docker-compose.prod.yml). CI starts this
exact stack and smoke-tests it over HTTPS on every pull request (`prod-stack` job).

## 1. Server

Any VPS with **2 GB RAM** (1 vCPU is fine) and Ubuntu 24.04 works, e.g. Hetzner CX22 or a
DigitalOcean basic droplet.

```bash
# as root on a fresh server
apt-get update && apt-get -y upgrade
curl -fsSL https://get.docker.com | sh            # Docker Engine + compose plugin

adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy

# Firewall: SSH + HTTP(S) only (Caddy also serves HTTP/3 on 443/udp)
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp
ufw --force enable
```

Copy your SSH public key to `/home/deploy/.ssh/authorized_keys` and disable password logins
(`PasswordAuthentication no` in `/etc/ssh/sshd_config`, then `systemctl restart ssh`).

## 2. DNS

Point an `A` record (and `AAAA` if the server has IPv6) for your domain, e.g.
`salonflow.example.com`, at the server. Caddy requests the TLS certificate on first start, so
DNS must resolve **before** step 4.

## 3. Code and configuration

```bash
# as deploy
sudo mkdir -p /opt/salonflow && sudo chown deploy: /opt/salonflow
git clone https://github.com/liizzieenn/salonflow.git /opt/salonflow
cd /opt/salonflow
cp .env.prod.example .env
chmod 600 .env
```

Edit `.env`:

| Variable | Value |
|---|---|
| `DOMAIN` | your hostname, e.g. `salonflow.example.com` |
| `ACME_EMAIL` | your email (Let's Encrypt expiry notices); **required** |
| `DJANGO_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `POSTGRES_PASSWORD` | a long random password (`openssl rand -hex 24`) |
| `EMAIL_*` | SMTP credentials for confirmations and reminders |
| `SMS_BACKEND` / `TWILIO_*` | keep the console backend, or configure Twilio for real SMS |
| `ASSISTANT_*`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY` | keep the fake providers, or switch to Claude + Voyage |
| `DEMO_PASSWORD` | password of the `demo` / `demo-staff` logins |

`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` are derived from `DOMAIN`; nothing else needs
setting. Secrets live only in this file on the server, never in git.

## 4. First start

```bash
docker compose -f docker-compose.prod.yml up -d --build --wait
```

The `web` container waits for PostgreSQL and runs migrations on start. Caddy obtains the
certificate within a few seconds; check with:

```bash
docker compose -f docker-compose.prod.yml ps        # everything "healthy"
curl -fsS https://salonflow.example.com/health/     # {"status": "ok", "db": true}
```

Create your admin account and, for the public demo, the demo salon:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
docker compose -f docker-compose.prod.yml exec web python manage.py seed_demo
```

If you switched the assistant to real providers, build its index once:
`docker compose -f docker-compose.prod.yml exec web python manage.py reindex_assistant`.

## 5. Automatic deploys from GitHub

[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) runs after CI succeeds on `main`
(or manually via *Run workflow*). It SSHes to the server and runs
[`deploy/deploy.sh`](../deploy/deploy.sh), which:

1. resets the checkout to `origin/main` (the server checkout is deploy-only),
2. rebuilds the image and restarts changed services, waiting until they're healthy,
3. runs `manage.py check --deploy` and prunes old images.

Set it up once:

```bash
# on your machine: a key used only for deploys
ssh-keygen -t ed25519 -f salonflow_deploy -N "" -C "github-actions-deploy"
```

On the server, append `salonflow_deploy.pub` to `/home/deploy/.ssh/authorized_keys`. Optionally
restrict it to the deploy script:

```
command="cd /opt/salonflow && ./deploy/deploy.sh",no-port-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA… github-actions-deploy
```

In the GitHub repository → *Settings → Secrets and variables → Actions*:

| Name | Kind | Value |
|---|---|---|
| `DEPLOY_HOST` | secret | server hostname or IP |
| `DEPLOY_USER` | secret | `deploy` |
| `DEPLOY_SSH_KEY` | secret | contents of `salonflow_deploy` (private key) |
| `DEPLOY_KNOWN_HOSTS` | secret | output of `ssh-keyscan -t ed25519 <server>` (pins the host key) |
| `DEPLOY_PATH` | secret, optional | defaults to `/opt/salonflow` |
| `DEPLOY_PORT` | secret, optional | defaults to `22` |
| `LIVE_DEMO_URL` | variable | `https://salonflow.example.com`, used for the post-deploy health check |

Until `DEPLOY_HOST` is set the workflow skips the deploy instead of failing, so forks stay green.

## 6. Operations

**Logs**

```bash
docker compose -f docker-compose.prod.yml logs -f web celery beat caddy
```

**Backups:** nightly `pg_dump`, keeping 14 days. As `deploy`, run `crontab -e`:

```cron
15 3 * * * cd /opt/salonflow && docker compose -f docker-compose.prod.yml exec -T db pg_dump -U salonflow -Fc salonflow > /opt/backups/salonflow-$(date +\%F).dump && find /opt/backups -name 'salonflow-*.dump' -mtime +14 -delete
```

Create `/opt/backups` first, and copy the dumps off the server (e.g. `rclone` to object storage).
Restore with
`docker compose -f docker-compose.prod.yml exec -T db pg_restore -U salonflow -d salonflow --clean < file.dump`.

**Fresh demo every night** (optional, for the public demo):

```cron
30 4 * * * cd /opt/salonflow && docker compose -f docker-compose.prod.yml exec -T web python manage.py seed_demo --reset >/dev/null
```

**Manual deploy or rollback**

```bash
cd /opt/salonflow && ./deploy/deploy.sh                 # latest main
git checkout <sha> && docker compose -f docker-compose.prod.yml up -d --build --wait   # pin a version
```

**Scaling knobs:** `GUNICORN_WORKERS` / `GUNICORN_THREADS` in `.env` (about 2 × vCPU workers),
and the worker's `--concurrency` in `docker-compose.prod.yml`.

## Troubleshooting

| Symptom | Check |
|---|---|
| `required variable … is missing a value` | `DOMAIN`, `ACME_EMAIL` and `POSTGRES_PASSWORD` must be set in `.env` |
| Browser shows a certificate error | DNS not pointing at the server yet, or ports 80/443 blocked; see `logs caddy` |
| `400 Bad Request` | the hostname you used isn't `DOMAIN` (`ALLOWED_HOSTS`) |
| No confirmation / reminder messages | `ps` shows `celery` healthy? `logs celery`; console backends only log messages |
| Assistant says it's unavailable | provider keys, or Redis down (rate limiting fails closed) |
