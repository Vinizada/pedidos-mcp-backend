"""Consolida execuções de `avaliar.py` (ex.: modelos rodados separadamente) num único resumo.

Uso: python scripts/consolidar_avaliacao.py avaliacao/<exec1> avaliacao/<exec2> \
         --saida avaliacao/consolidado
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def _avaliar():
    caminho = Path(__file__).resolve().parent / "avaliar.py"
    spec = importlib.util.spec_from_file_location("avaliar", caminho)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def consolidar(execucoes: list[Path], saida: Path) -> dict:
    avaliar = _avaliar()
    linhas, metas = [], []
    for execucao in execucoes:
        linhas += [json.loads(linha) for linha in (execucao / "resultados.jsonl").open()]
        metas.append(json.loads((execucao / "resumo.json").read_text())["meta"])
    resumo = avaliar.resumir(linhas)
    meta = {
        "execucao": " + ".join(m["execucao"] for m in metas),
        "conjunto": ", ".join(sorted({m["conjunto"] for m in metas})),
        "repeticoes": max(m["repeticoes"] for m in metas),
        "versao_prompt": ", ".join(sorted({m["versao_prompt"] for m in metas})),
        "modelos": [modelo for m in metas for modelo in m["modelos"]],
    }
    saida.mkdir(parents=True, exist_ok=True)
    with (saida / "resultados.jsonl").open("w") as arquivo:
        for linha in linhas:
            arquivo.write(json.dumps(linha, ensure_ascii=False) + "\n")
    (saida / "resumo.json").write_text(
        json.dumps(resumo | {"meta": meta}, ensure_ascii=False, indent=2)
    )
    (saida / "resumo.md").write_text(avaliar.resumo_markdown(resumo, meta))
    return resumo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("execucoes", nargs="+", type=Path)
    parser.add_argument("--saida", type=Path, default=Path("avaliacao/consolidado"))
    args = parser.parse_args()
    consolidar(args.execucoes, args.saida)
    print(f"Resumo consolidado em {args.saida / 'resumo.md'}")


if __name__ == "__main__":
    main()
