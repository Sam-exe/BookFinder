"""
Gunicorn configuration for production deployment
"""

import multiprocessing

# Server socket
bind = "127.0.0.1:8000"

# Worker processes
# NOTE: SSE streaming keeps a connection open for the full analysis (~min).
# Use a modest worker count so long-running streams don't starve new requests.
workers = max(2, multiprocessing.cpu_count())
worker_class = "sync"
worker_connections = 1000
timeout = 300  # 5 minutes – long enough for Gemini + ISBN + Boekenbalie
keepalive = 5

# Logging
accesslog = "logs/access.log"
errorlog = "logs/error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "book_profitability_checker"

# Server mechanics
daemon = False
pidfile = "logs/gunicorn.pid"
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (if needed)
# keyfile = None
# certfile = None
