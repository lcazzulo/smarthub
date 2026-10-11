"""Production entry point: exactly one Gunicorn worker, no preload/reloader."""
from . import create_app

app = create_app(start_mqtt=True)
