from datetime import UTC, datetime

from sqlalchemy import select, text

from pedidos.config import get_settings
from pedidos.db import session_factory
from pedidos.models import Produto, ProdutoEmbedding
from pedidos.retrieval import catalog_text, content_hash, embed_texts


def main():
    settings = get_settings()
    with session_factory()() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(1301142027)"))
        produtos = list(session.scalars(select(Produto).where(Produto.ativo.is_(True))))
        existentes = {e.produto_id: e for e in session.scalars(select(ProdutoEmbedding))}
        pendentes = [
            p
            for p in produtos
            if p.id not in existentes
            or existentes[p.id].hash_conteudo != content_hash(p)
            or existentes[p.id].modelo != settings.embedding_model
        ]
        if pendentes:
            vectors = embed_texts([catalog_text(p) for p in pendentes], settings)
            for p, vector in zip(pendentes, vectors, strict=True):
                session.merge(
                    ProdutoEmbedding(
                        produto_id=p.id,
                        modelo=settings.embedding_model,
                        hash_conteudo=content_hash(p),
                        vetor=vector,
                        atualizado_em=datetime.now(UTC),
                    )
                )
            session.commit()
        print(f"Índice de catálogo: {len(pendentes)} atualizados, {len(produtos)} ativos.")


if __name__ == "__main__":
    main()
