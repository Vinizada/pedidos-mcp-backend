"""Registro idempotente das análises produzidas pelo agente consumidor."""

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from pedidos.errors import CODIGOS_REJEICAO, DomainError
from pedidos.models import AnaliseDocumento, Documento, Pedido
from pedidos.privacidade import mascarar_pii, mascarar_pii_json
from pedidos.schemas import (
    AnaliseInput,
    AnaliseResumo,
    AnaliseView,
    CustoDiarioView,
    MotivoAnalise,
)


def analise_view(analise: AnaliseDocumento, repeticao: bool = False) -> AnaliseView:
    return AnaliseView(
        analise_id=analise.id,
        documento_id=analise.documento_id,
        decisao=analise.decisao,
        pedido_id=analise.pedido_id,
        perguntas=analise.perguntas,
        motivos=[MotivoAnalise(**m) for m in analise.motivos],
        extracao=analise.extracao,
        modelo=analise.modelo,
        versao_prompt=analise.versao_prompt,
        tokens_entrada=analise.tokens_entrada,
        tokens_saida=analise.tokens_saida,
        tokens_cache_leitura=analise.tokens_cache_leitura,
        custo_estimado=analise.custo_estimado,
        latencia_ms=analise.latencia_ms,
        revisao_obrigatoria=analise.revisao_obrigatoria,
        motivos_revisao=analise.motivos_revisao,
        guardrails_acionados=analise.guardrails_acionados,
        texto_verificavel=analise.texto_verificavel,
        criado_em=analise.criado_em,
        repeticao_idempotente=repeticao,
    )


def analise_resumo(analise: AnaliseDocumento) -> AnaliseResumo:
    return AnaliseResumo(
        analise_id=analise.id,
        decisao=analise.decisao,
        pedido_id=analise.pedido_id,
        perguntas=analise.perguntas,
        motivos=[MotivoAnalise(**m) for m in analise.motivos],
        revisao_obrigatoria=analise.revisao_obrigatoria,
        motivos_revisao=analise.motivos_revisao,
        modelo=analise.modelo,
        versao_prompt=analise.versao_prompt,
        criado_em=analise.criado_em,
    )


def revisoes_por_pedido(session: Session, pedido_ids: list[UUID]) -> dict[UUID, list[str]]:
    """Motivos de revisão obrigatória por pedido, a partir da análise que o originou."""
    if not pedido_ids:
        return {}
    rows = session.execute(
        select(AnaliseDocumento.pedido_id, AnaliseDocumento.motivos_revisao).where(
            AnaliseDocumento.pedido_id.in_(pedido_ids),
            AnaliseDocumento.revisao_obrigatoria.is_(True),
        )
    ).all()
    result: dict[UUID, list[str]] = {}
    for pedido_id, motivos in rows:
        result.setdefault(pedido_id, [])
        result[pedido_id] += [m for m in motivos if m not in result[pedido_id]]
    return result


