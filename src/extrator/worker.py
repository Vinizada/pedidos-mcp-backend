"""Worker: processa documentos pendentes em ciclos, com orçamento diário e disjuntor.

Uso: `python -m extrator.worker`. Encerra de forma limpa com SIGTERM/SIGINT.
"""

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from functools import partial
from typing import Any, Protocol
from uuid import UUID

from extrator.api_rest import ClienteAPI
from extrator.config import AgenteSettings, get_agente_settings
from extrator.llm import ClienteAnthropic
from extrator.orquestrador import ResultadoProcessamento, processar_documento

LOGGER = logging.getLogger("extrator.worker")
ESPERA_MAXIMA_S = 3_600


class FonteDocumentos(Protocol):
    async def pendentes(self) -> list[UUID]: ...
    async def custo_do_dia(self) -> Decimal: ...


class Worker:
    def __init__(
        self,
        settings: AgenteSettings,
        api: FonteDocumentos,
        processar: Callable[[UUID], Awaitable[ResultadoProcessamento]],
        relogio: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.api = api
        self.processar = processar
        self.relogio = relogio
        self.falhas_consecutivas = 0
        self.aberturas = 0
        self.pausado_ate: float | None = None

    @property
    def disjuntor_aberto(self) -> bool:
        return self.pausado_ate is not None and self.relogio() < self.pausado_ate

    def _registrar_falha(self, motivo: str) -> None:
        self.falhas_consecutivas += 1
        if self.falhas_consecutivas < self.settings.agente_disjuntor_falhas:
            return
        self.aberturas += 1
        espera = min(self.settings.agente_intervalo_s * 2 ** (self.aberturas - 1), ESPERA_MAXIMA_S)
        self.pausado_ate = self.relogio() + espera
        self.falhas_consecutivas = 0
        LOGGER.warning("disjuntor aberto por %ss após falhas seguidas (%s)", espera, motivo)

    def _registrar_sucesso(self) -> None:
        self.falhas_consecutivas = 0
        self.aberturas = 0
        self.pausado_ate = None

    async def ciclo(self, parar: asyncio.Event | None = None) -> dict[str, Any]:
        if self.disjuntor_aberto:
            return {"estado": "disjuntor_aberto", "processados": 0}
        try:
            gasto = await self.api.custo_do_dia()
            pendentes = await self.api.pendentes()
        except Exception as exc:  # backend indisponível conta para o disjuntor
            self._registrar_falha(type(exc).__name__)
            return {"estado": "backend_indisponivel", "processados": 0}
        orcamento = self.settings.agente_orcamento_diario_usd
        processados = 0
        for documento_id in pendentes:
            if parar is not None and parar.is_set():
                break
            if gasto >= orcamento:
                LOGGER.warning(
                    "orçamento diário atingido (US$ %s de %s); pausa até amanhã", gasto, orcamento
                )
                return {"estado": "orcamento_diario", "processados": processados}
            try:
                resultado = await self.processar(documento_id)
            except Exception as exc:
                # Falha de serviço: o documento continua pendente para o próximo ciclo.
                LOGGER.warning(
                    "documento=%s falha_transitoria=%s", documento_id, type(exc).__name__
                )
                self._registrar_falha(type(exc).__name__)
                if self.disjuntor_aberto:
                    return {"estado": "disjuntor_aberto", "processados": processados}
                continue
            self._registrar_sucesso()
            gasto += resultado.custo_usd
            processados += resultado.situacao == "processado"
        return {"estado": "ok", "processados": processados}

    async def executar(self, parar: asyncio.Event) -> None:
        while not parar.is_set():
            await self.ciclo(parar)
            espera = self.settings.agente_intervalo_s
            if self.disjuntor_aberto:
                espera = max(self.pausado_ate - self.relogio(), 1)
            try:
                await asyncio.wait_for(parar.wait(), timeout=espera)
            except TimeoutError:
                pass


def configurar_logs() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Logs de requisição HTTP trazem URLs; não precisam aparecer em nível INFO.
    for nome in ("httpx", "httpx2"):
        logging.getLogger(nome).setLevel(logging.WARNING)


async def main() -> None:
    configurar_logs()
    settings = get_agente_settings()
    llm = ClienteAnthropic(settings)
    api = ClienteAPI(settings.api_base_url, settings.api_token.get_secret_value())
    worker = Worker(settings, api, partial(processar_documento, settings, llm))
    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sinal, parar.set)
    LOGGER.info(
        "worker iniciado: modelo=%s versao_prompt=%s", settings.llm_model, settings.versao_prompt
    )
    await worker.executar(parar)
    LOGGER.info("worker encerrado")


if __name__ == "__main__":
    asyncio.run(main())
