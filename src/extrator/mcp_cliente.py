"""Cliente MCP do agente: leitura de documentos e ponte de ferramentas para o modelo.

O modelo só recebe as ferramentas de `FERRAMENTAS_LLM`, por lista fechada. As de escrita
(`criar_pedido_rascunho`, `registrar_analise`) são chamadas apenas pelo orquestrador.
"""

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import BlobResourceContents, TextResourceContents, Tool
from pydantic import AnyUrl

# Ordem fixa: as definições enviadas ao modelo precisam ser idênticas a cada requisição
# para o cache de prompt funcionar.
FERRAMENTAS_LLM = (
    "buscar_produtos",
    "buscar_cliente",
    "consultar_disponibilidade",
    "validar_carrinho",
)

# Palavras-chave que o modo `strict` não aceita; o backend continua validando cada uma.
RESTRICOES_SERVIDOR = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
    }
)
# Valores literais: não são schemas e não devem ser percorridos.
VALORES_LITERAIS = frozenset({"default", "enum", "const", "examples"})


class ErroMCP(Exception):
    """Falha na comunicação com o backend; a mensagem não contém dados do documento."""


class FerramentasIndisponiveis(ErroMCP):
    pass


class FerramentaNaoPermitida(ErroMCP):
    pass


class ErroRecurso(ErroMCP):
    def __init__(self, mensagem: str, codigo: str = "RECURSO_INDISPONIVEL"):
        super().__init__(f"{codigo}: {mensagem}")
        self.codigo = codigo
        self.mensagem = mensagem


class DocumentoCorrompido(ErroMCP):
    pass


class ErroFerramenta(ErroMCP):
    """`isError=true` de uma ferramenta, com o JSON de erro do backend quando houver."""

    def __init__(self, ferramenta: str, codigo: str, mensagem: str, problemas: list | None = None):
        super().__init__(f"{ferramenta}: {codigo}: {mensagem}")
        self.ferramenta = ferramenta
        self.codigo = codigo
        self.mensagem = mensagem
        self.problemas = problemas or []

    def como_dict(self) -> dict[str, Any]:
        return {"codigo": self.codigo, "mensagem": self.mensagem, "problemas": self.problemas}


@dataclass(frozen=True, slots=True)
class DocumentoBaixado:
    documento_id: UUID
    nome_original: str
    content_type: str
    sha256: str
    conteudo: bytes
    metadados: dict[str, Any]


def schema_estrito(schema: dict[str, Any]) -> dict[str, Any]:
    """Converte o `inputSchema` MCP para o subconjunto aceito por tools `strict: true`.

    Resolve `$ref` para `$defs` (rejeitando recursão), exige `additionalProperties: false`
    em todo objeto e move restrições não suportadas para a descrição do campo.
    """
    definicoes = schema.get("$defs", {})

    def resolver(no: Any, pilha: frozenset[str]) -> Any:
        if isinstance(no, list):
            return [resolver(item, pilha) for item in no]
        if not isinstance(no, dict):
            return no
        if "$ref" in no:
            nome = no["$ref"].removeprefix("#/$defs/")
            if nome in pilha:
                raise ValueError(f"Schema recursivo não é aceito em modo strict: {nome}.")
            if nome not in definicoes:
                raise ValueError(f"Referência de schema não encontrada: {no['$ref']}.")
            local = {k: v for k, v in no.items() if k != "$ref"}
            return resolver({**definicoes[nome], **local}, pilha | {nome})
        saida: dict[str, Any] = {}
        notas: list[str] = []
        for chave, valor in no.items():
            if chave in {"$defs", "title"}:
                continue
            if chave in RESTRICOES_SERVIDOR or (chave == "minItems" and valor > 1):
                notas.append(f"{chave}={valor}")
            elif chave == "properties":
                saida[chave] = {campo: resolver(sub, pilha) for campo, sub in valor.items()}
            elif chave in VALORES_LITERAIS:
                saida[chave] = valor
            else:
                saida[chave] = resolver(valor, pilha)
        if saida.get("type") == "object" or "properties" in saida:
            saida["additionalProperties"] = False
        if notas:
            restricoes = f"Validado no servidor: {', '.join(notas)}."
            descricao = saida.get("description")
            saida["description"] = f"{descricao} {restricoes}" if descricao else restricoes
        return saida

    return resolver(schema, frozenset())


def ferramenta_para_claude(ferramenta: Tool) -> dict[str, Any]:
    if ferramenta.name not in FERRAMENTAS_LLM:
        raise FerramentaNaoPermitida(f"{ferramenta.name} não pode ser oferecida ao modelo.")
    return {
        "name": ferramenta.name,
        "description": ferramenta.description or "",
        "input_schema": schema_estrito(ferramenta.inputSchema),
        "strict": True,
    }


def _json_de_erro(texto: str) -> dict[str, Any] | None:
    """Extrai o JSON de erro do backend; o SDK prefixa a mensagem ("Error executing tool…")."""
    inicio = texto.find("{")
    if inicio < 0:
        return None
    try:
        dados = json.loads(texto[inicio:])
    except ValueError:
        return None
    return dados if isinstance(dados, dict) and isinstance(dados.get("codigo"), str) else None


