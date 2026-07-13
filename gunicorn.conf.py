import os


def _positive_int(name, default):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


bind = "0.0.0.0:5000"
workers = _positive_int("FITX_WEB_WORKERS", 1)
threads = _positive_int("FITX_WEB_THREADS", 8)
timeout = 300
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
