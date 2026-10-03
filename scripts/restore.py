"""Restaura reservas e visitantes ao estado dos arquivos originais de `dados/`.

Uso:
    uv run python -m scripts.restore              # preserva as sessões
    uv run python -m scripts.restore --sessions   # apaga também sessões/eventos

Rode com a API parada (ou reinicie-a depois) ao usar --sessions.
"""

from __future__ import annotations

import argparse
import sys

from aurora import config, db


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sessions",
        action="store_true",
        help="apaga também as sessões e eventos do ADK",
    )
    args = parser.parse_args()
    counts = db.restore_seed_data(clear_sessions=args.sessions)
    print(f"Banco: {config.domain_db_path()}")
    print("Dados restaurados a partir de dados/: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print("Sessões: " + ("apagadas" if args.sessions else "preservadas"))


if __name__ == "__main__":
    main()
