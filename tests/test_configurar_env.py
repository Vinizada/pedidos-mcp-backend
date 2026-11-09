"""Gerador de .env: cria segredos e completa variáveis novas sem alterar valores."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "configurar_env", ROOT / "scripts/configurar_env.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_env_gets_missing_agent_variables_without_changing_values(tmp_path):
    module = load_script()
    env = tmp_path / ".env"
    env.write_text("API_TOKEN=segredo-existente\nLLM_MODEL=claude-opus-5-5")
    assert module.completar_env(env) == ["ANTHROPIC_API_KEY", "LLM_EFFORT"]
    content = env.read_text()
    assert "API_TOKEN=segredo-existente\nLLM_MODEL=claude-opus-5-5\n" in content
    assert "ANTHROPIC_API_KEY=\n" in content
    assert "LLM_EFFORT=medium\n" in content
    assert content.count("LLM_MODEL=") == 1
    assert module.completar_env(env) == []
    assert env.read_text() == content
