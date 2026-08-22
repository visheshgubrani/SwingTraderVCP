"""CLI entrypoint used by Compose/CI. Lives in the server image at /app/scripts."""

import sys
from pathlib import Path

# `python scripts/<name>.py` puts only scripts/ on sys.path; add the app root
# so `from app...` imports resolve (same bootstrap as the sibling scripts).
SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.services.pending_migrations import main  # noqa: E402


if __name__ == "__main__":
    main()
