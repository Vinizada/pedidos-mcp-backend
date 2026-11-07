"""Exporta schemas das ferramentas reais; não conecta ao banco nem ao modelo."""

import asyncio
import json
from pathlib import Path

from pedidos.config import Settings
from pedidos.mcp_server import create_mcp


async def main():
    server = create_mcp(Settings(api_token="schema-export-only-" + "x" * 32))
    tools = await server.list_tools()
    target = Path("contracts/mcp-tools.json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "versao_contrato": 1,
                "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in tools],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(f"{len(tools)} ferramentas exportadas em {target}.")


if __name__ == "__main__":
    asyncio.run(main())
