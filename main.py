import argparse
import subprocess
import sys
from pathlib import Path

from config import settings


def run_script(command: list[str]) -> None:
    """Spawn a subprocess execute a command and stream stdout in real-time."""
    print(f"\n🚀 Running: {' '.join(command)}")
    try:
        # Use sys.executable to ensure the subprocess runs within the current
        # virtual environment (venv).
        result = subprocess.run([sys.executable] + command, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Critical error in the subprocess: {' '.join(command)}")
        sys.exit(e.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified Scraping Pipeline for StockX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 1. Argumentos para la extracción de catálogo
    parser.add_argument(
        "--url",
        type=str,
        default="https://stockx.com/brands/jordan",
        help="Target URL for the StockX category (Phase 1)",
    )
    parser.add_argument(
        "--archivo",
        type=str,
        default=str(Path('docs')/Path(settings.URL_LIST_NAME)),
        help="TXT file name with URLs (Phase 1 and 2)",
    )

    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip already downloaded HTMLs and slugs already processed",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process only the first N URLs/slugs",
    )

    args = parser.parse_args()

    print("==================================================")
    print("      STARTING FULL STOCKX PIPELINE       ")
    print("==================================================")

    # -------------------------------------------------------------------------
    # PHASE 1: Extract URLs from the catalog
    # -------------------------------------------------------------------------
    cmd_fase1 = [
        "utils/search_urls.py",
        "--url",
        args.url,
        "--archivo",
        args.archivo
        ]
    run_script(cmd_fase1)

    # -------------------------------------------------------------------------
    # PHASE 2: Download HTML pages
    # -------------------------------------------------------------------------
    cmd_fase2 = ["utils/copy_html.py"]
    if args.skip_existing:
        cmd_fase2.append("--skip-existing")
    if args.limit is not None:
        cmd_fase2.extend(["--limit", str(args.limit)])

    run_script(cmd_fase2)

    # -------------------------------------------------------------------------
    # PHASE 3: Merge local HTML data with GraphQL API payloads
    # -------------------------------------------------------------------------

    cmd_fase3 = ["utils/scraper.py"]
    if args.skip_existing:
        cmd_fase3.append("--skip-existing")
    if args.limit is not None:
        cmd_fase3.extend(["--limit", str(args.limit)])

    run_script(cmd_fase3)

    print("\n✅ Pipeline executed successfully!")


if __name__ == "__main__":
    main()
