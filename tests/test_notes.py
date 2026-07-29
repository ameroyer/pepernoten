from notes import match_topics, parse_update_response

TOPIC_INDEX = {
    "streaming-video-llms": {
        "fingerprint_tags": ["streaming-video", "kv-cache", "video-llm"],
        "fingerprint_benchmarks": ["StreamingBench"],
        "min_tag_overlap": 2,
    },
    "world-models": {
        "fingerprint_tags": ["world-model", "video-generation"],
        "fingerprint_benchmarks": [],
        "min_tag_overlap": 2,
    },
}


def test_match_topics_by_tag_overlap():
    note = {"tags": ["kv-cache", "video-llm", "unrelated"], "benchmarks": []}
    assert match_topics(note, TOPIC_INDEX) == ["streaming-video-llms"]


def test_match_topics_by_benchmark():
    note = {"tags": [], "benchmarks": ["StreamingBench"]}
    assert match_topics(note, TOPIC_INDEX) == ["streaming-video-llms"]


def test_match_topics_no_match():
    note = {"tags": ["kv-cache"], "benchmarks": []}  # only 1 tag overlap, needs 2
    assert match_topics(note, TOPIC_INDEX) == []


def test_parse_update_response_with_changelog():
    raw = "## Introduction\nbody\n---CHANGELOG---\n- did a thing"
    doc, changelog = parse_update_response(raw)
    assert doc == "## Introduction\nbody"
    assert changelog == "- did a thing"


def test_parse_update_response_without_changelog():
    doc, changelog = parse_update_response("## Introduction\nbody")
    assert doc == "## Introduction\nbody"
    assert changelog == ""
