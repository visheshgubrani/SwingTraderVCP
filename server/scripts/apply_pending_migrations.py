"""CLI entrypoint used by Compose/CI. Lives in the server image at /app/scripts."""

from app.services.pending_migrations import main


if __name__ == "__main__":
    main()
