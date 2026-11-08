import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    revisao_obrigatoria: bool = False
    motivos_revisao: list[str] = Field(default_factory=list)
    aviso: str = "Rascunho: não reserva nem baixa estoque; confirmação humana fora deste protótipo."


class ErrorOutput(Contract):
    codigo: str
    mensagem: str
    problemas: list[Problema] = Field(default_factory=list)


class ListagemInput(Contract):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100_000)
    consulta: str = Field(default="", max_length=200)


DecisaoAnalise = Literal["criar_rascunho", "pedir_esclarecimento", "rejeitar", "falha"]
Identificador = Annotated[str, Field(min_length=2, max_length=60, pattern=r"^[a-z0-9_]+$")]
MAX_EXTRACAO_BYTES = 65_536


class MotivoAnalise(Contract):
    codigo: str = Field(min_length=2, max_length=60, pattern=r"^[A-Z0-9_]+$")
    mensagem: str = Field(min_length=1, max_length=500)
    item_idx: int | None = Field(default=None, ge=0, le=99, strict=True)


class GuardrailAcionado(Contract):
    guardrail: Identificador
    item_idx: int | None = Field(default=None, ge=0, le=99, strict=True)
    decisao_original: DecisaoAnalise
    decisao_final: DecisaoAnalise


class AnaliseInput(Contract):
    """Resultado do processamento de um documento, registrado pelo consumidor."""

    documento_id: UUID
    chave_idempotencia: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    decisao: DecisaoAnalise
    pedido_id: UUID | None = None
    perguntas: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=50
    )
    motivos: list[MotivoAnalise] = Field(default_factory=list, max_length=100)
    extracao: dict[str, Any] | None = None
    modelo: str = Field(min_length=1, max_length=100)
    versao_prompt: str = Field(min_length=1, max_length=40)
    tokens_entrada: int = Field(default=0, ge=0, strict=True)
    tokens_saida: int = Field(default=0, ge=0, strict=True)
    tokens_cache_leitura: int = Field(default=0, ge=0, strict=True)
    custo_estimado: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=6)
    latencia_ms: int = Field(default=0, ge=0, strict=True)
    revisao_obrigatoria: bool = False
    motivos_revisao: list[Identificador] = Field(default_factory=list, max_length=20)
    guardrails_acionados: list[GuardrailAcionado] = Field(default_factory=list, max_length=200)
    texto_verificavel: bool | None = None

    @field_validator("extracao")
    @classmethod
    def extracao_limitada(cls, value):
        if value is not None and len(json.dumps(value, default=str)) > MAX_EXTRACAO_BYTES:
            raise ValueError("A extração excede 64 KiB; registre apenas os itens extraídos.")
        return value


class AnaliseView(Contract):
    analise_id: UUID
    documento_id: UUID
    decisao: DecisaoAnalise
    pedido_id: UUID | None
    perguntas: list[str]
    motivos: list[MotivoAnalise]
    extracao: dict[str, Any] | None
    modelo: str
    versao_prompt: str
    tokens_entrada: int
    tokens_saida: int
    tokens_cache_leitura: int
    custo_estimado: Decimal
    latencia_ms: int
    revisao_obrigatoria: bool
    motivos_revisao: list[str]
    guardrails_acionados: list[GuardrailAcionado]
    texto_verificavel: bool | None
    criado_em: datetime
    repeticao_idempotente: bool = False


class CustoDiarioView(Contract):
    data: date
    custo_estimado: Decimal
    analises: int


class AnaliseResumo(Contract):
    analise_id: UUID
    decisao: DecisaoAnalise
    pedido_id: UUID | None
    perguntas: list[str]
    motivos: list[MotivoAnalise]
    revisao_obrigatoria: bool
    motivos_revisao: list[str]
    modelo: str
    versao_prompt: str
    criado_em: datetime


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
    revisao_obrigatoria: bool = False
    motivos_revisao: list[str] = Field(default_factory=list)


StatusDocumento = Literal[
    "aguardando_agente",
    "pedido_criado",
    "aguardando_esclarecimento",
    "rejeitado",
    "falha_processamento",
]


class DocumentoView(Contract):
    documento_id: UUID
    nome_original: str
    content_type: Literal["application/pdf", "text/plain", "image/png", "image/jpeg"]
    tamanho_bytes: int
    sha256: str
    criado_em: datetime
    status: StatusDocumento
    pedidos: list[PedidoResumo]
    analises: list[AnaliseResumo] = Field(default_factory=list)
    revisao_obrigatoria: bool = False
