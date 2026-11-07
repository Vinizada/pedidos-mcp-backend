import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Produto(Base):
    __tablename__ = "produtos"
    __table_args__ = (
        CheckConstraint("ean ~ '^[0-9]{13}$'", name="ck_produtos_ean_formato"),
        CheckConstraint("unidade = 'UN'", name="ck_produtos_unidade"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(String(40), unique=True)
    ean: Mapped[str] = mapped_column(String(13), unique=True)
    nome: Mapped[str] = mapped_column(String(200))
    marca: Mapped[str] = mapped_column(String(100))
    categoria: Mapped[str] = mapped_column(String(100))
    unidade: Mapped[str] = mapped_column(String(8), default="UN")
    embalagem: Mapped[str] = mapped_column(String(200))
    descricao_busca: Mapped[str] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)


class Preco(Base):
    __tablename__ = "precos"
    __table_args__ = (
        CheckConstraint("valor > 0", name="ck_precos_valor"),
        CheckConstraint(
            "fim_vigencia IS NULL OR fim_vigencia > inicio_vigencia", name="ck_precos_vigencia"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    produto_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("produtos.id"), index=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    inicio_vigencia: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fim_vigencia: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Estoque(Base):
    __tablename__ = "estoque"
    __table_args__ = (
        CheckConstraint("quantidade >= 0", name="ck_estoque_quantidade"),
        CheckConstraint("reservado >= 0 AND reservado <= quantidade", name="ck_estoque_reservado"),
    )
    produto_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("produtos.id"), primary_key=True)
    quantidade: Mapped[int] = mapped_column(Integer)
    reservado: Mapped[int] = mapped_column(Integer, default=0)


class Cliente(Base):
    __tablename__ = "clientes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    codigo: Mapped[str] = mapped_column(String(40), unique=True)
    nome: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(254))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)


class Pedido(Base):
    __tablename__ = "pedido_cabecalho"
    __table_args__ = (
        CheckConstraint("status = 'rascunho'", name="ck_pedido_status"),
        CheckConstraint("total > 0", name="ck_pedido_total"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cliente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clientes.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="rascunho")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    chave_idempotencia: Mapped[str] = mapped_column(String(100), unique=True)
    hash_requisicao: Mapped[str] = mapped_column(String(64))
    referencia_documento: Mapped[str | None] = mapped_column(String(200))


class PedidoItem(Base):
    __tablename__ = "pedido_itens"
    __table_args__ = (
        UniqueConstraint("pedido_id", "produto_id", name="uq_item_pedido_produto"),
        CheckConstraint("quantidade > 0", name="ck_item_quantidade"),
        CheckConstraint("preco_unitario > 0", name="ck_item_preco"),
        CheckConstraint("subtotal = quantidade * preco_unitario", name="ck_item_subtotal"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pedido_cabecalho.id"), index=True)
    produto_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("produtos.id"))
    ean: Mapped[str] = mapped_column(String(13))
    nome_produto: Mapped[str] = mapped_column(String(200))
    unidade: Mapped[str] = mapped_column(String(8))
    embalagem: Mapped[str] = mapped_column(String(200))
    quantidade: Mapped[int] = mapped_column(Integer)
    preco_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class ProdutoEmbedding(Base):
    __tablename__ = "produto_embeddings"
    produto_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("produtos.id"), primary_key=True)
    modelo: Mapped[str] = mapped_column(String(200))
    hash_conteudo: Mapped[str] = mapped_column(String(64))
    vetor: Mapped[list[float]] = mapped_column(Vector(384))
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
