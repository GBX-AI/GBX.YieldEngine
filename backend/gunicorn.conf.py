import os

bind = "0.0.0.0:" + str(os.getenv("PORT", "8000"))
workers = 2
timeout = 120
accesslog = "-"
errorlog = "-"
loglevel = "info"
