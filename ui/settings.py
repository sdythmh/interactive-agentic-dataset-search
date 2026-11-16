"""Settings of the UI layer itself."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# One entry per corpus: label shown in the UI, the env file the worker is
# started with (the same files eval/*/run_clear.sh and the README use), and the
# default worker port.
CORPORA = {
    "au": {"label": "Australia", "env_file": "eval/v4.env", "port": 8101},
    "uk": {"label": "United Kingdom", "env_file": "eval/uk/v4.env", "port": 8102},
    "us": {"label": "United States", "env_file": "eval/us/v4.env", "port": 8103},
}

HOST = "127.0.0.1"
GATEWAY_PORT = int(os.getenv("UI_PORT", "8000"))

# The comparison view: after a search, the agent's ten next to a keyword search
# and a single search round.
UI_COMPARE = os.getenv("UI_COMPARE", "0").lower() in ("1", "on", "true", "yes")

# Seconds a clarifying question waits for the user before it counts as the
# null reply ("No, nothing more specific.").
ASK_TIMEOUT = float(os.getenv("UI_ASK_TIMEOUT", "300"))

# Where run_ui.sh writes pid files, worker status files and logs. Not committed.
RUN_DIR = os.getenv("UI_RUN_DIR", os.path.join(ROOT, "ui", "run"))
LOG_DIR = os.getenv("UI_LOG_DIR", os.path.join(ROOT, "ui", "logs"))

STATIC_DIR = os.path.join(ROOT, "ui", "static")
EXAMPLES_FILE = os.path.join(ROOT, "ui", "examples.json")


def worker_port(corpus):
    return int(os.getenv("UI_WORKER_PORT_%s" % corpus.upper(),
                         str(CORPORA[corpus]["port"])))


def worker_url(corpus):
    return "http://%s:%d" % (HOST, worker_port(corpus))
