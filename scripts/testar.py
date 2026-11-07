"""Roda testes no PostgreSQL descartável do perfil test do Compose."""

import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.setdefault(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://pedidos:pedidos_test_local@127.0.0.1:55433/pedidos_test",
    )
    env["PYTHONPATH"] = str(root / "src")
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=root, env=env))
