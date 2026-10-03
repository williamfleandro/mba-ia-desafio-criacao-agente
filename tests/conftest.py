from __future__ import annotations

import warnings

import httpx
import pytest

from aurora import db
from aurora.api import create_app
from aurora.service import AssistantService
from tests.fake_llm import FakeLlm

warnings.filterwarnings("ignore", category=UserWarning)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_DATA_DIR", str(tmp_path))
    db.restore_seed_data()
    return tmp_path


@pytest.fixture
def make_client(data_dir):
    """Cria um cliente HTTP sobre uma nova instância da API (simula restart)."""
    services: list[AssistantService] = []

    def _make() -> httpx.AsyncClient:
        service = AssistantService(FakeLlm())
        services.append(service)
        app = create_app(service)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    yield _make
