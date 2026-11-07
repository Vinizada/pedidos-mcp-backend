import hashlib
from functools import lru_cache

from fastembed import TextEmbedding

from pedidos.config import Settings
from pedidos.models import Produto


def catalog_text(produto: Produto) -> str:
    return " | ".join(
        [
            produto.nome,
            produto.marca,
            produto.categoria,
            produto.unidade,
            produto.embalagem,
            produto.descricao_busca,
        ]
    )


def content_hash(produto: Produto) -> str:
    return hashlib.sha256(catalog_text(produto).encode()).hexdigest()


@lru_cache(maxsize=2)
def embedding_model(modelo: str, cache_dir: str):
    return TextEmbedding(model_name=modelo, cache_dir=cache_dir, threads=2)


def embed_texts(texts: list[str], settings: Settings) -> list[list[float]]:
    model = embedding_model(settings.embedding_model, settings.embedding_cache_dir)
    vectors = [v.tolist() for v in model.embed(texts, batch_size=16)]
    if any(len(vector) != 384 for vector in vectors):
        raise ValueError("O esquema requer um modelo de embeddings de 384 dimensões.")
    return vectors
