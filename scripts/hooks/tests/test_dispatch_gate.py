"""test_dispatch_gate.py — Tests for dispatch_gate.evaluate_dispatch.

Loads the shared corpus from dispatch_corpus.json and asserts verdicts.
Also includes direct unit tests for edge cases: empty prompt, off mode,
fail-open on bad input.
"""

import json
import sys
from pathlib import Path

import pytest

# Ensure the hooks directory is importable
HOOKS_DIR = Path(__file__).parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from dispatch_gate import evaluate_dispatch  # noqa: E402

CORPUS_PATH = Path(__file__).parent / "dispatch_corpus.json"


# ---------------------------------------------------------------------------
# Corpus tests — one parametrize entry per case
# ---------------------------------------------------------------------------

def _load_corpus():
    """Load and return corpus cases as (name, case) tuples."""
    with open(CORPUS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    return [(c["name"], c) for c in data["cases"]]


@pytest.mark.parametrize("name,case", _load_corpus(), ids=[c[0] for c in _load_corpus()])
def test_corpus_case(name: str, case: dict) -> None:
    """Each corpus case must produce a verdict matching its expect block."""
    prompt = case["prompt"]
    mode = case.get("mode", "warn")
    expect = case["expect"]

    verdict = evaluate_dispatch(prompt, mode=mode)

    # 1. checked
    if "checked" in expect:
        assert verdict["checked"] == expect["checked"], (
            f"[{name}] checked: got {verdict['checked']}, want {expect['checked']}"
        )

    # When checked=False, remaining assertions that depend on content don't apply.
    if expect.get("checked") is False:
        return

    # 2. compliant
    if "compliant" in expect:
        assert verdict["compliant"] == expect["compliant"], (
            f"[{name}] compliant: got {verdict['compliant']}, want {expect['compliant']}\n"
            f"  missing={verdict['missing']}\n  warnings={verdict['warnings']}"
        )

    # 3. block
    if "block" in expect:
        assert verdict["block"] == expect["block"], (
            f"[{name}] block: got {verdict['block']}, want {expect['block']}"
        )

    # 4. missing_has — substring must appear in some missing[] entry
    if "missing_has" in expect:
        needle = expect["missing_has"]
        assert any(needle in m for m in verdict["missing"]), (
            f"[{name}] missing_has '{needle}' not found in missing={verdict['missing']}"
        )

    # 5. warnings_has — substring must appear in some warnings[] entry
    if "warnings_has" in expect:
        needle = expect["warnings_has"]
        assert any(needle in w for w in verdict["warnings"]), (
            f"[{name}] warnings_has '{needle}' not found in warnings={verdict['warnings']}"
        )

    # 6. not_missing — substring must NOT appear in any missing[] entry
    if "not_missing" in expect:
        needle = expect["not_missing"]
        assert not any(needle in m for m in verdict["missing"]), (
            f"[{name}] not_missing '{needle}' found in missing={verdict['missing']}"
        )


# ---------------------------------------------------------------------------
# Direct unit tests
# ---------------------------------------------------------------------------

class TestEmptyPrompt:
    def test_empty_string_not_checked(self):
        v = evaluate_dispatch("")
        assert v["checked"] is False, f"Expected checked=False for empty prompt, got {v}"

    def test_whitespace_only_not_checked(self):
        v = evaluate_dispatch("   \n\t  ")
        assert v["checked"] is False

    def test_empty_prompt_allow(self):
        v = evaluate_dispatch("")
        assert v["compliant"] is True
        assert v["block"] is False
        assert v["missing"] == []
        assert v["warnings"] == []


class TestOffMode:
    def test_off_mode_not_checked(self):
        v = evaluate_dispatch("anything at all, no contract elements", mode="off")
        assert v["checked"] is False

    def test_off_mode_always_allow(self):
        v = evaluate_dispatch(
            "You are implementing with no termination criterion or output contract.",
            mode="off",
        )
        assert v["checked"] is False
        assert v["compliant"] is True
        assert v["block"] is False

    def test_off_mode_even_with_good_prompt(self):
        good = (
            "You are implementing bead x1. Read src/foo.py. "
            "Acceptance: tests pass. Return a summary."
        )
        v = evaluate_dispatch(good, mode="off")
        assert v["checked"] is False


class TestFailOpen:
    """The gate must never raise — any unexpected input returns an allow-verdict."""

    def test_none_prompt_does_not_raise(self):
        v = evaluate_dispatch(None)  # type: ignore[arg-type]
        assert v["checked"] is False
        assert v["compliant"] is True
        assert v["block"] is False

    def test_integer_prompt_does_not_raise(self):
        v = evaluate_dispatch(42)  # type: ignore[arg-type]
        assert v["checked"] is False

    def test_list_prompt_does_not_raise(self):
        v = evaluate_dispatch(["not", "a", "string"])  # type: ignore[arg-type]
        assert v["checked"] is False

    def test_dict_prompt_does_not_raise(self):
        v = evaluate_dispatch({"key": "value"})  # type: ignore[arg-type]
        assert v["checked"] is False


class TestVerdictShape:
    """Every verdict must have all five required keys with correct types."""

    def test_compliant_verdict_shape(self):
        prompt = (
            "You are implementing bead fblai-x1 in /Users/dev/repo (branch: main). "
            "Read scripts/open_brain.py and docs/dispatch-contract.md. "
            "Acceptance: the new pytest test passes and tsc is clean. "
            "Return a one-paragraph summary of the files changed."
        )
        v = evaluate_dispatch(prompt)
        assert isinstance(v["checked"], bool)
        assert isinstance(v["compliant"], bool)
        assert isinstance(v["missing"], list)
        assert isinstance(v["warnings"], list)
        assert isinstance(v["block"], bool)
        assert v["checked"] is True
        assert v["compliant"] is True
        assert v["block"] is False

    def test_non_compliant_verdict_shape(self):
        v = evaluate_dispatch("Add a parser to scripts/foo.py.", mode="warn")
        assert isinstance(v["checked"], bool)
        assert isinstance(v["compliant"], bool)
        assert isinstance(v["missing"], list)
        assert isinstance(v["warnings"], list)
        assert isinstance(v["block"], bool)


class TestRule1Termination:
    """Rule 1: termination criterion detection."""

    @pytest.mark.parametrize("phrase", [
        "Acceptance: tests pass.",
        "Done when the suite is green.",
        "Success criteria: no errors.",
        "Deliverable: a working module.",
        "Definition of done: CI is green.",
        "Stop when all assertions pass.",
        "When you are complete, exit.",
        "When you're done, stop.",
        "Complete when the migration runs.",
        "Expected output: a JSON summary.",
        "Expected result: 200 status.",
        "Must return a structured summary.",
        "Must produce the output file.",
        "Must deliver the artifact.",
        "Must output the JSON.",
        "Return only the summary.",
        "Return a structured report.",
        "Return the changes made.",
        "Return JSON.",
        "Return the following:",
        "Criteria: all tests green.",
        "Verify that the endpoint responds.",
    ])
    def test_termination_present(self, phrase: str):
        prompt = f"You are implementing in src/x.ts. {phrase}"
        v = evaluate_dispatch(prompt)
        assert "termination" not in " ".join(v["missing"]).lower(), (
            f"Phrase '{phrase}' should satisfy Rule 1 but missing={v['missing']}"
        )

    def test_termination_absent(self):
        prompt = "You are working in scripts/foo.py. Add a log parser and report your progress."
        v = evaluate_dispatch(prompt)
        assert any("termination" in m for m in v["missing"])

    def test_strict_blocks_on_missing_termination(self):
        prompt = "You are working in scripts/foo.py. Add a log parser and report your progress."
        v = evaluate_dispatch(prompt, mode="strict")
        assert v["block"] is True
        assert any("termination" in m for m in v["missing"])

    def test_strict_does_not_block_when_termination_present(self):
        prompt = (
            "You are implementing bead x in src/x.ts. "
            "Acceptance: the build passes. Return a summary."
        )
        v = evaluate_dispatch(prompt, mode="strict")
        assert v["block"] is False


class TestRule2RedundantPaste:
    """Rule 2: redundant content paste warnings."""

    def test_large_paste_with_path_warns(self):
        # Build a large fenced block (>1500 chars) with a path reference
        inner = ("def handler(x):\n    return x + 1\n" * 60)  # well over 1500 chars
        prompt = (
            f"Implement the change in scripts/open_brain.py. "
            f"Acceptance: tests pass. Return a summary. Here is the file:\n```python\n{inner}```"
        )
        v = evaluate_dispatch(prompt, max_embed_chars=1500)
        assert any("content" in w for w in v["warnings"]), (
            f"Expected 'content' warning for large paste + path, got warnings={v['warnings']}"
        )

    def test_large_paste_without_path_does_not_warn(self):
        inner = ("def handler(x):\n    return x + 1\n" * 60)
        prompt = (
            f"Here is the target implementation you must produce verbatim. "
            f"Acceptance: it compiles. Return the result.\n```python\n{inner}```"
        )
        v = evaluate_dispatch(prompt, max_embed_chars=1500)
        assert not any("content" in w for w in v["warnings"]), (
            f"Should NOT warn when no path reference, got warnings={v['warnings']}"
        )

    def test_small_paste_with_path_does_not_warn(self):
        prompt = (
            "Implement the change in scripts/open_brain.py. "
            "Acceptance: tests pass. Return a summary.\n```python\ndef f(): pass\n```"
        )
        # 3 words of inner content — well under 1500 chars
        v = evaluate_dispatch(prompt, max_embed_chars=1500)
        assert not any("content" in w for w in v["warnings"]), (
            f"Small block should not warn, got warnings={v['warnings']}"
        )

    def test_short_prompt_skips_rule2(self):
        # A short prompt (< min_prompt_tokens estimated) should skip Rule 2
        inner = ("x" * 2000)  # big fenced block
        short_prompt = f"scripts/foo.py\n```\n{inner}\n```"
        # With min_prompt_tokens=150 and len(short_prompt)//4 > 150... use a tiny prompt
        tiny = "scripts/foo.py\n```\nshort\n```"
        # Force min_prompt_tokens high enough to skip
        v = evaluate_dispatch(tiny, min_prompt_tokens=10000)
        assert not any("content" in w for w in v["warnings"])


class TestRule3OutputContract:
    """Rule 3: output contract warnings."""

    @pytest.mark.parametrize("phrase", [
        "Return a one-paragraph summary.",
        "Report the files changed.",
        "Output the test result.",
        "Summary: include all changed files.",
        "Respond with the analysis.",
        "Provide a structured report.",
        "Provide the result.",
        "Hand back the JSON.",
        "Deliver a summary.",
        "Deliverable: a working module.",
        "Produce a structured output.",
        "Produce the artifact.",
    ])
    def test_output_contract_present(self, phrase: str):
        prompt = f"You are implementing in src/x.ts. Acceptance: tests pass. {phrase}"
        v = evaluate_dispatch(prompt)
        assert not any("output" in w for w in v["warnings"]), (
            f"Phrase '{phrase}' should satisfy Rule 3 but warnings={v['warnings']}"
        )

    def test_output_contract_absent(self):
        prompt = (
            "You are implementing in src/x.ts (branch: main). "
            "Read src/tools.ts for the pattern. "
            "Acceptance: the build passes and the new vitest case is green. "
            "Stop when the suite is fully green."
        )
        v = evaluate_dispatch(prompt)
        assert any("output" in w for w in v["warnings"]), (
            f"Expected output-contract warning, got warnings={v['warnings']}"
        )

    def test_output_contract_absent_warn_mode_does_not_block(self):
        prompt = (
            "You are implementing in src/x.ts. "
            "Acceptance: the build passes."
        )
        v = evaluate_dispatch(prompt, mode="warn")
        assert v["block"] is False


class TestStrictModeSemantics:
    """Strict mode blocks only on missing Rule 1; warnings alone do not block."""

    def test_strict_with_only_missing_output_contract_does_not_block(self):
        prompt = (
            "You are implementing in src/x.ts (branch: main). "
            "Read src/tools.ts. "
            "Acceptance: the build passes."
            # No output contract phrase
        )
        v = evaluate_dispatch(prompt, mode="strict")
        assert v["block"] is False, (
            f"Strict should not block on warnings alone; got block={v['block']}, "
            f"missing={v['missing']}, warnings={v['warnings']}"
        )

    def test_strict_blocks_only_when_termination_missing(self):
        prompt = (
            "You are working in scripts/foo.py. Add a log parser."
            # No termination, no output contract
        )
        v = evaluate_dispatch(prompt, mode="strict")
        assert v["block"] is True
        assert any("termination" in m for m in v["missing"])
