import argparse
import subprocess
import sys
from pathlib import Path

from config import settings

def run_script(command: list[str]) -> None:
    """Ejecuta un comando en un subproceso y transmite la salida en tiempo real."""
    print(f"\n🚀 Ejecutando: {' '.join(command)}")
    try:
        # Se usa sys.executable para garantizar que use el mismo entorno virtual (venv)
        result = subprocess.run([sys.executable] + command, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error crítico en el subproceso: {' '.join(command)}")
        sys.exit(e.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline Unificado de Scraping para StockX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 1. Argumentos para la extracción de catálogo
    parser.add_argument(
        "--url",
        type=str,
        default="https://stockx.com/brands/jordan",
        help="URL objetivo de la categoría de StockX (Fase 1)",
    )
    parser.add_argument(
        "--archivo",
        type=str,
        default=str(Path('docs')/Path(settings.URL_LIST_NAME)),
        help="Nombre del archivo TXT con las URLs (Fase 1 y 2)",
    )

    # 2. Argumentos compartidos o específicos de las fases de descarga/scraping
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Omite HTMLs ya descargados y slugs ya procesados en JSON (Fases 2 y 3)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Procesar solo las primeras N URLs/slugs (Fases 2 y 3 — Útil para pruebas)",
    )

    args = parser.parse_args()

    print("==================================================")
    print("      INICIANDO PIPELINE COMPLETO DE STOCKX       ")
    print("==================================================")

    # -------------------------------------------------------------------------
    # FASE 1: Extracción de URLs desde catálogo
    # -------------------------------------------------------------------------
    cmd_fase1 = ["utils/search_urls.py", "--url", args.url, "--archivo", args.archivo]
    run_script(cmd_fase1)

    # -------------------------------------------------------------------------
    # FASE 2: Descarga de páginas HTML
    # -------------------------------------------------------------------------
    cmd_fase2 = ["utils/copy_html.py"]
    if args.skip_existing:
        cmd_fase2.append("--skip-existing")
    if args.limit is not None:
        cmd_fase2.extend(["--limit", str(args.limit)])

    run_script(cmd_fase2)

    # -------------------------------------------------------------------------
    # FASE 3: Cruce de HTML local + GraphQL API
    # -------------------------------------------------------------------------
    # NOTA: Ajusta 'scraping_final.py' por el nombre real de tu tercer archivo
    cmd_fase3 = ["utils/scraper.py"]
    if args.skip_existing:
        cmd_fase3.append("--skip-existing")
    if args.limit is not None:
        cmd_fase3.extend(["--limit", str(args.limit)])

    run_script(cmd_fase3)

    print("\n✅ ¡Pipeline completado con éxito!")


if __name__ == "__main__":
    main()