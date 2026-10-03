"""App ASGI com LLM determinístico — só para testar o validador sem chave Gemini.

    uv run python scripts/validate.py --app tests.fake_app:app
"""

from aurora.api import create_app
from aurora.service import AssistantService
from tests.fake_llm import FakeLlm

app = create_app(AssistantService(FakeLlm()))
