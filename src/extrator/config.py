"""Configuração do agente, lida do ambiente ou do `.env`."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PrecoModelo(BaseModel):
    """Preço em US$ por milhão de tokens."""

    model_config = ConfigDict(extra="forbid")

    entrada: Decimal = Field(ge=0)
    saida: Decimal = Field(ge=0)
    cache_leitura: Decimal = Field(ge=0)
    cache_escrita: Decimal = Field(ge=0)


# Tabela de referência (US$/1M tokens, API Anthropic, consulta em 2026-10). Confira a página
# oficial de preços antes de publicar números; sobrescreva com PRECOS_MODELOS em JSON.
# Escrita em cache = 1,25 × entrada (TTL de 5 min). Haiku 5.5: faixa de prompts ≤ 100K tokens;
# seus preços de cache são estimados pela mesma proporção (leitura = 0,1 × entrada).
PRECOS_PADRAO = {
    "claude-sonnet-5-5": PrecoModelo(
        entrada=Decimal("2.00"),
        saida=Decimal("10.00"),
        cache_leitura=Decimal("0.20"),
        cache_escrita=Decimal("2.50"),
    ),
    "claude-opus-5-5": PrecoModelo(
        entrada=Decimal("4.00"),
        saida=Decimal("20.00"),
        cache_leitura=Decimal("0.20"),
        cache_escrita=Decimal("5.00"),
    ),
    "claude-haiku-5-5": PrecoModelo(
        entrada=Decimal("0.10"),
        saida=Decimal("0.50"),
        cache_leitura=Decimal("0.01"),
        cache_escrita=Decimal("0.125"),
    ),
}


class AgenteSettings(BaseSettings):
    # env_ignore_empty: `ANTHROPIC_API_KEY=` vazio no .env/Compose equivale a ausente.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Conexão com o backend (MCP Streamable HTTP + REST para paginação de documentos).
    mcp_url: str = "http://backend:8000/mcp"
    api_token: SecretStr = Field(min_length=32)
    mcp_timeout_s: float = Field(default=60, gt=0, le=600)

    # Modelo. Sem chave explícita, o SDK usa os demais mecanismos de credencial.
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    versao_prompt: str = Field(default="v1", min_length=1, max_length=40)
    precos_modelos: dict[str, PrecoModelo] = Field(default_factory=lambda: dict(PRECOS_PADRAO))

    # Limites de entrada.
    agente_max_paginas: int = Field(default=10, ge=1, le=100)
    agente_max_caracteres_txt: int = Field(default=50_000, ge=1_000, le=1_000_000)
    agente_max_voltas_tools: int = Field(default=12, ge=1, le=50)

    # Limites de custo e operação.
    agente_max_tokens_doc: int = Field(default=150_000, ge=1_000)
    agente_max_custo_doc_usd: Decimal = Field(default=Decimal("0.50"), gt=0)
    agente_orcamento_diario_usd: Decimal = Field(default=Decimal("5.00"), gt=0)
    agente_disjuntor_falhas: int = Field(default=5, ge=1, le=100)
    agente_timeout_doc_s: float = Field(default=180, gt=0, le=3_600)
    agente_intervalo_s: float = Field(default=30, ge=1, le=3_600)

    # Revisão humana obrigatória.
    revisao_limite_total_brl: Decimal = Field(default=Decimal("5000"), gt=0)
    revisao_limite_quantidade: int = Field(default=500, ge=1, le=100_000)

    # Privacidade: mascara CPF/CNPJ/telefone/e-mail do TXT antes de enviar ao modelo.
    agente_mascarar_pii_txt: bool = False

    @model_validator(mode="after")
    def modelo_com_preco(self):
        # Sem preço não há teto de custo verificável; falhar cedo evita gasto sem controle.
        if self.llm_model not in self.precos_modelos:
            raise ValueError(
                f"Defina o preço de {self.llm_model!r} em PRECOS_MODELOS para controlar o custo."
            )
        return self

    @property
    def api_base_url(self) -> str:
        """Base REST do backend, derivada do endpoint MCP."""
        return self.mcp_url.rstrip("/").removesuffix("/mcp")

    @property
    def preco_modelo(self) -> PrecoModelo:
        return self.precos_modelos[self.llm_model]


@lru_cache
def get_agente_settings() -> AgenteSettings:
    return AgenteSettings()
