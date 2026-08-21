"""Routing bulk work to a cheap OpenAI-compatible provider.

Two properties matter more than the feature itself:

  1. Nothing routes anywhere unless the owner explicitly configured it. The default
     path must be untouched by this code existing.
  2. Any failure falls back to the default model rather than taking an agent down. A
     third party being unreachable should cost money, not a morning run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import providers  # noqa: E402

CFG = {
    "providers": {
        "cheap": {"base_url": "https://api.example.com/v1", "model": "big-cheap-1",
                  "api_key_env": "CHEAP_KEY"},
        "local": {"base_url": "http://localhost:11434/v1", "model": "llama"},
    },
    "models": {"tailor": "provider:cheap", "content": "claude-sonnet-4-6"},
}


class TestResolve:
    def test_a_configured_provider_resolves(self):
        r = providers.resolve("provider:cheap", CFG)
        assert r["model"] == "big-cheap-1"
        assert r["base_url"] == "https://api.example.com/v1"

    def test_ordinary_model_names_never_route(self):
        # the whole default path depends on this returning None
        for m in ("claude-sonnet-4-6", "claude-haiku-4-5-20251001", "", None, 7):
            assert providers.resolve(m, CFG) is None

    def test_an_unconfigured_name_does_not_route(self):
        assert providers.resolve("provider:nonexistent", CFG) is None

    def test_incomplete_config_is_refused(self):
        bad = {"providers": {"x": {"base_url": "https://a.example/v1"}}}   # no model
        assert providers.resolve("provider:x", bad) is None
        bad2 = {"providers": {"x": {"model": "m"}}}                        # no base_url
        assert providers.resolve("provider:x", bad2) is None

    def test_plaintext_to_a_remote_host_is_refused(self):
        bad = {"providers": {"x": {"base_url": "http://api.example.com/v1",
                                   "model": "m"}}}
        assert providers.resolve("provider:x", bad) is None

    def test_plaintext_to_localhost_is_allowed(self):
        # a local Ollama has no TLS and never leaves the machine
        assert providers.resolve("provider:local", CFG) is not None

    def test_trailing_slash_does_not_double_up(self):
        cfg = {"providers": {"x": {"base_url": "https://a.example/v1/", "model": "m"}}}
        assert providers.resolve("provider:x", cfg)["base_url"] == "https://a.example/v1"


class TestParseResponse:
    def test_normal_response(self):
        raw = json.dumps({"choices": [{"message": {"content": "hello"}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 4}})
        text, usage = providers.parse_response(raw)
        assert text == "hello"
        assert usage == {"input_tokens": 10, "output_tokens": 4}

    def test_error_payload_yields_nothing(self):
        raw = json.dumps({"error": {"message": "rate limited"}})
        assert providers.parse_response(raw) == (None, {})

    def test_malformed_shapes_yield_nothing(self):
        for raw in ("not json", "{}", "[]", json.dumps({"choices": []}),
                    json.dumps({"choices": [{}]})):
            assert providers.parse_response(raw)[0] is None

    def test_missing_usage_is_not_fatal(self):
        raw = json.dumps({"choices": [{"message": {"content": "hi"}}]})
        text, usage = providers.parse_response(raw)
        assert text == "hi" and usage == {"input_tokens": 0, "output_tokens": 0}


class TestFallback:
    def test_a_missing_key_falls_back_rather_than_raising(self, monkeypatch):
        monkeypatch.delenv("CHEAP_KEY", raising=False)
        prov = providers.resolve("provider:cheap", CFG)
        assert providers.call(prov, "hi") == (None, {})

    def test_call_never_raises_on_a_network_failure(self, monkeypatch):
        monkeypatch.setenv("CHEAP_KEY", "x")
        def boom(*a, **k):
            raise OSError("network is down")
        monkeypatch.setattr(providers.urllib.request, "urlopen", boom)
        monkeypatch.setattr(providers.time, "sleep", lambda s: None)
        assert providers.call(providers.resolve("provider:cheap", CFG), "hi") == (None, {})


class TestPlannerIntegration:
    def test_planner_falls_back_to_a_real_model_id(self):
        # after a provider failure it must NOT hand "provider:cheap" to the claude CLI
        src = (ROOT / "app" / "planner.py").read_text()
        seg = src.split("prov = providers.resolve(model)", 1)[1][:900]
        assert 'model = m.get("default") or MODEL' in seg
        assert seg.index("providers.call") < seg.index('m.get("default")')

    def test_the_default_path_is_unconditional(self):
        # provider routing must sit BEFORE the cli lookup and never replace it
        src = (ROOT / "app" / "planner.py").read_text()
        body = src.split("def _cli(", 1)[1].split("\ndef ", 1)[0]
        assert "cli = _find_claude_cli()" in body
        assert body.index("providers.resolve") < body.index("_find_claude_cli()")

    def test_provider_usage_is_metered_under_its_own_name(self):
        src = (ROOT / "app" / "planner.py").read_text()
        assert '_log_usage(feature, f"provider:{prov[\'name\']}/{prov[\'model\']}", usage)' in src

    def test_the_key_never_comes_from_config(self):
        # config.json gets pasted into bug reports; environment variables do not
        src = (ROOT / "app" / "providers.py").read_text()
        assert 'os.environ.get(prov["api_key_env"])' in src
        assert '"api_key"' not in src.split('"""', 2)[2]
