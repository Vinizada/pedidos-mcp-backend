from pedidos.schemas import Problema

# Códigos que justificam a decisão `rejeitar` registrada por um consumidor.
# QUANTIDADE_INVALIDA cobre zero/negativo, que o contrato de itens rejeita na validação.
CODIGOS_REJEICAO = frozenset(
    {
        "CLIENTE_INVALIDO",
        "PRODUTO_INEXISTENTE",
        "PRODUTO_INATIVO",
        "SEM_PRECO",
        "ESTOQUE_INSUFICIENTE",
        "CARRINHO_INVALIDO",
        "QUANTIDADE_INVALIDA",
    }
)


class DomainError(Exception):
    """Erro de negócio seguro para exposição ao cliente, sem detalhes internos."""

    def __init__(self, codigo: str, mensagem: str, problemas: list[Problema] | None = None):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        self.problemas = problemas or []

    def as_dict(self):
        return {
            "codigo": self.codigo,
            "mensagem": self.mensagem,
            "problemas": [p.model_dump(mode="json") for p in self.problemas],
        }
