from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BuscaInput(Contract):
    consulta: str = Field(min_length=2, max_length=300)
    limite: int = Field(default=5, ge=1, le=20, strict=True)


class ProdutoIdsInput(Contract):
    produto_ids: list[UUID] = Field(min_length=1, max_length=100)


class ItemInput(Contract):
    produto_id: UUID
    quantidade: int = Field(gt=0, le=100_000, strict=True)
    unidade: Literal["UN"] = "UN"


class CarrinhoInput(Contract):
    cliente_id: UUID
    itens: list[ItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def sem_produtos_duplicados(self):
        ids = [item.produto_id for item in self.itens]
        if len(ids) != len(set(ids)):
            raise ValueError("Consolide as quantidades de produtos repetidos antes de enviar.")
        return self


class PedidoInput(CarrinhoInput):
    chave_idempotencia: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    referencia_documento: str | None = Field(default=None, max_length=200)
    documento_id: UUID | None = None


Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]


class ProdutoView(Contract):
    produto_id: UUID
    sku: str
    ean: str
    nome: str
    marca: str
    categoria: str
    unidade: str
    embalagem: str
    ativo: bool
    preco_unitario: Money | None
    estoque_disponivel: int


class Candidato(ProdutoView):
    score: float
    origem: Literal["ean", "sku", "lexical", "hibrida"]


class BuscaOutput(Contract):
    consulta: str
    modo: str
    candidatos: list[Candidato]
    aviso: str = (
        "Ranking não é probabilidade. Confirme marca, embalagem e unidade; não substitua itens."
    )


class DisponibilidadeOutput(Contract):
    produtos: list[ProdutoView]
    nao_encontrados: list[UUID]


class ClienteView(Contract):
    cliente_id: UUID
    codigo: str
    nome: str
    ativo: bool


class ClientesOutput(Contract):
    clientes: list[ClienteView]


class Problema(Contract):
    codigo: str
    mensagem: str
    produto_id: UUID | None = None


class ItemCalculado(Contract):
    produto_id: UUID
    ean: str
    nome_produto: str
    unidade: str
    embalagem: str
    quantidade: int
    preco_unitario: Money
    subtotal: Money


class CarrinhoOutput(Contract):
    valido: bool
    cliente_id: UUID
    moeda: Literal["BRL"] = "BRL"
    itens: list[ItemCalculado]
    total: Money | None
    problemas: list[Problema]
    aviso: str = "Consulta não reserva estoque. Rascunho não é pedido confirmado."


class PedidoView(Contract):
    pedido_id: UUID
    cliente_id: UUID
    cliente_nome: str
    cliente_codigo: str
    status: Literal["rascunho"]
    criado_em: datetime
    moeda: Literal["BRL"] = "BRL"
    total: Money
    referencia_documento: str | None
    documento_id: UUID | None = None
    itens: list[ItemCalculado]
    repeticao_idempotente: bool = False
    aviso: str = "Rascunho: não reserva nem baixa estoque; confirmação humana fora deste protótipo."


class ErrorOutput(Contract):
    codigo: str
    mensagem: str
    problemas: list[Problema] = Field(default_factory=list)


class ListagemInput(Contract):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100_000)
    consulta: str = Field(default="", max_length=200)


class Page[T](Contract):
    items: list[T]
    total: int
    limit: int
    offset: int


class ClienteDetalhe(ClienteView):
    email: str


class PedidoResumo(Contract):
    pedido_id: UUID
    cliente_id: UUID
    cliente_nome: str
    cliente_codigo: str
    documento_id: UUID | None
    status: Literal["rascunho"]
    total: Money
    criado_em: datetime


class DocumentoView(Contract):
    documento_id: UUID
    nome_original: str
    content_type: Literal["application/pdf", "text/plain"]
    tamanho_bytes: int
    sha256: str
    criado_em: datetime
    status: Literal["aguardando_agente", "pedido_criado"]
    pedidos: list[PedidoResumo]
