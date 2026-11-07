import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from pedidos.db import session_factory
from pedidos.fixtures import catalog_records, client_records
from pedidos.models import Cliente, Estoque, Preco, Produto


def stable_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"pedidos-demo/{kind}/{key}")


def seed_catalog(session: Session):
    # Inserção idempotente: não sobrescreve catálogo, preços ou estoques editados.
    session.execute(text("SELECT pg_advisory_xact_lock(1301142026)"))
    for item in catalog_records():
        if session.scalar(select(Produto.id).where(Produto.sku == item["sku"])):
            continue
        values = {k: v for k, v in item.items() if k not in {"preco", "quantidade"}}
        produto = Produto(id=stable_id("produto", item["sku"]), **values)
        session.add(produto)
        session.flush()
        session.add(
            Preco(
                produto_id=produto.id,
                valor=Decimal(str(item["preco"])),
                inicio_vigencia=datetime(2024, 1, 1, tzinfo=UTC),
            )
        )
        session.add(Estoque(produto_id=produto.id, quantidade=item["quantidade"], reservado=0))
    for item in client_records():
        if not session.scalar(select(Cliente.id).where(Cliente.codigo == item["codigo"])):
            session.add(Cliente(id=stable_id("cliente", item["codigo"]), **item))
    session.flush()


def main():
    with session_factory()() as session:
        seed_catalog(session)
        session.commit()
    print("Catálogo e clientes sintéticos disponíveis (seed idempotente).")


if __name__ == "__main__":
    main()