def _erro_ferramenta(nome: str, conteudo: list) -> ErroFerramenta:
    texto = " ".join(getattr(bloco, "text", "") for bloco in conteudo).strip()
    dados = _json_de_erro(texto)
    if dados:
        return ErroFerramenta(
            nome, dados["codigo"], str(dados.get("mensagem", "")), dados.get("problemas")
        )
    # Erros de argumentos vêm do SDK como texto de validação. Podem citar valores enviados
    # pelo modelo: voltam ao modelo como tool_result, mas não devem ser logados.
    texto = texto.removeprefix(f"Error executing tool {nome}: ")
    return ErroFerramenta(nome, "ARGUMENTOS_INVALIDOS", texto[:1000] or "Argumentos inválidos.")


class ClienteMCP:
    def __init__(self, sessao: ClientSession):
        self.sessao = sessao

    @classmethod
    @asynccontextmanager
    async def conectar(
        cls, url: str, token: str, timeout: float = 60
    ) -> AsyncIterator["ClienteMCP"]:
        """Abre a sessão MCP com Bearer.

        Trate as exceções tipadas dentro do bloco `async with`: ao atravessar a sessão, o
        anyio as entrega embrulhadas em `ExceptionGroup`.
        """
        # O token vai apenas para o endpoint configurado.
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=timeout
        ) as http:
            async with streamable_http_client(url, http_client=http) as (leitura, escrita, _):
                async with ClientSession(leitura, escrita) as sessao:
                    await sessao.initialize()
                    yield cls(sessao)

    async def ferramentas_llm(self) -> list[dict[str, Any]]:
        """Definições para o modelo: só a lista fechada, na ordem de `FERRAMENTAS_LLM`."""
        oferecidas = {f.name: f for f in (await self.sessao.list_tools()).tools}
        ausentes = [nome for nome in FERRAMENTAS_LLM if nome not in oferecidas]
        if ausentes:
            raise FerramentasIndisponiveis(f"O backend não oferece: {', '.join(ausentes)}.")
        return [ferramenta_para_claude(oferecidas[nome]) for nome in FERRAMENTAS_LLM]

    async def chamar(self, nome: str, argumentos: dict[str, Any]) -> dict[str, Any]:
        """Chama qualquer ferramenta do backend; uso exclusivo do orquestrador."""
        resultado = await self.sessao.call_tool(nome, argumentos)
        if resultado.isError:
            raise _erro_ferramenta(nome, resultado.content)
        if resultado.structuredContent is not None:
            return resultado.structuredContent
        texto = " ".join(getattr(bloco, "text", "") for bloco in resultado.content)
        return json.loads(texto)

    async def chamar_para_llm(self, nome: str, argumentos: dict[str, Any]) -> dict[str, Any]:
        """Chamada pedida pelo modelo: recusada antes de sair se não estiver na lista fechada."""
        if nome not in FERRAMENTAS_LLM:
            raise FerramentaNaoPermitida(f"{nome} não está disponível para o modelo.")
        return await self.chamar(nome, argumentos)

    async def _ler_recurso(self, uri: str):
        try:
            resultado = await self.sessao.read_resource(AnyUrl(uri))
        except McpError as exc:
            dados = _json_de_erro(exc.error.message)
            if dados:
                raise ErroRecurso(str(dados.get("mensagem", "")), dados["codigo"]) from None
            raise ErroRecurso("Recurso indisponível no backend.") from None
        if not resultado.contents:
            raise ErroRecurso("Recurso sem conteúdo.")
        return resultado.contents[0]

    async def ler_metadados(self, documento_id: UUID) -> dict[str, Any]:
        conteudo = await self._ler_recurso(f"pedidos://documentos/{documento_id}")
        if not isinstance(conteudo, TextResourceContents):
            raise ErroRecurso("Metadados do documento em formato inesperado.")
        return json.loads(conteudo.text)

    async def baixar_documento(self, documento_id: UUID) -> DocumentoBaixado:
        metadados = await self.ler_metadados(documento_id)
        conteudo = await self._ler_recurso(f"pedidos://documentos/{documento_id}/arquivo")
        if not isinstance(conteudo, BlobResourceContents):
            raise ErroRecurso("Arquivo do documento em formato inesperado.")
        dados = base64.b64decode(conteudo.blob, validate=True)
        # Garante que o modelo recebe exatamente o original registrado no upload.
        if hashlib.sha256(dados).hexdigest() != metadados["sha256"]:
            raise DocumentoCorrompido("O arquivo recebido não corresponde ao SHA-256 registrado.")
        return DocumentoBaixado(
            documento_id=UUID(metadados["documento_id"]),
            nome_original=metadados["nome_original"],
            content_type=metadados["content_type"],
            sha256=metadados["sha256"],
            conteudo=dados,
            metadados=metadados,
        )
