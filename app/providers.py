#!/usr/bin/env python3
"""Route individual LLM features to a cheap OpenAI-compatible provider.

WHY. Most of what this system asks a model to do is bulk work behind a code gate:
draft a cover letter, rewrite two resume blocks, classify a mail thread, summarise a
transcript. Every one of those outputs is already checked by code that does not care
which model produced it (resume_tailor.validate, ats_forms.answer_for, humanize,
answer_bank._clean_qa). That is exactly the shape of work worth sending somewhere
cheap, and the gates are what make it safe to.

WHAT THIS IS NOT. It is not a migration. The default stays the `claude` CLI and
nothing routes anywhere else unless the owner configures it, feature by feature. The
intended split is: anything a human reads keeps the strong model; bulk generation
behind a gate goes to the cheap one.

DELIBERATELY OpenAI-COMPATIBLE rather than one vendor. DeepSeek, Together, Groq,
OpenRouter and a local Ollama all speak /chat/completions, so one adapter covers them
and swapping vendors is a config edit, not a rewrite.

CONFIGURE in store/config.json:

    "providers": {
      "cheap": {
        "base_url": "https://api.example.com/v1",
        "api_key_env": "CHEAP_API_KEY",
        "model": "their-model-name"
      }
    },
    "models": {
      "tailor":  "provider:cheap",     <- bulk, gated: send it
      "content": "claude-sonnet-4-6"   <- a human reads this: keep it
    }

The key comes from the ENVIRONMENT, never from config.json, so it cannot be committed
or pasted into a bug report by accident.

WHAT LEAVES THE MACHINE. Whatever is in the prompt, to whoever runs that base_url.
For project code that is unremarkable. For a resume, employment history or job
applications it is personal data going to a new company, so route those features
deliberately or not at all.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PREFIX = "provider:"


def _config() -> dict:
    try:
        return json.loads((ROOT / "store" / "config.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def resolve(model: str, cfg: dict | None = None) -> dict | None:
    """A 'provider:<name>' model string -> that provider's settings, else None.

    Returns None for anything that is not an explicitly configured provider, which is
    what keeps the default path (the claude CLI) untouched.
    """
    if not isinstance(model, str) or not model.startswith(PREFIX):
        return None
    name = model[len(PREFIX):].strip()
    if not name:
        return None
    p = ((cfg if cfg is not None else _config()).get("providers") or {}).get(name)
    if not isinstance(p, dict):
        return None
    base, mdl = str(p.get("base_url") or "").strip(), str(p.get("model") or "").strip()
    if not base or not mdl:
        return None
    if not base.startswith("https://") and "localhost" not in base and "127.0.0.1" not in base:
        return None                      # plaintext to a remote host: refuse
    return {"name": name, "base_url": base.rstrip("/"), "model": mdl,
            "api_key_env": str(p.get("api_key_env") or "").strip(),
            "max_tokens": int(p.get("max_tokens") or 4096),
            "temperature": p.get("temperature")}


def parse_response(raw: str) -> tuple[str | None, dict]:
    """OpenAI-shaped response -> (text, usage). Pure, so it is testable without a network."""
    try:
        d = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None, {}
    if not isinstance(d, dict):
        return None, {}
    if d.get("error"):
        return None, {}
    try:
        text = d["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, {}
    u = d.get("usage") or {}
    usage = {"input_tokens": int(u.get("prompt_tokens") or 0),
             "output_tokens": int(u.get("completion_tokens") or 0)}
    return (text if isinstance(text, str) else None), usage


def call(prov: dict, prompt: str, timeout: int = 120) -> tuple[str | None, dict]:
    """One completion. Returns (None, {}) on ANY failure so the caller can fall back.

    Never raises: a third-party outage must degrade to the default model rather than
    take an agent down with it.
    """
    key = os.environ.get(prov["api_key_env"]) if prov["api_key_env"] else None
    if prov["api_key_env"] and not key:
        print(f"providers: {prov['api_key_env']} is not set; falling back",
              file=sys.stderr)
        return None, {}
    body = {"model": prov["model"], "max_tokens": prov["max_tokens"],
            "messages": [{"role": "user", "content": prompt}]}
    if prov.get("temperature") is not None:
        body["temperature"] = prov["temperature"]
    req = urllib.request.Request(
        prov["base_url"] + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {key}"} if key else {})},
        method="POST")
    for attempt in range(2):             # one retry: transient 5xx/timeouts are common
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return parse_response(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(2)
                continue
            print(f"providers: {prov['name']} HTTP {code}; falling back", file=sys.stderr)
            return None, {}
        except Exception as e:  # noqa: BLE001 -- any failure means fall back
            if attempt == 0:
                time.sleep(2)
                continue
            print(f"providers: {prov['name']} {type(e).__name__}; falling back",
                  file=sys.stderr)
            return None, {}
    return None, {}


def status() -> str:
    cfg = _config()
    provs = (cfg.get("providers") or {})
    if not provs:
        return "  No providers configured. Everything uses the claude CLI.\n"
    out = ["  Configured providers:"]
    for name, p in provs.items():
        r = resolve(f"{PREFIX}{name}", cfg)
        if not r:
            out.append(f"    {name:<12} INVALID (needs base_url + model, https)")
            continue
        env = r["api_key_env"]
        keyed = "key set" if (not env or os.environ.get(env)) else f"{env} NOT SET"
        out.append(f"    {name:<12} {r['model']}  @ {r['base_url']}  ({keyed})")
    routed = {f: m for f, m in (cfg.get("models") or {}).items()
              if isinstance(m, str) and m.startswith(PREFIX)}
    out.append("")
    if routed:
        out.append("  Features routed away from Claude:")
        for f, m in sorted(routed.items()):
            out.append(f"    {f:<14} -> {m}")
    else:
        out.append("  No feature is routed to a provider yet (models map).")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print()
    print(status())
