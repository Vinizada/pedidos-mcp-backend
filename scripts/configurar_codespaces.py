"""Autoriza somente o hostname atribuído ao Codespace; preserva segredos existentes."""

import json
import os
import re
from pathlib import Path


def configure(root: Path, environ: dict[str, str]) -> str | None:
    name = environ.get("CODESPACE_NAME")
    domain = environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if not name or not domain:
        print("Ambiente local: configuração de acesso preservada.")
        return None
    if not re.fullmatch(r"[a-zA-Z0-9-]+", name) or not re.fullmatch(r"[a-zA-Z0-9.-]+", domain):
        raise ValueError("Hostname do Codespaces inválido.")
    path = root / ".env"
    lines = path.read_text().splitlines()
    current = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    port = current.get("APP_PORT", "8000")
    if port != "8000":
        raise ValueError("Este devcontainer encaminha a porta 8000; ajuste APP_PORT para 8000.")
    host = f"{name}-8000.{domain}"
    updates = {}
    for key, value, default in [
        ("ALLOWED_HOSTS", host, ["localhost", "127.0.0.1", "backend"]),
        ("ALLOWED_ORIGINS", f"https://{host}", ["http://localhost:8000"]),
    ]:
        values = json.loads(current[key]) if key in current else default
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"{key} deve ser array JSON de strings.")
        updates[key] = json.dumps(list(dict.fromkeys([*values, value])), separators=(",", ":"))
    new_lines = []
    for line in lines:
        key = line.split("=", 1)[0]
        new_lines.append(f"{key}={updates.pop(key)}" if key in updates else line)
    new_lines.extend(f"{key}={value}" for key, value in updates.items())
    path.write_text("\n".join(new_lines) + "\n")
    path.chmod(0o600)
    print(f"Endpoint preparado: https://{host}/mcp")
    print("Porta privada por padrão. Para o grupo, siga docs/codespaces.md.")
    return host


if __name__ == "__main__":
    configure(Path(__file__).resolve().parents[1], dict(os.environ))
