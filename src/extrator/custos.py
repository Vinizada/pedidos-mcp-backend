"""Custo estimado por documento, com teto de tokens e de US$ verificado a cada chamada."""

from decimal import ROUND_HALF_UP, Decimal

from extrator.config import AgenteSettings, PrecoModelo
from extrator.llm import Uso

MILHAO = Decimal(1_000_000)
CENTAVO_DE_MILESIMO = Decimal("0.000001")


class OrcamentoExcedido(Exception):
    def __init__(self, mensagem: str):
        super().__init__(mensagem)
        self.mensagem = mensagem


def custo(uso: Uso, preco: PrecoModelo) -> Decimal:
    total = (
        uso.entrada * preco.entrada
        + uso.saida * preco.saida
        + uso.cache_leitura * preco.cache_leitura
        + uso.cache_escrita * preco.cache_escrita
    ) / MILHAO
    return total.quantize(CENTAVO_DE_MILESIMO, rounding=ROUND_HALF_UP)


class ContadorCusto:
    def __init__(self, settings: AgenteSettings):
        self.settings = settings
        self.entrada = self.saida = self.cache_leitura = self.cache_escrita = 0
        self.usd = Decimal("0")
        self.modelos: list[str] = []

    @property
    def tokens(self) -> int:
        return self.entrada + self.saida + self.cache_leitura + self.cache_escrita

    def registrar(self, uso: Uso | None, verificar: bool = True) -> None:
        """Soma o uso; com `verificar`, interrompe o documento acima dos tetos."""
        if uso is None:
            return
        self.entrada += uso.entrada
        self.saida += uso.saida
        self.cache_leitura += uso.cache_leitura
        self.cache_escrita += uso.cache_escrita
        # Com fallback de recusa a resposta pode vir de outro modelo: usa o preço dele.
        preco = self.settings.precos_modelos.get(uso.modelo, self.settings.preco_modelo)
        self.usd += custo(uso, preco)
        if uso.modelo not in self.modelos:
            self.modelos.append(uso.modelo)
        if not verificar:
            return
        if self.tokens > self.settings.agente_max_tokens_doc:
            raise OrcamentoExcedido(
                f"Documento excedeu {self.settings.agente_max_tokens_doc} tokens."
            )
        if self.usd > self.settings.agente_max_custo_doc_usd:
            raise OrcamentoExcedido(
                f"Documento excedeu US$ {self.settings.agente_max_custo_doc_usd} estimados."
            )
