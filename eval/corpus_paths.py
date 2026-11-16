"""Where this corpus's evaluation files go by default."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def eval_path(*parts):
    """An artefact path under this corpus's EVAL_DIR."""
    return os.path.join(config.EVAL_DIR, *parts)


