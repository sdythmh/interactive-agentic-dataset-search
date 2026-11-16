"""Print the environment variable names config.py reads, one per line."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from ui import selfcheck  # noqa: E402

if __name__ == "__main__":
    root = os.path.dirname(HERE)
    for name in sorted(selfcheck.env_names_read_by_config(
            os.path.join(root, "config.py"))):
        print(name)
