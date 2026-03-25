import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
EXTRACT_DIR = os.path.join(DATA_DIR, "extracted")
OUTPUT_DIR = os.path.join(DATA_DIR, "outputs")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

def ensure_dirs():
    for d in [DATA_DIR, UPLOAD_DIR, EXTRACT_DIR, OUTPUT_DIR, CACHE_DIR, LOGS_DIR]:
        os.makedirs(d, exist_ok=True)
