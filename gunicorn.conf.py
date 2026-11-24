import os

# Flask-SocketIO keeps each client's session in the memory of one process.
# With several worker processes (and no message queue + sticky sessions) live
# updates are lost and long-polling clients get "400 Invalid session". One
# worker with many threads avoids that; scale up with threads, not workers.
bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")
workers = 1
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "50"))
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True
