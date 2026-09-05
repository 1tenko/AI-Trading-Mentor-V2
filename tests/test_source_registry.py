from mentor.source_registry import discover_text_sources, discover_transcripts


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
    assert transcripts[0].modified_at == (tmp_path / "2025" / "lesson.txt").stat().st_mtime


def test_generic_text_discovery_preserves_declared_root_without_guessing_authority(tmp_path):
    (tmp_path / "Lessons").mkdir()
    source = tmp_path / "Lessons" / "one.txt"
    source.write_text("synthetic", encoding="utf-8")

    discovered = discover_text_sources(tmp_path)

    assert [(item.relative_path, item.relative_category, item.filename) for item in discovered] == [
        ("Lessons/one.txt", "Lessons", "one.txt")
    ]
