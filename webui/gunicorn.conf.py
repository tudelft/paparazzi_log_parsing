# Gunicorn settings, taken from the LOGDB_* settings (see config.py). Used automatically when gunicorn starts here.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import env, env_int  # noqa: E402

bind = env("BIND")
workers = 1  # the parse queue lives in the process; more processes would parse logs twice
threads = env_int("THREADS")
timeout = 0  # large uploads can take long
forwarded_allow_ips = "*" if env_int("PROXIES") else "127.0.0.1"
wsgi_app = "app:app"
