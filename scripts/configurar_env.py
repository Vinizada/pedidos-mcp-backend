"""Cria configuração local sem sobrescrever segredos já existentes."""

import os
import secrets
from pathlib import Path

# Variáveis adicionadas depois da primeira versão; acrescentadas a um .env existente.
NOVAS_VARIAVEIS = {
    "ANTHROPIC_API_KEY": "",
    "LLM_MODEL": "claude-sonnet-5-5",
    "LLM_EFFORT": "medium",
}


def completar_env(destination: Path) -> list[str]:
    """Acrescenta variáveis ausentes sem alterar nenhum valor já definido."""
    content = destination.read_text()
    present = {
        line.split("=", 1)[0].strip()
        for line in content.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    missing = [name for name in NOVAS_VARIAVEIS if name not in present]
    if missing:
        prefix = "" if content.endswith("\n") or not content else "\n"
        lines = "".join(f"{name}={NOVAS_VARIAVEIS[name]}\n" for name in missing)
        with destination.open("a") as stream:
            stream.write(f"{prefix}# Agente (perfil `agente`).\n{lines}")
    return missing


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / ".env"
    content = (root / ".env.example").read_text()
    content = content.replace("substitua-por-uma-senha-local", secrets.token_hex(24))
    content = content.replace(
        "substitua-por-um-token-aleatorio-com-32-caracteres-ou-mais", secrets.token_urlsafe(36)
    )
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        missing = completar_env(destination)
        if missing:
            print(f".env já existe; valores preservados e acrescentado: {', '.join(missing)}.")
        else:
            print(".env já existe; configuração preservada.")
        return
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    print(".env criado com segredos aleatórios; não compartilhe nem versione este arquivo.")


if __name__ == "__main__":
    main()
