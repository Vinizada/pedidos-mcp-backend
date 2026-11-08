"""Consultas REST do worker: documentos pendentes (paginados) e custo do dia."""

from decimal import Decimal
from uuid import UUID

import httpx

PAGINA = 100


class ClienteAPI:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self._headers = {"Authorization": f"Bearer {token}"}
        self.timeout = timeout

    def _cliente(self) -> httpx.AsyncClient:
        # O token vai apenas para a base configurada.
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=self.timeout,
            transport=self.transport,
        )

    async def pendentes(self) -> list[UUID]:
        """Documentos sem análise nem pedido, do mais antigo para o mais recente."""
        encontrados: list[UUID] = []
        async with self._cliente() as cliente:
            offset = 0
            while True:
                resposta = await cliente.get(
                    "/api/documentos", params={"limit": PAGINA, "offset": offset}
                )
                resposta.raise_for_status()
                pagina = resposta.json()
                encontrados += [
                    UUID(d["documento_id"])
                    for d in pagina["items"]
                    if d["status"] == "aguardando_agente"
                ]
                offset += PAGINA
                if offset >= pagina["total"] or not pagina["items"]:
                    break
        return list(reversed(encontrados))

    async def custo_do_dia(self) -> Decimal:
        async with self._cliente() as cliente:
            resposta = await cliente.get("/api/analises/custo")
            resposta.raise_for_status()
            return Decimal(str(resposta.json()["custo_estimado"]))
