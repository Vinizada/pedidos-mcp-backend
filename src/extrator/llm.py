"""Acesso ao modelo por interface: implementação Anthropic e implementação roteirizada.

Regras da API seguidas aqui: thinking adaptativo (sem `budget_tokens`), profundidade por
`output_config.effort`, sem `temperature`/`top_p`/`top_k`, sem prefill, `stop_reason`
verificado antes de ler o conteúdo, e fallback de recusa no servidor quando o modelo aceita.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from extrator.config import AgenteSettings

# `fallbacks: "default"` reexecuta no servidor uma requisição recusada, no modelo indicado
# para a categoria da recusa. Haiku 5.5 não tem fallback de servidor (lista → 400).
BETA_FALLBACK = "server-side-fallback-2026-07-01"
MODELOS_COM_FALLBACK = frozenset(
    {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
)

ModeloT = TypeVar("ModeloT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class Uso:
    modelo: str
    entrada: int = 0
    saida: int = 0
    cache_leitura: int = 0
    cache_escrita: int = 0

    @classmethod
    def da_resposta(cls, resposta: Any) -> "Uso":
        usage = resposta.usage
        return cls(
            modelo=resposta.model,
            entrada=usage.input_tokens or 0,
            saida=usage.output_tokens or 0,
            cache_leitura=getattr(usage, "cache_read_input_tokens", None) or 0,
            cache_escrita=getattr(usage, "cache_creation_input_tokens", None) or 0,
        )

    @property
    def total(self) -> int:
        return self.entrada + self.saida + self.cache_leitura + self.cache_escrita


@dataclass(frozen=True, slots=True)
class RespostaLLM:
    stop_reason: str
    conteudo: list[Any]
    uso: Uso
    request_id: str | None = None

    @property
    def texto(self) -> str:
        return "".join(b.text for b in self.conteudo if getattr(b, "type", None) == "text")


class ErroLLM(Exception):
    """Falha da chamada ao modelo. Nunca inclui conteúdo do documento na mensagem.

    `uso` registra tokens já consumidos (ex.: recusa ou truncamento), para o custo.
    """

    CODIGOS = frozenset(
        {
            "RECUSA",
            "LIMITE_TOKENS",
            "SAIDA_INVALIDA",
            "AUTENTICACAO",
            "INDISPONIVEL",
            "REQUISICAO_INVALIDA",
        }
    )

    def __init__(self, codigo: str, mensagem: str, uso: Uso | None = None):
        if codigo not in self.CODIGOS:
            raise ValueError(f"Código de erro do modelo desconhecido: {codigo}")
        super().__init__(f"{codigo}: {mensagem}")
        self.codigo = codigo
        self.mensagem = mensagem
        self.uso = uso

    @property
    def transitorio(self) -> bool:
        """Conta para o disjuntor do worker (falha de serviço, não do documento)."""
        return self.codigo in {"AUTENTICACAO", "INDISPONIVEL"}


class ClienteLLM(Protocol):
    async def estruturar(
        self,
        *,
        system: str,
        conteudo: list[dict[str, Any]],
        formato: type[ModeloT],
        max_tokens: int,
    ) -> tuple[ModeloT, RespostaLLM]:
        """Uma chamada com saída estruturada validada contra `formato`."""
        ...

    async def conversar(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> RespostaLLM:
        """Uma volta do loop de ferramentas. Levanta `ErroLLM` em recusa ou truncamento."""
        ...


def _erro_api(exc: anthropic.APIError) -> ErroLLM:
    # Mensagens da API podem citar a requisição: guardamos só tipo, status e request id.
    request_id = getattr(exc, "request_id", None)
    sufixo = f" (request id {request_id})" if request_id else ""
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return ErroLLM("AUTENTICACAO", f"Credencial recusada pela API{sufixo}.")
    if isinstance(exc, anthropic.APIConnectionError | anthropic.APITimeoutError):
        return ErroLLM("INDISPONIVEL", f"Sem conexão com a API ({type(exc).__name__}){sufixo}.")
    status = getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.RateLimitError) or (status is not None and status >= 500):
        return ErroLLM("INDISPONIVEL", f"API indisponível (HTTP {status}){sufixo}.")
    return ErroLLM("REQUISICAO_INVALIDA", f"Requisição rejeitada (HTTP {status}){sufixo}.")


class ClienteAnthropic:
    """Implementação real. `client` pode ser injetado nos testes."""

    def __init__(self, settings: AgenteSettings, client: Any | None = None):
        self.modelo = settings.llm_model
        self.effort = settings.llm_effort
        if client is None:
            chave = settings.anthropic_api_key
            client = anthropic.AsyncAnthropic(
                # Sem chave explícita, o SDK resolve a credencial do ambiente.
                api_key=chave.get_secret_value() if chave else None,
                timeout=settings.agente_timeout_doc_s,
                max_retries=2,
            )
        self.client = client

    def _parametros_comuns(self) -> dict[str, Any]:
        parametros: dict[str, Any] = {"model": self.modelo, "thinking": {"type": "adaptive"}}
        if self.modelo in MODELOS_COM_FALLBACK:
            parametros |= {"betas": [BETA_FALLBACK], "fallbacks": "default"}
        return parametros

    async def estruturar(self, *, system, conteudo, formato, max_tokens):
        parametros = self._parametros_comuns() | {
            "max_tokens": max_tokens,
            # Prefixo estável em cache; o documento vem depois, nas mensagens.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": conteudo}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(formato)},
            },
        }
        try:
            resposta = await self.client.beta.messages.create(**parametros)
        except anthropic.APIError as exc:
            raise _erro_api(exc) from None
        resultado = RespostaLLM(
            stop_reason=resposta.stop_reason,
            conteudo=list(resposta.content),
            uso=Uso.da_resposta(resposta),
            request_id=getattr(resposta, "_request_id", None),
        )
        return validar_estruturado(resultado, formato, getattr(resposta, "stop_details", None))

    async def conversar(self, *, system, messages, tools, max_tokens):
        ferramentas = [dict(t) for t in tools]
        if ferramentas:
            # Breakpoint no fim das ferramentas: tools + system ficam no prefixo em cache.
            ferramentas[-1]["cache_control"] = {"type": "ephemeral"}
        parametros = self._parametros_comuns() | {
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "tools": ferramentas,
            # Forçar ferramenta (`any`/`tool`) retorna 400 nestes modelos.
            "tool_choice": {"type": "auto"},
            "output_config": {"effort": self.effort},
            # Cache automático do histórico, que cresce a cada volta.
            "cache_control": {"type": "ephemeral"},
        }
        try:
            resposta = await self.client.beta.messages.create(**parametros)
        except anthropic.APIError as exc:
            raise _erro_api(exc) from None
        resultado = RespostaLLM(
            stop_reason=resposta.stop_reason,
            conteudo=list(resposta.content),
            uso=Uso.da_resposta(resposta),
            request_id=getattr(resposta, "_request_id", None),
        )
        verificar_parada(resultado, getattr(resposta, "stop_details", None))
        return resultado


def verificar_parada(resposta: RespostaLLM, stop_details: Any = None) -> None:
    if resposta.stop_reason == "refusal":
        categoria = getattr(stop_details, "category", None) or "não informada"
        raise ErroLLM(
            "RECUSA", f"O modelo recusou a solicitação (categoria {categoria}).", resposta.uso
        )
    if resposta.stop_reason == "max_tokens":
        raise ErroLLM("LIMITE_TOKENS", "Resposta truncada pelo limite de tokens.", resposta.uso)


def validar_estruturado[M: BaseModel](
    resposta: RespostaLLM, formato: type[M], stop_details: Any = None
) -> tuple[M, RespostaLLM]:
    """Checa `stop_reason` antes de ler o conteúdo; só então valida o JSON."""
    verificar_parada(resposta, stop_details)
    try:
        return formato.model_validate_json(resposta.texto), resposta
    except ValidationError as exc:
        # Só a contagem de erros: os detalhes poderiam citar trechos do documento.
        raise ErroLLM(
            "SAIDA_INVALIDA",
            f"Saída fora do formato esperado ({exc.error_count()} erro(s) de validação).",
            resposta.uso,
        ) from None


@dataclass(frozen=True, slots=True)
class _Bloco:
    text: str
    type: str = "text"


@dataclass(frozen=True, slots=True)
class BlocoToolUse:
    """Bloco `tool_use` no formato lido pelo loop (para roteiros de teste)."""

    name: str
    input: dict[str, Any]
    id: str
    type: str = "tool_use"


def resposta_ferramentas(*blocos: BlocoToolUse, modelo: str = "modelo-roteirizado") -> RespostaLLM:
    return RespostaLLM("tool_use", list(blocos), Uso(modelo, entrada=2_000, saida=150))


@dataclass
class ClienteLLMRoteirizado:
    """Devolve respostas pré-definidas, em ordem, e registra as chamadas. Sem rede.

    Cada passo é um texto (resposta `end_turn`), uma `RespostaLLM`, uma exceção ou uma função
    que recebe os argumentos da chamada e devolve um desses (para reagir aos resultados
    das ferramentas, como IDs gerados pelo backend).
    """

    passos: Sequence[str | RespostaLLM | Exception | Callable[[dict[str, Any]], Any]]
    modelo: str = "modelo-roteirizado"
    chamadas: list[dict[str, Any]] = field(default_factory=list)

    def _proximo(self, **chamada) -> RespostaLLM:
        self.chamadas.append(chamada)
        indice = len(self.chamadas) - 1
        if indice >= len(self.passos):
            raise AssertionError("Roteiro esgotado: chamada ao modelo não prevista.")
        passo = self.passos[indice]
        if callable(passo) and not isinstance(passo, Exception):
            passo = passo(chamada)
        if isinstance(passo, Exception):
            raise passo
        if isinstance(passo, RespostaLLM):
            return passo
        return RespostaLLM("end_turn", [_Bloco(passo)], Uso(self.modelo, entrada=1_000, saida=200))

    async def estruturar(self, *, system, conteudo, formato, max_tokens):
        resposta = self._proximo(system=system, conteudo=conteudo, formato=formato)
        return validar_estruturado(resposta, formato)

    async def conversar(self, *, system, messages, tools, max_tokens):
        # Cópia rasa: o loop acrescenta mensagens depois; o roteiro vê o estado desta volta.
        resposta = self._proximo(system=system, messages=list(messages), tools=tools)
        verificar_parada(resposta)
        return resposta
