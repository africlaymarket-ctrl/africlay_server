"""
Settings package initialization for africlay_server.

Selects settings module based on DEV_ENVIRONMENT environment variable:
- 'local' (default): local.py (local Docker PostgreSQL / SQLite)
- 'dev' / 'development': dev.py (development Cloud SQL PostgreSQL with Public IP)
- 'prod' / 'production': prod.py (production database instance with security hardening)
"""

import os
from .base import *

env = os.getenv('DEV_ENVIRONMENT', 'local').strip().lower()

if env in ('prod', 'production'):
    from .prod import *
elif env in ('dev', 'development'):
    from .dev import *
else:
    from .local import *
