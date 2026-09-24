"""Entry point for gunicorn (app:app) and local development (python app.py).

The application lives in the virtuwill package; see virtuwill/__init__.py.
"""
import os

import config
from virtuwill import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=config.FLASK_DEBUG, host="0.0.0.0", port=int(os.getenv("DATABRICKS_APP_PORT", 5000)))
