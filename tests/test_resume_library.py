"""Pre-built resume variants: the matcher, and the guarantees around it.

The matcher must never call a model (that would spend most of what the library
saves) and must never leave an application with no resume attached. Both are pinned
here, along with the fallback behaviour that makes a half-built library degrade to
today's behaviour rather than breaking an apply round.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

import resume_library as rl  # noqa: E402


def _lib(*keys):
    """A fake built library covering the given family_level keys."""
    out = []
    for k in keys:
        family, level = k.rsplit("_", 1)
        out.append({"key": k, "family": family, "level": level,
                    "keywords": list(rl.FAMILIES[family]),
                    "tagline": f"{family} tagline", "summary": f"{family} summary"})
    return out


class TestMatcher:
    def test_picks_the_family_the_title_names(self):
        lib = _lib("seo_manager", "paid_media_manager", "generalist_manager")
        got = rl.match({"title": "SEO Manager"}, lib)
        assert got["family"] == "seo"

    def test_paid_media_beats_generic_marketing(self):
        lib = _lib("generalist_manager", "paid_media_manager")
        got = rl.match({"title": "Paid Media Manager, Growth Marketing"}, lib)
        assert got["family"] == "paid_media", "specific keyword must outweigh generic"

    def test_seniority_breaks_a_family_tie(self):
        lib = _lib("seo_manager", "seo_director")
        assert rl.match({"title": "Director of SEO"}, lib)["level"] == "director"
        assert rl.match({"title": "SEO Manager"}, lib)["level"] == "manager"

    def test_unmatched_title_falls_back_rather_than_guessing(self):
        lib = _lib("generalist_manager", "seo_manager")
        got = rl.match({"title": "Underwater Basket Weaver"}, lib)
        assert got["key"] == rl.FALLBACK

    def test_empty_library_returns_nothing_instead_of_crashing(self):
        assert rl.match({"title": "SEO Manager"}, []) is None

    def test_query_text_contributes_to_the_match(self):
        lib = _lib("lifecycle_manager", "generalist_manager")
        got = rl.match({"title": "Marketing Manager",
                        "query": "lifecycle email marketing"}, lib)
        assert got["family"] == "lifecycle"

    def test_level_words_are_word_bounded(self):
        # 'senior' inside another word must not set the tier
        assert rl._level_of("Seniority Analyst") is None
        assert rl._level_of("Senior Analyst") == "senior"

    def test_matching_never_imports_a_model(self):
        src = (ROOT / "agents" / "resume_library.py").read_text()
        # the whole economic argument: choosing among N options with an LLM costs
        # about 20k tokens per application and spends most of the saving
        matcher = src.split("def score(", 1)[1].split("def build(", 1)[0]
        for banned in ("planner", "_cli", "claude"):
            assert banned not in matcher, f"matcher must stay free of {banned}"


class TestResumeSelection:
    def test_unrendered_variant_falls_back_to_the_static_resume(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rl, "ROOT", tmp_path)
        monkeypatch.setattr(rl, "OUTDIR", tmp_path / "variants")
        monkeypatch.setattr(rl, "load", lambda: _lib("seo_manager"))
        key, path = rl.resume_for({"title": "SEO Manager"})
        assert path.name == "resume.pdf", "a half-built library must degrade, not break"

    def test_rendered_variant_is_preferred(self, monkeypatch, tmp_path):
        out = tmp_path / "variants"
        out.mkdir(parents=True)
        pdf = out / "seo_manager.pdf"
        pdf.write_bytes(b"x" * 30000)          # over the 20KB sanity floor
        monkeypatch.setattr(rl, "ROOT", tmp_path)
        monkeypatch.setattr(rl, "OUTDIR", out)
        monkeypatch.setattr(rl, "load", lambda: _lib("seo_manager"))
        key, path = rl.resume_for({"title": "SEO Manager"})
        assert path == pdf and key == "seo_manager"

    def test_a_truncated_render_is_not_uploaded(self, monkeypatch, tmp_path):
        out = tmp_path / "variants"
        out.mkdir(parents=True)
        (out / "seo_manager.pdf").write_bytes(b"x" * 100)   # clearly broken
        monkeypatch.setattr(rl, "ROOT", tmp_path)
        monkeypatch.setattr(rl, "OUTDIR", out)
        monkeypatch.setattr(rl, "load", lambda: _lib("seo_manager"))
        _, path = rl.resume_for({"title": "SEO Manager"})
        assert path.name == "resume.pdf"

    def test_mode_off_always_sends_the_static_resume(self, monkeypatch, tmp_path):
        (tmp_path / "store").mkdir()
        (tmp_path / "store" / "config.json").write_text(json.dumps({"resume_mode": "off"}))
        monkeypatch.setattr(rl, "ROOT", tmp_path)
        kind, _ = rl.resume_for_mode({"title": "SEO Manager"})
        assert kind == "static"

    def test_mode_defaults_to_library_once_one_is_built(self, monkeypatch, tmp_path):
        (tmp_path / "store").mkdir()
        (tmp_path / "store" / "config.json").write_text("{}")     # no resume_mode set
        monkeypatch.setattr(rl, "ROOT", tmp_path)
        monkeypatch.setattr(rl, "OUTDIR", tmp_path / "variants")
        monkeypatch.setattr(rl, "load", lambda: _lib("seo_manager"))
        kind, _ = rl.resume_for_mode({"title": "SEO Manager"})
        assert kind in ("static", "variant:seo_manager")          # never "tailored"


class TestFamiliesCarryNoCareer:
    def test_families_are_keywords_only(self):
        # hardcoding one person's taglines would hand every other owner a stranger's
        # career; the words must come from the owner's own resume at build time
        for family, kws in rl.FAMILIES.items():
            assert isinstance(kws, tuple) and kws
            for k in kws:
                assert isinstance(k, str) and k == k.lower()

    def test_no_personal_facts_are_baked_into_the_module(self):
        full = (ROOT / "agents" / "resume_library.py").read_text().lower()
        # career FACTS must appear nowhere, docstring included: those are what would
        # end up on someone else's resume
        for leak in ("$400k", "$1m", "2.5x", "sioux falls", "chief technology officer"):
            assert leak not in full, f"a real person's career detail leaked in: {leak}"
        # a NAME is allowed in the attribution line and nowhere else. Crediting the
        # person who built the mechanism is correct; carrying their identity into the
        # executable data is not.
        body = full.split('"""', 2)[2]
        for name in ("austin", "apon", "soren", "peterson"):
            assert name not in body, f"{name} appears outside the attribution"

    def test_build_routes_through_the_anti_fabrication_gates(self):
        src = (ROOT / "agents" / "resume_library.py").read_text()
        build = src.split("def build(", 1)[1].split("def render(", 1)[0]
        # generated text is checked against the real resume's allowed facts, same as
        # the per-job LLM path; a failing variant is dropped, never patched
        assert "resume_tailor.tailor_blocks" in build
        assert "rejected" in build
