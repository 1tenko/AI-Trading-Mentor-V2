from types import SimpleNamespace

from mentor.import_jacob import import_transcripts
from mentor.storage import Storage


class FakeOpenAI:
    def __init__(self):
        self.uploads: list[str] = []
        self.files = SimpleNamespace(create=self.create_file)
        self.vector_stores = SimpleNamespace(
            create=self.create_vector_store,
            files=SimpleNamespace(create=self.attach_file, retrieve=self.retrieve_file),
        )

    def create_file(self, *, file, purpose):
        self.uploads.append(file.name)
        return SimpleNamespace(id=f"file_{len(self.uploads)}")

    def create_vector_store(self, *, name, metadata):
        assert name == "Jacob Speculates 2025-2026"
        assert metadata == {"source": "jacob"}
        return SimpleNamespace(id="vs_jacob")

    def attach_file(self, vector_store_id, *, file_id, attributes):
        assert vector_store_id == "vs_jacob"
        assert attributes["source"] == "jacob"
        return SimpleNamespace(id=f"vsf_{file_id}")

    def retrieve_file(self, file_id, *, vector_store_id):
        return SimpleNamespace(status="completed")


def test_import_uploads_each_transcript_once_and_records_remote_ids(tmp_path):
    transcripts = tmp_path / "transcripts"
    (transcripts / "2025").mkdir(parents=True)
    (transcripts / "2025" / "old.txt").write_text("old", encoding="utf-8")
    (transcripts / "May").mkdir()
    (transcripts / "May" / "new.txt").write_text("new", encoding="utf-8")
    storage = Storage(tmp_path / "data" / "mentor.sqlite3")
    storage.initialize()
    client = FakeOpenAI()

    first = import_transcripts(transcripts, storage, client, sleep=lambda _: None)
    second = import_transcripts(transcripts, storage, client, sleep=lambda _: None)

    assert first.uploaded_count == 2
    assert first.skipped_count == 0
    assert second.uploaded_count == 0
    assert second.skipped_count == 2
    assert storage.vector_store_id() == "vs_jacob"
    assert storage.source_count() == 2
    assert storage.source_for_file("file_1").modified_at == (transcripts / "2025" / "old.txt").stat().st_mtime
    assert [path.rsplit("\\", 1)[-1] for path in client.uploads] == ["old.txt", "new.txt"]
