import importlib.util
import json
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "scripts/configurar_codespaces.py"
spec = importlib.util.spec_from_file_location("codespaces_setup", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_remote_host_added_without_overwriting_secret(tmp_path):
    env_path = tmp_path / ".env"
    original = 'API_TOKEN=segredo-de-teste\nALLOWED_HOSTS=["localhost"]\nAPP_PORT=8000\n'
    env_path.write_text(original)
    environment = {
        "CODESPACE_NAME": "demo-name",
        "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
    }
    host = module.configure(tmp_path, environment)
    first = env_path.read_text()
    module.configure(tmp_path, environment)
    assert env_path.read_text() == first
    assert "API_TOKEN=segredo-de-teste\n" in first
    values = dict(line.split("=", 1) for line in first.splitlines())
    assert json.loads(values["ALLOWED_HOSTS"]) == ["localhost", host]
    assert f"https://{host}" in json.loads(values["ALLOWED_ORIGINS"])
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_local_configuration_is_not_modified(tmp_path):
    assert module.configure(tmp_path, {}) is None
    assert not (tmp_path / ".env").exists()


def test_invalid_remote_hostname_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        module.configure(
            tmp_path,
            {
                "CODESPACE_NAME": "bad\nname",
                "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
            },
        )
