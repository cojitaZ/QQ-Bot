from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openai.types.chat import ChatCompletionMessageParam

from src.AIService import AIConfigurationError, AIService
from src.Api import Api
from src.Bot import check_config_files


def _make_service(api_key: str = "test-secret") -> AIService:
    service = AIService(
        "configs/ai.toml.template",
        "utils/persona.j2",
        Api("localhost"),
    )
    provider_name = service._config["profile"]["default"]["provider"]
    service._config["provider"][provider_name]["api_key"] = api_key
    return service


class _FakeCompletions:
    def __init__(self, captured: dict):
        self.captured = captured

    async def create(self, **kwargs):
        self.captured["request"] = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="configured reply", tool_calls=None)
                )
            ]
        )


class _FakeAsyncOpenAI:
    def __init__(self, captured: dict, **kwargs):
        captured.update(kwargs)
        self.chat = SimpleNamespace(completions=_FakeCompletions(captured))


def test_generate_rejects_unknown_profile():
    service = _make_service()

    with pytest.raises(AIConfigurationError, match="unknown"):
        service._get_profile("unknown")


@pytest.mark.asyncio
async def test_generate_rejects_missing_api_key():
    service = _make_service(api_key="")

    messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": "hello"}]

    with pytest.raises(AIConfigurationError, match="api_key"):
        await service.generate("default", messages)


def test_check_config_files_creates_ai_configuration_from_template():
    with (
        patch("src.Bot.os.path.isfile", return_value=False),
        patch("src.Bot.copyfile") as copyfile,
        patch("src.Bot.Log.warning"),
    ):
        check_config_files("configs")

    copied_destinations = [call.args[1] for call in copyfile.call_args_list]
    assert any(destination.endswith("ai.toml") for destination in copied_destinations)
