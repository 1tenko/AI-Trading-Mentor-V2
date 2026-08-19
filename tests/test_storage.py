from mentor.storage import Storage


def test_storage_registers_a_source_once(tmp_path):
    storage = Storage(tmp_path / "mentor.sqlite3")
    storage.initialize()
    storage.set_vector_store("vs_jacob")

    storage.register_source(
        relative_path="2025/lesson.txt",
        filename="lesson.txt",
        year=2025,
        local_path="C:/transcripts/2025/lesson.txt",
        file_id="file_jacob",
        vector_store_file_id="vsf_jacob",
    )
    storage.register_source(
        relative_path="2025/lesson.txt",
        filename="lesson.txt",
        year=2025,
        local_path="C:/transcripts/2025/lesson.txt",
        file_id="file_jacob",
        vector_store_file_id="vsf_jacob",
    )

    assert storage.vector_store_id() == "vs_jacob"
    assert storage.source_count() == 1
