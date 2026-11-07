"""Cliente MCP sem LLM: consulta, valida, grava e confere um rascunho idempotente."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url: str):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token = os.environ["API_TOKEN"]
    # O token é enviado apenas ao endpoint explicitamente indicado.
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}, timeout=120
    ) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 6

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    if result.isError:
                        raise RuntimeError(f"{name}: {result.content}")
                    return result.structuredContent

                search = await call("buscar_produtos", {"entrada": {"consulta": "MER-001"}})
                product = search["candidatos"][0]
                clients = await call("buscar_cliente", {"entrada": {"consulta": "CLI-001"}})
                customer = clients["clientes"][0]
                cart = {
                    "cliente_id": customer["cliente_id"],
                    "itens": [
                        {"produto_id": product["produto_id"], "quantidade": 2, "unidade": "UN"}
                    ],
                }
                valid = await call("validar_carrinho", {"entrada": cart})
                assert valid["valido"]
                data = cart | {
                    "chave_idempotencia": "smoke-mcp-v1",
                    "referencia_documento": "smoke-sem-llm",
                }
                first = await call("criar_pedido_rascunho", {"entrada": data})
                repeated = await call("criar_pedido_rascunho", {"entrada": data})
                assert first["pedido_id"] == repeated["pedido_id"]
                assert repeated["repeticao_idempotente"]
                saved = await call("consultar_pedido", {"pedido_id": first["pedido_id"]})
                assert saved["total"] == valid["total"]
                availability = await call(
                    "consultar_disponibilidade",
                    {"entrada": {"produto_ids": [product["produto_id"]]}},
                )
                assert (
                    availability["produtos"][0]["estoque_disponivel"]
                    == product["estoque_disponivel"]
                )
                print(
                    json.dumps(
                        {
                            "status": "ok",
                            "ferramentas": len(tools.tools),
                            "pedido_id": saved["pedido_id"],
                            "total": saved["total"],
                            "idempotencia": "ok",
                            "estoque_preservado": True,
                        },
                        indent=2,
                    )
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    asyncio.run(main(parser.parse_args().url))
