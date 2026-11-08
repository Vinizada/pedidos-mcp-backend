"""Processamento manual: `python -m extrator processar <documento_id>...` ou `--pendentes`.

Imprime um JSON por documento com decisão, IDs, custo e latência (sem conteúdo).
"""

import argparse
import asyncio
import json
import sys
from uuid import UUID

from extrator.api_rest import ClienteAPI
from extrator.config import get_agente_settings
from extrator.llm import ClienteAnthropic
from extrator.orquestrador import processar_documento
from extrator.worker import configurar_logs


async def processar(ids: list[UUID], pendentes: bool) -> int:
    settings = get_agente_settings()
    if pendentes:
        api = ClienteAPI(settings.api_base_url, settings.api_token.get_secret_value())
        ids = [*ids, *await api.pendentes()]
    llm = ClienteAnthropic(settings)
    falhas = 0
    for documento_id in ids:
        try:
            resultado = await processar_documento(settings, llm, documento_id)
            print(json.dumps(resultado.como_dict(), ensure_ascii=False))
        except Exception as exc:
            falhas += 1
            erro = {
                "documento_id": str(documento_id),
                "erro": type(exc).__name__,
                "mensagem": str(exc)[:300],
            }
            print(json.dumps(erro, ensure_ascii=False), file=sys.stderr)
    return 1 if falhas else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m extrator")
    comandos = parser.add_subparsers(dest="comando", required=True)
    comando = comandos.add_parser("processar", help="Processa documentos enviados.")
    comando.add_argument("documento_ids", nargs="*", type=UUID)
    comando.add_argument("--pendentes", action="store_true", help="Inclui todos os pendentes.")
    args = parser.parse_args(argv)
    if not args.documento_ids and not args.pendentes:
        parser.error("informe ao menos um documento_id ou --pendentes")
    configurar_logs()
    return asyncio.run(processar(args.documento_ids, args.pendentes))
