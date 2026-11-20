"""The provider catalogue (contract C11): the packaged file, who covers what, the sentences."""

from __future__ import annotations

from pathlib import Path

import pytest

from nanomuse.config import LLMSettings, check_provider, resolve_provider
from nanomuse.llm import catalogue
from nanomuse.llm.factory import create_llm


def test_the_packaged_file_is_read_once_and_has_the_four_local_providers():
    cat = catalogue.load()
    assert cat.source.endswith("providers.json")
    assert [p.id for p in cat.providers] == ["ollama", "lm-studio", "vllm", "custom"]
    assert cat.get("ollama").capabilities == ["chat"]
    assert cat.get("custom").user_capabilities is True
    assert cat.get("nowhere") is None
    # the same object on every call
    assert catalogue.load() is cat


def test_a_cloud_id_from_before_is_unknown_now():
    """The catalogue keeps local servers and the custom row only; a config that still
    names a cloud provider fails to load, loudly, instead of silently pointing elsewhere."""
    for provider in ("bailian", "deepseek", "moonshot", "openai", "openrouter", "gemini"):
        assert catalogue.load().get(provider) is None
    for provider in ("bailian", "deepseek", "moonshot", "openrouter", "gemini"):
        with pytest.raises(ValueError, match="unknown provider"):
            check_provider(provider)
    # "openai" is a protocol name, not a catalogue id: it still passes on its own
    assert check_provider("openai") == "openai"


def test_ollama_uses_openai_chat_with_no_key():
    cat = catalogue.load()
    provider = cat.get("ollama")
    assert provider is not None
    assert provider.auth == ["none"]
    assert provider.key_url == "https://ollama.com/download"
    assert provider.capabilities == ["chat"]
    assert provider.defaults == {"chat": "qwen3:8b"}
    assert check_provider("ollama") == "ollama"
    assert resolve_provider("ollama", None) == (
        "openai",
        "http://127.0.0.1:11434/v1",
    )
    settings = LLMSettings(provider="ollama")
    assert settings.protocol == "openai"
    assert settings.endpoint == provider.base_url
    assert settings.model == "qwen3:8b"
    assert LLMSettings(provider="ollama", model="qwen3:32b").model == "qwen3:32b"


def test_custom_brings_no_endpoint_of_its_own():
    cat = catalogue.load()
    provider = cat.get("custom")
    assert provider is not None
    assert provider.base_url == ""
    assert provider.auth == ["key", "none"]
    assert check_provider("custom") == "custom"
    assert resolve_provider("custom", None) == ("openai", None)
    settings = LLMSettings(provider="custom")
    assert settings.protocol == "openai"
    assert settings.endpoint is None
    assert settings.model == ""
    # and building a client from it fails loudly: without an address the SDK would
    # silently point at api.openai.com instead
    with pytest.raises(ValueError, match="needs a base_url"):
        create_llm(settings)
    assert create_llm(LLMSettings(provider="custom", base_url="http://127.0.0.1:9/v1"))


def test_providers_for_a_capability_names_no_cloud_provider():
    cat = catalogue.load()
    # local servers and the custom row never stand in for a provider, and no cloud
    # provider is listed at all: every capability's list is empty
    assert cat.providers_for("chat") == []
    assert cat.providers_for("image", "cn") == []
    assert cat.providers_for("video", "global") == []


def test_the_unavailable_sentence_in_english_and_chinese():
    cat = catalogue.load()
    assert cat.unavailable_sentence("image", "global", "en") == (
        "Pictures need a provider with image models; none is available (how: docs/own-key.md)."
    )
    assert cat.unavailable_sentence("image", "cn", "zh") == (
        "生成图片需要有图像模型的服务商，目前没有可用的（方法见 docs/own-key.md）。"
    )
    assert cat.unavailable_sentence("video", "global", "en") == (
        "Clips need a provider with video models; none is available (how: docs/own-key.md)."
    )
    # zh-CN, zh_TW and the like are Chinese
    assert cat.unavailable_sentence("vision", "", "zh-CN").startswith("手需要")


def test_by_base_url_finds_the_entry_for_a_host_or_nothing():
    cat = catalogue.load()
    assert cat.by_base_url("http://127.0.0.1:11434/v1").id == "ollama"
    assert cat.by_base_url("http://127.0.0.1:1234/v1").id == "lm-studio"
    assert cat.by_base_url("http://127.0.0.1:8000/v1").id == "vllm"
    assert cat.by_base_url("https://gateway.example.net/v1") is None
    # a cloud endpoint is no longer listed, and neither is a self-hosted one
    assert cat.by_base_url("https://api.deepseek.com/v1") is None
    assert cat.by_base_url("http://10.1.1.52:8000/v1") is None
    assert cat.by_base_url("") is None
    # entries on one host are told apart by the whole address, the host is only the fallback
    assert cat.by_base_url("http://127.0.0.1:11434").id == "ollama"
    assert cat.by_base_url("http://127.0.0.1:11434/v1/").id == "ollama"


def test_the_chatgpt_sign_in_maps_to_the_chatgpt_provider_with_chat_and_vision(tmp_path: Path):
    cat = catalogue.load()
    assert cat.capabilities([catalogue.CHATGPT_PROVIDER]) == {"chat", "vision"}
    # the config accepts it as a provider, with no endpoint of its own and Codex's model
    assert check_provider("chatgpt") == "chatgpt"
    assert resolve_provider("chatgpt", None) == ("chatgpt", None)
    settings = LLMSettings(provider="chatgpt")
    assert settings.base_url is None and settings.model == ""
    llm = create_llm(settings, data_dir=tmp_path)
    # tool_mode "auto" wraps the client in the prompt-tools adapter, as for every provider
    inner = getattr(llm, "inner", llm)
    assert type(inner).__name__ == "CodexLLM"
    assert inner.model == "gpt-5.6-sol"


def test_a_catalogue_id_as_provider_brings_its_protocol_and_endpoint():
    assert check_provider("ollama") == "ollama"
    assert resolve_provider("ollama", None) == (
        "openai",
        "http://127.0.0.1:11434/v1",
    )
    settings = LLMSettings(provider="ollama")
    assert settings.protocol == "openai"
    assert settings.endpoint == "http://127.0.0.1:11434/v1"
    assert settings.model == catalogue.load().get("ollama").defaults["chat"]


def test_a_missing_file_falls_back_to_the_built_in_table(tmp_path: Path):
    cat = catalogue.load_file(tmp_path / "nothing.json")
    assert cat.source == "built-in"
    assert [p.id for p in cat.providers] == ["ollama", "lm-studio", "vllm", "custom"]
    assert cat.get("bailian") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert catalogue.load_file(broken).source == "built-in"
