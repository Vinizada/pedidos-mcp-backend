import subprocess

from pedidos.config import get_settings
from pedidos.index_catalog import main as index_catalog
from pedidos.seed import main as seed


def main():
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    seed()
    if get_settings().search_mode == "hybrid":
        index_catalog()


if __name__ == "__main__":
    main()
