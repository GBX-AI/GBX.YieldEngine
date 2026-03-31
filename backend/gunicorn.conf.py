import os

bind = "0.0.0.0:" + str(os.getenv("PORT", "8000"))
# Single worker with threads — required for SQLite on Azure File Share (SMB)
# Multiple workers would each try to lock the same DB file
workers = 1
threads = 4
timeout = 300  # 5 min — scan with Kite + multiple strategies can take time
accesslog = "-"
errorlog = "-"
loglevel = "info"
