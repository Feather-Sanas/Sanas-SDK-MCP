"""Console entry point: ``sanas-mcp`` / ``python -m sanas_mcp``."""

from __future__ import annotations

from dotenv import load_dotenv

from .server import run


def main() -> None:
    # Load a local .env (if present) so credentials can be set without exporting.
    load_dotenv()
    run()


if __name__ == "__main__":
    main()
