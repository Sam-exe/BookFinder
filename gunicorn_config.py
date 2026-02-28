"""
Gunicorn configuration for production deployment
"""

import os

# Server socket — bind to 0.0.0.0 using Railway's PORT env var
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# Worker processes — keep low to fit Railway's memory limit
workers = 2
worker_class = "sync"
worker_connections = 1000
timeout = 300
keepalive = 5

# Logging — stdout/stderr so Railway/Render capture them automatically
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "book_profitability_checker"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (if needed)
# keyfile = None
# certfile = None
