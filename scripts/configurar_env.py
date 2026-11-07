"""Cria configuração local sem sobrescrever segredos já existentes."""

import os
import secrets
from pathlib import Path


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
        print(".env já existe; configuração preservada.")
        return
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    print(".env criado com segredos aleatórios; não compartilhe nem versione este arquivo.")


if __name__ == "__main__":
    main()
