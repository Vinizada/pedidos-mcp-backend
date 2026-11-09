"""Etapa 1: documento → `ExtracaoPedido`, numa única chamada sem acesso ao catálogo."""

from dataclasses import dataclass

from extrator.ingestao import DocumentoPreparado
from extrator.llm import ClienteLLM, RespostaLLM
from extrator.prompts import INSTRUCAO_EXTRACAO, SYSTEM_EXTRACAO
from extrator.schemas import ExtracaoPedido

MAX_TOKENS_EXTRACAO = 16_000


@dataclass(frozen=True, slots=True)
class ResultadoExtracao:
    extracao: ExtracaoPedido
    resposta: RespostaLLM


async def extrair_pedido(llm: ClienteLLM, documento: DocumentoPreparado) -> ResultadoExtracao:
    """Levanta `ErroLLM` em recusa, truncamento, saída inválida ou falha da API."""
    # Bloco do documento antes da instrução, como a API recomenda.
    conteudo = [*documento.blocos, {"type": "text", "text": INSTRUCAO_EXTRACAO}]
    extracao, resposta = await llm.estruturar(
        system=SYSTEM_EXTRACAO,
        conteudo=conteudo,
        formato=ExtracaoPedido,
        max_tokens=MAX_TOKENS_EXTRACAO,
    )
    return ResultadoExtracao(extracao, resposta)
