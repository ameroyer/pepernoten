from notes import extract_my_notes, match_topics, parse_sectioned_response, parse_update_response

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


def test_parse_sectioned_response_basic():
    raw = "===TLDR===\nOne liner.\n===PROBLEM===\nPara one.\n\nPara two.\n===GAPS===\n- gap a\n- gap b"
    sections = parse_sectioned_response(raw)
    assert sections["tldr"] == "One liner."
    assert sections["problem"] == "Para one.\n\nPara two."
    assert sections["gaps"] == "- gap a\n- gap b"


def test_parse_sectioned_response_empty_on_no_markers():
    assert parse_sectioned_response("just plain text, no markers") == {}


def test_parse_sectioned_response_last_section_to_end():
    raw = "===A===\nfirst\n===B===\nlast section\nwith two lines"
    sections = parse_sectioned_response(raw)
    assert sections["b"] == "last section\nwith two lines"


def test_extract_my_notes_empty_placeholder():
    text = "## My Notes\n\n> [!note] Thoughts\n>\n\n<div>hr</div>\n"
    assert extract_my_notes(text) == ""


def test_extract_my_notes_with_content():
    text = (
        "## My Notes\n\n"
        "> [!note] Thoughts\n"
        "> This connects to project X.\n"
        "> Re-read section 3.\n\n"
        "<div>hr</div>\n"
    )
    assert extract_my_notes(text) == "This connects to project X.\nRe-read section 3."


def test_extract_my_notes_absent():
    assert extract_my_notes("## Some Note\n\nNo thoughts callout here.") == ""
