"""Orquestração de um documento: Etapa 1 → guardrails → Etapa 2 → guardrails → ação.

Somente este módulo chama as ferramentas de escrita (`criar_pedido_rascunho`,
`registrar_analise`), depois das verificações. Logs contêm identificadores e métricas,
nunca conteúdo do documento, prompts ou respostas do modelo.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from extrator.config import AgenteSettings
from extrator.custos import ContadorCusto, OrcamentoExcedido
from extrator.extracao import extrair_pedido
from extrator.guardrails_extracao import verificar_extracao
from extrator.guardrails_resolucao import (
    ContextoResolucao,
    aplicar_guardrails,
    rebaixar_por_revalidacao,
)
from extrator.ingestao import DocumentoRecusado, LimitesIngestao, preparar_documento
from extrator.llm import ClienteLLM, ErroLLM
from extrator.mcp_cliente import ClienteMCP, DocumentoCorrompido, ErroFerramenta, ErroMCP
from extrator.resolucao import LimiteVoltas, resolver
from extrator.revisao import motivos_revisao
from pedidos.schemas import MAX_EXTRACAO_BYTES

LOGGER = logging.getLogger("extrator")


class ErroTransitorio(Exception):
    """Falha de serviço (API do modelo ou backend): o documento continua pendente."""


@dataclass(frozen=True, slots=True)
class ResultadoProcessamento:
    documento_id: UUID
    situacao: Literal["processado", "pulado"]
    decisao: str | None = None
    pedido_id: str | None = None
    analise_id: str | None = None
    motivo: str | None = None
    revisao_obrigatoria: bool = False
    custo_usd: Decimal = Decimal("0")
    tokens: int = 0
    latencia_ms: int = 0

    def como_dict(self) -> dict[str, Any]:
        valores = {f.name: getattr(self, f.name) for f in fields(self)}
        return {k: str(v) if isinstance(v, UUID | Decimal) else v for k, v in valores.items()}


@dataclass
class _Analise:
    decisao: str
    pedido_id: str | None = None
    perguntas: list[str] = field(default_factory=list)
    motivos: list[dict[str, Any]] = field(default_factory=list)
    extracao: dict[str, Any] | None = None
    motivos_revisao: list[str] = field(default_factory=list)
    acionamentos: list[dict[str, Any]] = field(default_factory=list)
    texto_verificavel: bool | None = None


def _codigo(texto: str | None, padrao: str) -> str:
    codigo = re.sub(r"[^A-Z0-9_]", "_", (texto or "").upper())[:60].strip("_")
    return codigo if len(codigo) >= 2 else padrao


def _motivos(motivos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "codigo": _codigo(m.get("codigo"), "MOTIVO"),
            "mensagem": str(m.get("mensagem") or "Sem detalhes.")[:500],
            "item_idx": m.get("item_idx")
            if isinstance(m.get("item_idx"), int) and 0 <= m["item_idx"] <= 99
            else None,
        }
        for m in motivos
    ][:100]


def _limitar(extracao: dict[str, Any] | None) -> dict[str, Any] | None:
    """Mantém o registro abaixo do limite do backend, sacrificando primeiro as evidências."""
    if extracao is None or len(json.dumps(extracao, default=str)) <= MAX_EXTRACAO_BYTES:
        return extracao
    reduzida = dict(extracao)
    reduzida["itens"] = [
        {k: v for k, v in i.items() if k != "evidencia"} for i in extracao.get("itens", [])
    ]
    while reduzida["itens"] and len(json.dumps(reduzida, default=str)) > MAX_EXTRACAO_BYTES:
        reduzida["itens"] = reduzida["itens"][:-1]
        reduzida["truncado"] = True
    return reduzida


class Orquestrador:
    def __init__(
        self,
        mcp: ClienteMCP,
        llm: ClienteLLM,
        settings: AgenteSettings,
        relogio: Callable[[], float] = time.monotonic,
    ):
        self.mcp = mcp
        self.llm = llm
        self.settings = settings
        self.relogio = relogio

    def chave(self, documento_id: UUID) -> str:
        return f"agente:{documento_id}:{self.settings.versao_prompt}"

    async def processar(self, documento_id: UUID) -> ResultadoProcessamento:
        inicio = self.relogio()
        metadados = await self.mcp.ler_metadados(documento_id)
        if metadados.get("pedidos"):
            return ResultadoProcessamento(documento_id, "pulado", motivo="pedido_existente")
        versao = self.settings.versao_prompt
        if any(a.get("versao_prompt") == versao for a in metadados.get("analises", [])):
            return ResultadoProcessamento(documento_id, "pulado", motivo="analise_existente")

        contador = ContadorCusto(self.settings)
        try:
            async with asyncio.timeout(self.settings.agente_timeout_doc_s):
                analise = await self._analisar(documento_id, metadados, contador)
        except ErroLLM as exc:
            contador.registrar(exc.uso, verificar=False)
            if exc.transitorio:
                raise ErroTransitorio(exc.mensagem) from None
            analise = self._falha(exc.codigo, exc.mensagem)
        except OrcamentoExcedido as exc:
            analise = self._falha("ORCAMENTO_DOCUMENTO", exc.mensagem)
        except LimiteVoltas as exc:
            analise = self._falha("LIMITE_VOLTAS", str(exc))
        except TimeoutError:
            analise = self._falha("TIMEOUT", "Processamento excedeu o tempo limite do documento.")
        except DocumentoCorrompido as exc:
            analise = self._falha("DOCUMENTO_CORROMPIDO", str(exc))
        except ErroFerramenta as exc:
            analise = self._falha(exc.codigo, exc.mensagem)
        except ErroMCP as exc:
            raise ErroTransitorio(f"Backend indisponível ({type(exc).__name__}).") from None
        except Exception as exc:  # Falha não prevista: registrada sem expor detalhes.
            LOGGER.error("documento=%s erro_interno=%s", documento_id, type(exc).__name__)
            analise = self._falha("ERRO_INTERNO", "Falha interna do agente.")

        latencia = int((self.relogio() - inicio) * 1000)
        registrada = await self.mcp.chamar(
            "registrar_analise",
            {"entrada": self._payload(documento_id, analise, contador, latencia)},
        )
        LOGGER.info(
            "documento=%s sha256=%s decisao=%s modelos=%s tokens_entrada=%d tokens_saida=%d "
            "tokens_cache=%d custo_usd=%s latencia_ms=%d revisao=%s",
            documento_id,
            metadados.get("sha256"),
            analise.decisao,
            ",".join(contador.modelos) or "-",
            contador.entrada,
            contador.saida,
            contador.cache_leitura,
            contador.usd,
            latencia,
            bool(analise.motivos_revisao),
        )
        return ResultadoProcessamento(
            documento_id,
            "processado",
            decisao=analise.decisao,
            pedido_id=analise.pedido_id,
            analise_id=registrada.get("analise_id"),
            revisao_obrigatoria=bool(analise.motivos_revisao),
            custo_usd=contador.usd,
            tokens=contador.tokens,
            latencia_ms=latencia,
        )

    def _falha(self, codigo: str, mensagem: str) -> _Analise:
        return _Analise("falha", motivos=[{"codigo": codigo, "mensagem": mensagem}])

    def _payload(
        self, documento_id: UUID, analise: _Analise, contador: ContadorCusto, latencia: int
    ) -> dict[str, Any]:
        return {
            "documento_id": str(documento_id),
            "chave_idempotencia": self.chave(documento_id),
            "decisao": analise.decisao,
            "pedido_id": analise.pedido_id,
            "perguntas": [p[:500] for p in analise.perguntas][:50],
            "motivos": _motivos(analise.motivos),
            "extracao": _limitar(analise.extracao),
            "modelo": (",".join(contador.modelos) or self.settings.llm_model)[:100],
            "versao_prompt": self.settings.versao_prompt,
            "tokens_entrada": contador.entrada,
            "tokens_saida": contador.saida,
            "tokens_cache_leitura": contador.cache_leitura,
            "custo_estimado": str(contador.usd),
            "latencia_ms": latencia,
            "revisao_obrigatoria": bool(analise.motivos_revisao),
            "motivos_revisao": analise.motivos_revisao,
            "guardrails_acionados": analise.acionamentos[:200],
            "texto_verificavel": analise.texto_verificavel,
        }

    async def _analisar(
        self, documento_id: UUID, metadados: dict[str, Any], contador: ContadorCusto
    ) -> _Analise:
        settings = self.settings
        documento = await self.mcp.baixar_documento(documento_id)
        preparado = preparar_documento(
            documento.conteudo, documento.content_type, LimitesIngestao.de_settings(settings)
        )
        if isinstance(preparado, DocumentoRecusado):
            return _Analise(
                "pedir_esclarecimento",
                perguntas=[preparado.pergunta],
                motivos=[{"codigo": preparado.codigo, "mensagem": preparado.pergunta}],
            )

        # Etapa 1 e guardrails de ancoragem.
        extracao = await extrair_pedido(self.llm, preparado)
        contador.registrar(extracao.resposta.uso)
        pedido = extracao.extracao
        verificacao = verificar_extracao(
            pedido, preparado.texto_referencia, preparado.texto_verificavel
        )

        # Etapa 2 só para itens sem bloqueio; itens bloqueados já têm decisão.
        abertos = [item for item in verificacao.itens if item.bloqueio is None]
        etapa2 = None
        if abertos:
            etapa2 = await resolver(
                self.llm,
                self.mcp,
                abertos,
                pedido.cliente_codigo,
                pedido.cliente_nome,
                contador,
                settings.agente_max_voltas_tools,
            )
        contexto = ContextoResolucao(
            itens=verificacao.itens,
            cliente_codigo=pedido.cliente_codigo,
            cliente_nome=pedido.cliente_nome,
            texto_referencia=preparado.texto_referencia,
            texto_verificavel=preparado.texto_verificavel,
            chamadas=etapa2.chamadas if etapa2 else [],
        )
        resultado = aplicar_guardrails(etapa2.decisao if etapa2 else None, contexto)

        # Guardrail 11: revalidação pelo backend antes de criar.
        total = None
        if resultado.decisao == "criar_rascunho":
            carrinho = {"cliente_id": resultado.cliente_id, "itens": resultado.carrinho}
            validacao = await self.mcp.chamar("validar_carrinho", {"entrada": carrinho})
            if validacao["valido"]:
                total = Decimal(str(validacao["total"]))
            else:
                resultado = rebaixar_por_revalidacao(resultado, validacao["problemas"])

        revisao = motivos_revisao(
            resultado,
            verificacao.motivos_revisao,
            total,
            settings.revisao_limite_total_brl,
            settings.revisao_limite_quantidade,
        )
        pedido_id = None
        if resultado.decisao == "criar_rascunho":
            entrada = {
                "cliente_id": resultado.cliente_id,
                "itens": resultado.carrinho,
                "chave_idempotencia": self.chave(documento_id),
                "referencia_documento": str(metadados.get("nome_original", ""))[:200] or None,
                "documento_id": str(documento_id),
            }
            try:
                criado = await self.mcp.chamar("criar_pedido_rascunho", {"entrada": entrada})
                pedido_id = criado["pedido_id"]
            except ErroFerramenta as exc:
                if exc.codigo != "CARRINHO_INVALIDO":
                    raise
                # Estoque/preço mudou entre a validação e a criação.
                resultado = rebaixar_por_revalidacao(resultado, exc.problemas)
                revisao = []

        registro = verificacao.registro(pedido)
        registro["acionamentos_extracao"] = [
            {"guardrail": a.guardrail, "item_idx": a.item_idx, "efeito": a.efeito}
            for a in verificacao.acionamentos
        ]
        registro["chamadas_ferramentas"] = len(etapa2.chamadas) if etapa2 else 0
        return _Analise(
            resultado.decisao,
            pedido_id=pedido_id,
            perguntas=resultado.perguntas,
            motivos=resultado.motivos,
            extracao=registro,
            motivos_revisao=revisao,
            acionamentos=resultado.acionamentos,
            texto_verificavel=preparado.texto_verificavel,
        )


async def processar_documento(
    settings: AgenteSettings, llm: ClienteLLM, documento_id: UUID
) -> ResultadoProcessamento:
    """Abre uma sessão MCP e processa um documento.

    A exceção é capturada dentro da sessão e relançada fora dela, para não chegar ao chamador
    embrulhada em `ExceptionGroup`.
    """
    erro: BaseException | None = None
    resultado = None
    async with ClienteMCP.conectar(
        settings.mcp_url, settings.api_token.get_secret_value(), settings.mcp_timeout_s
    ) as mcp:
        try:
            resultado = await Orquestrador(mcp, llm, settings).processar(documento_id)
        except Exception as exc:
            erro = exc
    if erro is not None:
        raise erro
    return resultado
