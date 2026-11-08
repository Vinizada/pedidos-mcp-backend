"""Worker: orçamento diário, disjuntor, parada limpa e paginação REST (sem rede)."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from extrator.api_rest import ClienteAPI
from extrator.config import AgenteSettings
from extrator.orquestrador import ErroTransitorio, ResultadoProcessamento
from extrator.worker import Worker


class Relogio:
    def __init__(self):
        self.agora = 0.0

    def __call__(self):
        return self.agora


class FakeAPI:
    def __init__(self, pendentes, gasto="0", falhar=False):
        self._pendentes = pendentes
        self.gasto = Decimal(gasto)
        self.falhar = falhar

    async def pendentes(self):
        if self.falhar:
            raise httpx.ConnectError("backend fora")
        return list(self._pendentes)

    async def custo_do_dia(self):
        if self.falhar:
            raise httpx.ConnectError("backend fora")
        return self.gasto


def settings(monkeypatch, **overrides):
    monkeypatch.setenv("API_TOKEN", "t" * 32)
    return AgenteSettings(_env_file=None, agente_intervalo_s=10, **overrides)


def processador(custo="0.05", falhas=0):
    chamados = []

    async def processar(documento_id):
        chamados.append(documento_id)
        if len(chamados) <= falhas:
            raise ErroTransitorio("API indisponível")
        return ResultadoProcessamento(
            documento_id, "processado", "criar_rascunho", custo_usd=Decimal(custo)
        )

    return processar, chamados


def test_cycle_processes_all_pending(monkeypatch):
    docs = [uuid4() for _ in range(3)]
    processar, chamados = processador()
    worker = Worker(settings(monkeypatch), FakeAPI(docs), processar)
    assert asyncio.run(worker.ciclo()) == {"estado": "ok", "processados": 3}
    assert chamados == docs


def test_daily_budget_stops_processing(monkeypatch):
    docs = [uuid4() for _ in range(5)]
    processar, chamados = processador(custo="0.40")
    worker = Worker(
        settings(monkeypatch, agente_orcamento_diario_usd="1.00"),
        FakeAPI(docs, gasto="0.30"),
        processar,
    )
    estado = asyncio.run(worker.ciclo())
    # 0,30 + 0,40 + 0,40 = 1,10 ≥ 1,00: o terceiro não começa.
    assert estado == {"estado": "orcamento_diario", "processados": 2}
    assert chamados == docs[:2]
    esgotado = Worker(
        settings(monkeypatch, agente_orcamento_diario_usd="1.00"),
        FakeAPI(docs, gasto="1.00"),
        processar,
    )
    assert asyncio.run(esgotado.ciclo())["processados"] == 0


def test_circuit_breaker_opens_backs_off_and_resets(monkeypatch):
    relogio = Relogio()
    docs = [uuid4() for _ in range(10)]
    processar, chamados = processador(falhas=6)
    worker = Worker(
        settings(monkeypatch, agente_disjuntor_falhas=3), FakeAPI(docs), processar, relogio
    )

    assert asyncio.run(worker.ciclo())["estado"] == "disjuntor_aberto"
    assert len(chamados) == 3  # parou na terceira falha seguida
    assert worker.pausado_ate == 10  # intervalo × 2⁰
    assert asyncio.run(worker.ciclo())["estado"] == "disjuntor_aberto"
    assert len(chamados) == 3  # pausado: nada foi tentado

    relogio.agora = 11
    assert asyncio.run(worker.ciclo())["estado"] == "disjuntor_aberto"
    assert worker.pausado_ate == 11 + 20  # backoff exponencial: intervalo × 2¹

    relogio.agora = 32
    assert asyncio.run(worker.ciclo()) == {"estado": "ok", "processados": 10}
    assert worker.aberturas == 0 and worker.pausado_ate is None


def test_backend_unavailable_counts_for_breaker(monkeypatch):
    worker = Worker(
        settings(monkeypatch, agente_disjuntor_falhas=2), FakeAPI([], falhar=True), processador()[0]
    )
    assert asyncio.run(worker.ciclo())["estado"] == "backend_indisponivel"
    asyncio.run(worker.ciclo())
    assert worker.disjuntor_aberto


def test_stop_event_ends_loop_promptly(monkeypatch):
    docs = [uuid4() for _ in range(3)]
    parar = asyncio.Event()

    async def processar(documento_id):
        parar.set()  # SIGTERM chegou durante o primeiro documento
        return ResultadoProcessamento(documento_id, "processado", custo_usd=Decimal("0"))

    worker = Worker(settings(monkeypatch), FakeAPI(docs), processar)

    async def main():
        await asyncio.wait_for(worker.executar(parar), timeout=2)

    asyncio.run(main())


def test_rest_client_pages_and_keeps_only_pending():
    docs = [
        {"documento_id": str(uuid4()), "status": status}
        for status in ["aguardando_agente", "pedido_criado"] * 75 + ["aguardando_agente"]
    ]
    vistos = []

    def handler(request: httpx.Request):
        assert request.headers["authorization"] == "Bearer segredo"
        vistos.append(dict(request.url.params))
        if request.url.path == "/api/analises/custo":
            return httpx.Response(
                200, json={"data": "2026-11-08", "custo_estimado": "1.25", "analises": 3}
            )
        offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
        return httpx.Response(
            200, json={"items": docs[offset : offset + limit], "total": len(docs)}
        )

    api = ClienteAPI("http://backend:8000", "segredo", transport=httpx.MockTransport(handler))
    pendentes = asyncio.run(api.pendentes())
    esperado = [d["documento_id"] for d in docs if d["status"] == "aguardando_agente"]
    assert [str(p) for p in pendentes] == list(reversed(esperado))
    assert [v["offset"] for v in vistos] == ["0", "100"]
    assert asyncio.run(api.custo_do_dia()) == Decimal("1.25")


@pytest.mark.parametrize("status", [401, 503])
def test_rest_client_raises_on_http_errors(status):
    api = ClienteAPI(
        "http://backend:8000",
        "x",
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json={})),
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(api.pendentes())
