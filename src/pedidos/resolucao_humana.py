"""Resolução humana de um esclarecimento: uma pessoa escolhe cliente, produtos e quantidades.

Disponível só pela API REST da interface, nunca como ferramenta MCP: um agente não pode
aprovar a própria pendência. O pedido passa pelas mesmas validações de preço, estoque,
cliente e unidade de qualquer rascunho.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pedidos.analise_service import AnaliseService
from pedidos.config import Settings
from pedidos.errors import DomainError
from pedidos.models import AnaliseDocumento, Documento, Pedido
from pedidos.schemas import AnaliseInput, PedidoInput, PedidoView, ResolucaoInput
from pedidos.service import CatalogService

MODELO_HUMANO = "humano"
VERSAO_RESOLUCAO = "resolucao-manual"


def _invalida(mensagem: str):
    raise DomainError("RESOLUCAO_INVALIDA", mensagem)


class ResolucaoService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def resolver(self, documento_id: UUID, entrada: ResolucaoInput) -> PedidoView:
        documento = self.session.get(Documento, documento_id)
        if documento is None:
            raise DomainError("DOCUMENTO_NAO_ENCONTRADO", "Documento não encontrado.")
        chave = f"humano:{entrada.analise_id}"
        ja_resolvida = self.session.scalar(
            select(AnaliseDocumento).where(AnaliseDocumento.chave_idempotencia == chave)
        )
        if ja_resolvida is None:
            self._conferir_pendencia(documento_id, entrada.analise_id, chave)

        # Itens repetidos do mesmo produto são somados, como faz o agente.
        quantidades: dict[UUID, int] = {}
        for item in entrada.itens:
            quantidades[item.produto_id] = quantidades.get(item.produto_id, 0) + item.quantidade
        if any(q > 100_000 for q in quantidades.values()):
            _invalida("A soma das quantidades de um mesmo produto excede 100.000.")
        pedido = CatalogService(self.session, self.settings).criar_pedido_rascunho(
            PedidoInput(
                cliente_id=entrada.cliente_id,
                itens=[{"produto_id": p, "quantidade": q} for p, q in quantidades.items()],
                chave_idempotencia=chave,
                referencia_documento=documento.nome_original,
                documento_id=documento_id,
            )
        )
        AnaliseService(self.session).registrar_analise(
            AnaliseInput(
                documento_id=documento_id,
                chave_idempotencia=chave,
                decisao="criar_rascunho",
                pedido_id=pedido.pedido_id,
                motivos=[
                    {
                        "codigo": "RESOLUCAO_HUMANA",
                        "mensagem": entrada.observacao or "Esclarecimento resolvido na interface.",
                    }
                ],
                extracao={
                    "resolve_analise": str(entrada.analise_id),
                    "itens": [i.model_dump(mode="json") for i in entrada.itens],
                },
                modelo=MODELO_HUMANO,
                versao_prompt=VERSAO_RESOLUCAO,
            )
        )
        return pedido

    def _conferir_pendencia(self, documento_id: UUID, analise_id: UUID, chave: str) -> None:
        ultima = self.session.scalar(
            select(AnaliseDocumento)
            .where(AnaliseDocumento.documento_id == documento_id)
            .order_by(AnaliseDocumento.criado_em.desc(), AnaliseDocumento.id.desc())
            .limit(1)
        )
        if ultima is None or ultima.id != analise_id:
            _invalida("A análise informada não é a mais recente deste documento; atualize a tela.")
        if ultima.decisao != "pedir_esclarecimento":
            _invalida("Só é possível resolver um documento aguardando esclarecimento.")
        outro_pedido = self.session.scalar(
            select(Pedido.id).where(
                Pedido.documento_id == documento_id, Pedido.chave_idempotencia != chave
            )
        )
        if outro_pedido is not None:
            _invalida("Este documento já tem um pedido vinculado.")
