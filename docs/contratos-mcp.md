# Contratos MCP — v1

Transporte: Streamable HTTP, endpoint `/mcp`, autenticação Bearer com segredo
compartilhado do grupo. Este perfil privado exige cliente que permita definir
headers; não implementa descoberta OAuth para clientes públicos.

Todas as ferramentas recebem um objeto `entrada`, exceto `consultar_pedido`, que
recebe `pedido_id` diretamente. Esquemas JSON exportados em `contracts/mcp-tools.json`
devem ser regenerados a partir do servidor quando o contrato mudar.

| Ferramenta | Entrada | Resultado |
|---|---|---|
| buscar_produtos | consulta; limite 1–20 (padrão 5) | candidatos com UUID, SKU, EAN, marca, embalagem, unidade, preço, estoque, score e origem |
| consultar_disponibilidade | produto_ids: 1–100 UUIDs | produtos atuais e IDs não encontrados |
| buscar_cliente | consulta; limite 1–20 | candidatos por código/nome; nunca escolher arbitrariamente entre homônimos |
| validar_carrinho | cliente_id; itens de produto_id, quantidade, unidade UN | válido, problemas, itens calculados, total ou null |
| criar_pedido_rascunho | dados do carrinho; chave_idempotencia; referencia_documento opcional | pedido persistido, snapshots e indicador de repetição |
| consultar_pedido | pedido_id UUID | rascunho com itens e totais |

## Regras para o consumidor

1. Extraia do documento apenas descrições, identificadores e quantidades.
2. Busque candidatos; EAN informado é consulta exata, nunca aproximada.
3. Resolva marca/embalagem/unidade. Sem evidência suficiente, peça esclarecimento.
4. Identifique o cliente e valide o carrinho.
5. Crie rascunho com uma chave única por operação/documento. Reuse a chave em retries.
6. Apresente o resultado para revisão humana; não trate o rascunho como venda efetivada.

Preços são strings decimais nos JSONs (`"12.90"`), moeda BRL; quantidades são inteiros
positivos até 100.000. Não enviar preços, totais, SQL ou campos extras: são rejeitados.
Não usar score como probabilidade de acerto. Uma consulta sem match pode retornar
zero candidatos, e a busca descritiva é auxílio à decisão, não identificação garantida.

## Exemplo de arguments para criação

```json
{
  "entrada": {
    "cliente_id": "UUID retornado por buscar_cliente",
    "itens": [{"produto_id": "UUID retornado por buscar_produtos", "quantidade": 2, "unidade": "UN"}],
    "chave_idempotencia": "grupo-documento-001-v1",
    "referencia_documento": "pedido-001.txt"
  }
}
```

Os UUIDs acima são placeholders; o exemplo executável está em `scripts/smoke_mcp.py`.

## Erros

Erros de argumentos são retornados pelo SDK MCP com `isError=true`. Erros de negócio
na criação/consulta também usam `isError=true`, com texto JSON contendo `codigo`,
`mensagem` e `problemas`. `validar_carrinho` retorna normalmente `valido=false`,
permitindo correção sem gravação. Erros internos não expõem SQL nem credenciais.

Códigos: `CLIENTE_INVALIDO`, `PRODUTO_INEXISTENTE`, `PRODUTO_INATIVO`,
`SEM_PRECO`, `ESTOQUE_INSUFICIENTE`, `CARRINHO_INVALIDO`,
`IDEMPOTENCIA_CONFLITO`, `PEDIDO_NAO_ENCONTRADO`, `INDICE_INDISPONIVEL`.

O recurso `pedidos://politicas` descreve regras de uso. Nenhuma ferramenta executa
SQL livre, acessa URLs, lê documentos arbitrários ou disponibiliza gabaritos.
