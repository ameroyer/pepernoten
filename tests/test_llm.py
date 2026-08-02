import pytest

from llm import Usage, UsageTracker, _cli_model_alias, _extract_json, _is_claude_model


def test_direct_json():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_markdown_fenced_json():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_with_leading_prose():
    assert _extract_json('Sure, here you go:\n{"a": [1, 2]}') == {"a": [1, 2]}


def test_json_array():
    assert _extract_json('[1, 2, 3]') == [1, 2, 3]


def test_empty_response_raises():
    with pytest.raises(ValueError):
        _extract_json("   ")


def test_unparseable_response_raises():
    with pytest.raises(ValueError):
        _extract_json("not json at all")


def test_is_claude_model_openrouter_style():
    assert _is_claude_model("anthropic/claude-sonnet-4-5")
    assert _is_claude_model("anthropic/claude-haiku-4.5")


def test_is_claude_model_bare_alias():
    assert _is_claude_model("sonnet")
    assert _is_claude_model("haiku")


def test_is_claude_model_false_for_other_providers():
    assert not _is_claude_model("google/gemini-2.5-flash-lite")
    assert not _is_claude_model("openai/gpt-4o")


def test_cli_model_alias_from_openrouter_slug():
    assert _cli_model_alias("anthropic/claude-sonnet-4-5") == "sonnet"
    assert _cli_model_alias("anthropic/claude-haiku-4.5") == "haiku"
    assert _cli_model_alias("anthropic/claude-opus-4") == "opus"


def test_cli_model_alias_passthrough_for_unknown():
    assert _cli_model_alias("some-custom-model") == "some-custom-model"


def test_usage_tracker_totals_tokens():
    tracker = UsageTracker()
    tracker.add(Usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.01, model="m", provider="p"))
    tracker.add(Usage(prompt_tokens=10, completion_tokens=5, cost_usd=0.002, model="m", provider="p"))
    assert tracker.total_tokens() == 165
    assert tracker.total_cost() == pytest.approx(0.012)


def test_usage_tracker_ignores_none():
    tracker = UsageTracker()
    tracker.add(None)
    assert tracker.total_tokens() == 0
    assert tracker.total_cost() is None


def test_usage_tracker_partial_cost_sums_known_entries():
    tracker = UsageTracker()
    tracker.add(Usage(prompt_tokens=10, completion_tokens=5, cost_usd=0.01, model="m", provider="p"))
    tracker.add(Usage(prompt_tokens=10, completion_tokens=5, cost_usd=None, model="m", provider="p"))
    assert tracker.total_cost() == pytest.approx(0.01)
