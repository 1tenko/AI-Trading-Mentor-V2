from mentor.source_registry import discover_transcripts


def test_discover_transcripts_preserves_paths_and_assigns_years(tmp_path):
    (tmp_path / "2025").mkdir()
    (tmp_path / "2025" / "lesson.txt").write_text("old lesson", encoding="utf-8")
    (tmp_path / "May").mkdir()
    (tmp_path / "May" / "lesson.txt").write_text("new lesson", encoding="utf-8")
    (tmp_path / "May" / "ignore.md").write_text("ignore", encoding="utf-8")

    transcripts = discover_transcripts(tmp_path)

    assert [(item.relative_path, item.year) for item in transcripts] == [
        ("2025/lesson.txt", 2025),
        ("May/lesson.txt", 2026),
    ]
