"""Gera 20 documentos fictícios e gabaritos isolados para uso pelo grupo.

Uso: python scripts/gerar_exemplos.py [--output-dir data]
Requer o pacote instalado e reportlab. Não chama LLM nem avalia modelos.
Os arquivos JSON em gabaritos/ NÃO devem ser fornecidos ao agente avaliado.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape

from pedidos.fixtures import catalog_records, client_records, synthetic_ean

DOCUMENT_TITLE = "Solicitação de orçamento"


def example_cases() -> list[dict]:
    """Casos ligados aos SKUs estáveis do seed, incluindo falhas esperadas."""
    products = {p["sku"]: p for p in catalog_records()}
    clients = {c["codigo"]: c for c in client_records()}

    def item(number: int, quantity: int) -> dict:
        product = products[f"MER-{number:03d}"]
        return {"sku": product["sku"], "ean": product["ean"], "quantidade": quantity}

    def ean(number: int) -> str:
        return products[f"MER-{number:03d}"]["ean"]

    cases = [
        {
            "titulo": "Pedido por EAN",
            "conteudo": f"EAN {ean(1)}: 10 unidades\nEAN {ean(3)}: 5 unidades",
            "esperado": "criar_rascunho",
            "itens": [item(1, 10), item(3, 5)],
            "notas": "Identificação exata por EAN; quantidade em unidades da embalagem cadastrada.",
        },
        {
            "titulo": "Reposição por código interno",
            "conteudo": "Favor separar MER-004, 4 pacotes, e MER-007, 6 pacotes.",
            "esperado": "criar_rascunho",
            "itens": [item(4, 4), item(7, 6)],
            "notas": "O agente deve localizar os SKUs no catálogo antes de criar os itens.",
        },
        {
            "titulo": "Solicitação em linguagem natural",
            "conteudo": (
                "Precisamos de 8 pacotes do café moído tradicional Aurora de 500 g "
                "e 3 pacotes de açúcar cristal Aurora de 1 kg."
            ),
            "esperado": "criar_rascunho",
            "itens": [item(1, 8), item(3, 3)],
            "notas": "Marca, produto e tamanho permitem desambiguação.",
        },
        {
            "titulo": "Lista abreviada",
            "conteudo": "6 un - det. neutro Casa Serena 500 ml\n2 un - sabão pó Casa Serena 800 g",
            "esperado": "criar_rascunho",
            "itens": [item(91, 6), item(94, 2)],
            "notas": "Recuperar descrições abreviadas com categoria e marca.",
        },
        {
            "titulo": "Caixa fechada com código explícito",
            "conteudo": (
                f"2 unidades do produto EAN {ean(33)} "
                "(cada unidade é uma caixa fechada de 12 caixinhas de leite integral)."
            ),
            "esperado": "criar_rascunho",
            "itens": [item(33, 2)],
            "notas": (
                "Quantidade 2 do SKU de caixa fechada; "
                "não converter para 24 unidades do SKU avulso."
            ),
        },
        {
            "titulo": "Embalagens distintas",
            "conteudo": "Arroz branco tipo 1 Campo Claro: 2 pacotes de 5 kg e 7 pacotes de 1 kg.",
            "esperado": "criar_rascunho",
            "itens": [item(4, 2), item(5, 7)],
            "notas": "As duas apresentações correspondem a produtos diferentes.",
        },
        {
            "titulo": "Sinônimos no pedido",
            "conteudo": (
                "Por favor, 5 pacotes da bolacha maisena Forno Bom de 350 g "
                "e 3 pacotes da massa parafuso Mesa Feliz de meio quilo."
            ),
            "esperado": "criar_rascunho",
            "itens": [item(52, 5), item(17, 3)],
            "notas": "Bolacha/biscoito e massa/macarrão são sinônimos presentes no índice textual.",
        },
        {
            "titulo": "Orçamento com observações",
            "conteudo": (
                "Bom dia! Solicito orçamento para o próximo encontro.\n"
                f"Quantidade: 12 | EAN: {ean(71)}\n"
                f"Quantidade: 4 | EAN: {ean(98)}\n"
                "Entregar somente após nosso aceite. Obrigado!"
            ),
            "esperado": "criar_rascunho",
            "itens": [item(71, 12), item(98, 4)],
            "notas": (
                "A observação não autoriza confirmação ou baixa de estoque; preparar rascunho."
            ),
        },
        {
            "titulo": "Itens com descrições completas",
            "conteudo": (
                "3 garrafas de suco integral de uva Pomar Novo de 1 L; "
                "2 potes de geleia de morango Doce Manhã de 250 g; "
                "4 pacotes de torrada tradicional Forno Bom de 160 g."
            ),
            "esperado": "criar_rascunho",
            "itens": [item(74, 3), item(60, 2), item(55, 4)],
            "notas": "Carrinho de três itens; nenhum item adicional deve ser incluído.",
        },
        {
            "titulo": "Itens numerados",
            "conteudo": (
                f"1) Produto solicitado: EAN {ean(47)}, quantidade 2 unidades.\n"
                f"2) Outro item: EAN {ean(49)}, quantidade 1 unidade."
            ),
            "esperado": "criar_rascunho",
            "itens": [item(47, 2), item(49, 1)],
            "notas": (
                "Os números da lista não são quantidades; "
                "identificar os produtos pelo EAN explícito."
            ),
        },
        {
            "titulo": "Marca não especificada",
            "conteudo": "Quero 10 pacotes de café moído de meio quilo. Pode preparar o pedido?",
            "esperado": "pedir_esclarecimento",
            "itens": [],
            "candidatos": ["MER-001", "MER-002"],
            "notas": "Há pelo menos duas marcas compatíveis; não escolher arbitrariamente.",
        },
        {
            "titulo": "Código inexistente no catálogo",
            "conteudo": f"Solicito 3 unidades do EAN {synthetic_ean(999999)}.",
            "esperado": "rejeitar",
            "itens": [],
            "notas": (
                "Checksum válido não implica existência no catálogo. "
                "Informar produto não encontrado e não criar pedido."
            ),
        },
        {
            "titulo": "Produto sem estoque",
            "conteudo": f"Favor separar 2 caixas do filtro de papel Aurora 103, EAN {ean(99)}.",
            "esperado": "rejeitar",
            "itens": [],
            "notas": "MER-099 tem estoque zero no seed; não substituir por outro produto.",
        },
        {
            "titulo": "Produto inativo",
            "conteudo": (
                "Preciso de 3 unidades da mistura para bolo Forno Bom de baunilha 400 g, "
                f"EAN {ean(100)}."
            ),
            "esperado": "rejeitar",
            "itens": [],
            "notas": "MER-100 está inativo; a identificação exata não permite vendê-lo.",
        },
        {
            "titulo": "Quantidade zero",
            "conteudo": f"Produto {ean(3)}; quantidade: 0 unidades.",
            "esperado": "rejeitar",
            "itens": [],
            "notas": (
                "Quantidade deve ser inteira e estritamente positiva; não corrigir silenciosamente."
            ),
        },
        {
            "titulo": "Quantidade negativa",
            "conteudo": f"Pedido novo: EAN {ean(8)}; quantidade -2 unidades.",
            "esperado": "rejeitar",
            "itens": [],
            "notas": "Pedido novo não representa devolução; quantidades negativas são inválidas.",
        },
        {
            "titulo": "Fração de embalagem indivisível",
            "conteudo": f"Enviar 1,5 unidades do EAN {ean(31)} (leite integral de 1 L).",
            "esperado": "pedir_esclarecimento",
            "itens": [],
            "notas": (
                "UN representa embalagem inteira. Não arredondar 1,5 para 1 ou 2; "
                "validação do backend deve rejeitar frações."
            ),
        },
        {
            "titulo": "Caixa sem definição de embalagem",
            "conteudo": "Enviar 2 caixas de leite integral Vale Branco de 1 litro.",
            "esperado": "pedir_esclarecimento",
            "itens": [],
            "candidatos": ["MER-031", "MER-033"],
            "notas": "Confirmar se são 2 caixinhas de 1 L ou 2 caixas fechadas com 12 caixinhas.",
        },
        {
            "titulo": "Cliente não cadastrado",
            "cliente_codigo": "CLI-999",
            "cliente_nome": "Mercado Demonstrativo Horizonte",
            "conteudo": f"Sou o cliente CLI-999. Preciso de 4 unidades do EAN {ean(16)}.",
            "esperado": "pedir_esclarecimento",
            "itens": [],
            "notas": (
                "Não inventar ID de cliente nem cadastrar automaticamente. "
                "Solicitar identificação/cadastro à pessoa responsável."
            ),
        },
        {
            "titulo": "Quantidade ausente",
            "conteudo": (
                f"Gostaria de pedir o açúcar cristal Aurora de 1 kg, EAN {ean(3)}. Aguardo retorno."
            ),
            "esperado": "pedir_esclarecimento",
            "itens": [],
            "notas": "Quantidade não informada; não assumir uma unidade.",
        },
    ]
    for i, case in enumerate(cases, 1):
        customer_code = case.setdefault("cliente_codigo", f"CLI-{((i - 1) % 15) + 1:03d}")
        case.setdefault(
            "cliente_nome", clients.get(customer_code, {}).get("nome", "Cliente não cadastrado")
        )
        case["id"] = f"pedido-{i:03d}"
        case["formato"] = "pdf" if i % 2 else "txt"
        case["data"] = "2026-11-07"
    return cases


def document_text(case: dict) -> str:
    return (
        f"DOCUMENTO FICTÍCIO PARA TESTES\n{DOCUMENT_TITLE}\n"
        f"Referência: {case['id']} | Data: {case['data']}\n"
        f"Cliente: {case['cliente_codigo']} - {case['cliente_nome']}\n\n"
        f"{case['conteudo']}\n"
    )


def write_pdf(path: Path, case: dict) -> None:
    # Importação tardia: catálogo e casos podem ser consultados sem reportlab.
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    def invariant_canvas(*args, **kwargs):
        return Canvas(*args, **(kwargs | {"invariant": 1}))

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=22 * mm,
        leftMargin=22 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=DOCUMENT_TITLE,
        author="Grupo IA Generativa - dados fictícios",
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "PedidoTexto", fontName="Helvetica", fontSize=11, leading=17, alignment=TA_LEFT
        )
    )
    styles.add(
        ParagraphStyle(
            "Aviso",
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#54647a"),
        )
    )
    elements = [
        Paragraph("DOCUMENTO FICTÍCIO PARA TESTES", styles["Aviso"]),
        Spacer(1, 6 * mm),
        Paragraph(DOCUMENT_TITLE, styles["Title"]),
        Spacer(1, 4 * mm),
    ]
    header = Table(
        [
            ["Referência", case["id"], "Data", case["data"]],
            [
                "Cliente",
                Paragraph(
                    escape(f"{case['cliente_codigo']} - {case['cliente_nome']}"), styles["BodyText"]
                ),
                "",
                "",
            ],
        ],
        colWidths=[24 * mm, 78 * mm, 16 * mm, 40 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("SPAN", (1, 1), (3, 1)),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef3f8")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1dae4")),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.extend([header, Spacer(1, 10 * mm)])
    for line in case["conteudo"].splitlines():
        elements.extend([Paragraph(escape(line), styles["PedidoTexto"]), Spacer(1, 3 * mm)])
    elements.extend(
        [
            Spacer(1, 15 * mm),
            Paragraph(
                "Cenário acadêmico. Clientes, pedidos e códigos foram criados para demonstração. "
                "Nenhuma compra real será efetuada.",
                styles["Aviso"],
            ),
        ]
    )
    doc.build(elements, canvasmaker=invariant_canvas)


def generate(output_dir: Path) -> dict:
    """Regenera somente os arquivos de nomes conhecidos; não apaga a pasta."""
    documents = output_dir / "pedidos"
    answers = output_dir / "gabaritos"
    documents.mkdir(parents=True, exist_ok=True)
    answers.mkdir(parents=True, exist_ok=True)
    cases = example_cases()
    manifest = {
        "versao": 1,
        "aviso": (
            "Dados fictícios. EANs com checksum válido, sem alegação de registro GS1. "
            "Gabaritos exclusivos para avaliação; não enviar ao agente."
        ),
        "catalogo": {
            "produtos": 100,
            "clientes": 15,
            "sem_estoque": "MER-099",
            "inativo": "MER-100",
        },
        "limites": (
            "PDFs usam um único layout, com texto selecionável; "
            "não incluem OCR, documentos escaneados "
            "nem avaliação automática de modelos."
        ),
        "casos": [],
    }
    for case in cases:
        filename = f"{case['id']}.{case['formato']}"
        path = documents / filename
        if case["formato"] == "pdf":
            write_pdf(path, case)
        else:
            path.write_text(document_text(case), encoding="utf-8")
        answer = {
            key: value for key, value in case.items() if key not in ("conteudo", "cliente_nome")
        }
        answer["documento"] = f"pedidos/{filename}"
        (answers / f"{case['id']}.json").write_text(
            json.dumps(answer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        manifest["casos"].append(
            {
                "id": case["id"],
                "documento": f"pedidos/{filename}",
                "gabarito": f"gabaritos/{case['id']}.json",
            }
        )
    (answers / "manifesto.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    manifest = generate(args.output_dir)
    print(
        f"Gerados {len(manifest['casos'])} documentos (10 PDF e 10 TXT) "
        f"em {args.output_dir / 'pedidos'}."
    )
    print(
        f"Gabaritos separados em {args.output_dir / 'gabaritos'}. Não fornecer ao agente avaliado."
    )


if __name__ == "__main__":
    main()
