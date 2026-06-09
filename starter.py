"""Thin entrypoint preserved for `gunicorn starter:app`, `flask --app starter ...`,
`python starter.py`, and `from starter import app, db`. Real wiring lives in app/."""
import os

from app import create_app
from app.extensions import db  # re-exported for tests/CLI compatibility

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