class AnaliseService:
    def __init__(self, session: Session):
        self.session = session

    def _inconsistente(self, mensagem: str):
        raise DomainError("ANALISE_INCONSISTENTE", mensagem)

    def _validar(self, entrada: AnaliseInput):
        if self.session.get(Documento, entrada.documento_id) is None:
            raise DomainError("DOCUMENTO_NAO_ENCONTRADO", "Documento não encontrado.")
        if (entrada.decisao == "criar_rascunho") != (entrada.pedido_id is not None):
            self._inconsistente("Informe pedido_id somente para a decisão criar_rascunho.")
        if entrada.pedido_id is not None:
            pedido = self.session.get(Pedido, entrada.pedido_id)
            if pedido is None or pedido.documento_id != entrada.documento_id:
                self._inconsistente("O pedido informado não pertence a este documento.")
        if entrada.decisao == "pedir_esclarecimento" and not entrada.perguntas:
            self._inconsistente("Pedido de esclarecimento exige ao menos uma pergunta.")
        if entrada.decisao == "rejeitar" and not any(
            m.codigo in CODIGOS_REJEICAO for m in entrada.motivos
        ):
            self._inconsistente(
                "Rejeição exige ao menos um motivo com código de erro do backend: "
                + ", ".join(sorted(CODIGOS_REJEICAO))
                + "."
            )

    def registrar_analise(self, entrada: AnaliseInput) -> AnaliseView:
        payload = entrada.model_dump(mode="json", exclude={"chave_idempotencia"})
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        lock = int.from_bytes(
            hashlib.sha256(f"analise:{entrada.chave_idempotencia}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        try:
            self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
            existente = self.session.scalar(
                select(AnaliseDocumento).where(
                    AnaliseDocumento.chave_idempotencia == entrada.chave_idempotencia
                )
            )
            if existente:
                if existente.hash_requisicao != fingerprint:
                    raise DomainError("IDEMPOTENCIA_CONFLITO", "Chave já usada com outro conteúdo.")
                result = analise_view(existente, repeticao=True)
                self.session.commit()
                return result
            self._validar(entrada)
            analise = AnaliseDocumento(
                documento_id=entrada.documento_id,
                decisao=entrada.decisao,
                pedido_id=entrada.pedido_id,
                perguntas=[mascarar_pii(p) for p in entrada.perguntas],
                motivos=[
                    m.model_copy(update={"mensagem": mascarar_pii(m.mensagem)}).model_dump(
                        mode="json"
                    )
                    for m in entrada.motivos
                ],
                extracao=mascarar_pii_json(entrada.extracao),
                modelo=entrada.modelo,
                versao_prompt=entrada.versao_prompt,
                tokens_entrada=entrada.tokens_entrada,
                tokens_saida=entrada.tokens_saida,
                tokens_cache_leitura=entrada.tokens_cache_leitura,
                custo_estimado=entrada.custo_estimado,
                latencia_ms=entrada.latencia_ms,
                chave_idempotencia=entrada.chave_idempotencia,
                hash_requisicao=fingerprint,
                revisao_obrigatoria=entrada.revisao_obrigatoria,
                motivos_revisao=entrada.motivos_revisao,
                guardrails_acionados=[
                    g.model_dump(mode="json") for g in entrada.guardrails_acionados
                ],
                texto_verificavel=entrada.texto_verificavel,
            )
            self.session.add(analise)
            self.session.flush()
            self.session.refresh(analise)
            result = analise_view(analise)
            self.session.commit()
            return result
        except Exception:
            self.session.rollback()
            raise

    def custo_do_dia(self, dia: date | None = None) -> CustoDiarioView:
        """Custo estimado das análises registradas no dia (UTC), para o orçamento do agente."""
        dia = dia or datetime.now(UTC).date()
        inicio = datetime.combine(dia, time.min, tzinfo=UTC)
        custo, quantidade = self.session.execute(
            select(
                func.coalesce(func.sum(AnaliseDocumento.custo_estimado), 0),
                func.count(AnaliseDocumento.id),
            ).where(
                AnaliseDocumento.criado_em >= inicio,
                AnaliseDocumento.criado_em < inicio + timedelta(days=1),
            )
        ).one()
        return CustoDiarioView(data=dia, custo_estimado=Decimal(custo), analises=quantidade)

    def listar_por_documento(self, documento_id: UUID) -> list[AnaliseView]:
        if self.session.get(Documento, documento_id) is None:
            raise DomainError("DOCUMENTO_NAO_ENCONTRADO", "Documento não encontrado.")
        analises = self.session.scalars(
            select(AnaliseDocumento)
            .where(AnaliseDocumento.documento_id == documento_id)
            .order_by(AnaliseDocumento.criado_em.desc(), AnaliseDocumento.id.desc())
        )
        return [analise_view(a) for a in analises]
