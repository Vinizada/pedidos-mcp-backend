from pedidos.schemas import Problema


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
