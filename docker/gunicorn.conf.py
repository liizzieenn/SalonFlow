"""Gunicorn settings (tunable from the environment)."""

import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))  # assistant calls an LLM
graceful_timeout = 30
max_requests = 1000  # recycle workers to bound memory growth
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = "*"  # only Caddy can reach the container
