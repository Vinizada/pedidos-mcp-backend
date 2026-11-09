"""Gera versões escaneadas (sem camada de texto) dos 10 PDFs e 3 fotos, para avaliar o
caminho multimodal sem OCR.

Uso: python scripts/gerar_escaneados.py [--data-dir data] [--seed 7]
Requer dependências de desenvolvimento (pypdfium2, pillow). Determinístico pela semente.
Os gabaritos são os mesmos dos originais; o manifesto fica em
gabaritos/manifesto-escaneados.json e não deve ser fornecido ao agente.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from io import BytesIO
from pathlib import Path

DPI = 150
DATA_FIXA = time.strptime("2026-11-08", "%Y-%m-%d")  # Pillow espera struct_time
FOTOS = ("pedido-003", "pedido-007", "pedido-009")


def _renderizar(pdf: Path):
    import pypdfium2 as pdfium

    documento = pdfium.PdfDocument(str(pdf))
    try:
        return documento[0].render(scale=DPI / 72).to_pil().convert("L")
    finally:
        documento.close()


def _degradar(imagem, rng: random.Random, angulo: float, qualidade: int):
    """Rotação leve, ruído, desfoque e compressão JPEG, como num scanner de escritório."""
    from PIL import Image, ImageFilter

    imagem = imagem.rotate(angulo, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    ruido = Image.effect_noise(imagem.size, 24).point(lambda v: 255 if v > 245 else 0)
    imagem = Image.blend(imagem, Image.eval(ruido, lambda v: 255 - v), 0.04)
    imagem = imagem.filter(ImageFilter.GaussianBlur(radius=0.6))
    buffer = BytesIO()
    imagem.save(buffer, format="JPEG", quality=qualidade)
    return Image.open(BytesIO(buffer.getvalue())).convert("L")


def _foto(imagem, rng: random.Random):
    """Foto de celular: fundo escuro, inclinação maior, iluminação desigual."""
    from PIL import Image, ImageDraw, ImageOps

    papel = imagem.rotate(rng.uniform(2.5, 4.0), expand=True, fillcolor=90)
    fundo = Image.new("L", (papel.width + 120, papel.height + 120), 70)
    fundo.paste(papel, (60, 60))
    sombra = Image.new("L", fundo.size, 0)
    ImageDraw.Draw(sombra).rectangle((0, 0, fundo.width // 3, fundo.height), fill=40)
    fundo = Image.composite(Image.new("L", fundo.size, 0), fundo, sombra.point(lambda v: v // 2))
    return ImageOps.autocontrast(fundo.resize((fundo.width * 3 // 4, fundo.height * 3 // 4)))


def gerar(data_dir: Path, seed: int = 7) -> dict:
    rng = random.Random(seed)
    manifesto_original = json.loads((data_dir / "gabaritos" / "manifesto.json").read_text())
    destino = data_dir / "pedidos" / "escaneados"
    destino.mkdir(parents=True, exist_ok=True)
    casos = []
    for caso in manifesto_original["casos"]:
        if not caso["documento"].endswith(".pdf"):
            continue
        origem = data_dir / caso["documento"]
        imagem = _renderizar(origem)
        escaneado = _degradar(imagem, rng, rng.uniform(-1.5, 1.5), rng.randint(45, 65))
        caminho = destino / f"{caso['id']}-scan.pdf"
        # Datas fixas nos metadados: o mesmo seed gera exatamente os mesmos bytes.
        escaneado.save(caminho, "PDF", resolution=DPI, creationDate=DATA_FIXA, modDate=DATA_FIXA)
        casos.append(
            {
                "id": f"{caso['id']}-scan",
                "documento": f"pedidos/escaneados/{caminho.name}",
                "gabarito": caso["gabarito"],
                "formato": "pdf-escaneado",
            }
        )
        if caso["id"] in FOTOS:
            foto = destino / f"{caso['id']}-foto.png"
            _foto(imagem, rng).save(foto, "PNG", optimize=True)
            casos.append(
                {
                    "id": f"{caso['id']}-foto",
                    "documento": f"pedidos/escaneados/{foto.name}",
                    "gabarito": caso["gabarito"],
                    "formato": "png",
                }
            )
    manifesto = {
        "versao": 1,
        "conjunto": "escaneado",
        "aviso": (
            "Derivados dos PDFs originais, sem camada de texto. Mesmos gabaritos dos "
            "originais; não fornecer ao agente avaliado."
        ),
        "casos": casos,
    }
    (data_dir / "gabaritos" / "manifesto-escaneados.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n"
    )
    return manifesto


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    manifesto = gerar(args.data_dir, args.seed)
    destino = args.data_dir / "pedidos" / "escaneados"
    print(f"{len(manifesto['casos'])} documentos escaneados em {destino}.")


if __name__ == "__main__":
    main()
