# Each WebSocket occupies a thread. One worker owns the MQTT cache/subscriptions.
bind = '127.0.0.1:5055'
workers = 1
worker_class = 'gthread'
threads = 24
preload_app = False
accesslog = '-'
errorlog = '-'


def worker_exit(server, worker):
    app = getattr(worker, 'wsgi', None)
    if app is not None:
        app.extensions['heating_mqtt'].stop()
