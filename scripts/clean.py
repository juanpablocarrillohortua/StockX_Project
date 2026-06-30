"""
Cross-platform clean-up script.

Replaces shell-specific `find ... -exec rm` calls (which only work on
Unix-like shells) with pure Python, so `make clean` behaves the same on
Windows, macOS, and Linux regardless of which shell is running `make`.
"""

import shutil
from pathlib import Path

DIRS_TO_REMOVE = ["__pycache__", ".ipynb_checkpoints", ".pytest_cache"]
FILE_PATTERNS_TO_REMOVE = ["*.pyc", "*.pyo", "*~"]


def main():
    root = Path(".")
    removed = 0

    for dirname in DIRS_TO_REMOVE:
        for path in root.rglob(dirname):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                print(f"removed dir:  {path}")
                removed += 1

    for pattern in FILE_PATTERNS_TO_REMOVE:
        for path in root.rglob(pattern):
            if path.is_file():
                path.unlink(missing_ok=True)
                print(f"removed file: {path}")
                removed += 1

    print(f"\nclean: {removed} item(s) removed")


if __name__ == "__main__":
    main()
