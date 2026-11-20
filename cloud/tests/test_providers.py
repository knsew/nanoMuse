"""The provider catalogue (relay copy): it matches the runtime's, and it lists local
endpoints only. The own-key guidance that used to order cloud providers by region
went away with the allowance gate."""

from __future__ import annotations

from pathlib import Path

from nanomuse_cloud import providers as pv

ROOT = Path(__file__).resolve().parents[2]


def test_catalogue_copy_is_the_runtimes():
    """`node scripts/providers-json.mjs` writes the relay's copy; it must be byte-identical
    to the source of truth (CI runs `--check`, this test says the same from Python)."""
    source = ROOT / "nanomuse" / "llm" / "providers.json"
    if not source.exists():  # the relay installed on its own, without the monorepo
        return
    assert pv.CATALOGUE.read_bytes() == source.read_bytes()


def test_catalogue_shape():
    cat = pv.catalogue()
    ids = [p["id"] for p in cat["providers"]]
    assert ids == ["ollama", "lm-studio", "vllm", "custom"]
    for p in cat["providers"]:
        assert "chat" in p["capabilities"]
        assert set(p["capabilities"]) <= set(pv.CAPABILITIES)
        assert set(p["regions"]) <= {"cn", "global"}
        assert p["protocol"] in ("openai", "openai-responses", "anthropic", "gemini")
        assert p["verified"] and p["name"] and p["name_zh"]
        for lane, model in p.get("defaults", {}).items():
            need = "vision" if lane == "hands" else lane
            assert need in p["capabilities"], (p["id"], lane)
            assert isinstance(model, str) and model
    # no cloud provider, no plan, no session header survives the slimming
    assert pv.provider("bailian") is None
    assert pv.provider("openai") is None
    assert pv.provider("openrouter") is None
    assert all("session_header" not in p for p in cat["providers"])
    # the local servers are chat until the model says more
    for pid in ("ollama", "lm-studio", "vllm"):
        assert pv.provider(pid)["capabilities"] == ["chat"]
