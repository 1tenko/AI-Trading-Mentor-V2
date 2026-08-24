"""Run Theo's private Phase 1 mentor."""

import os
from pathlib import Path

from openai import OpenAI

from mentor.chat_service import ChatService
from mentor.config import load_config
from mentor.server import create_server
from mentor.storage import Storage


def main() -> None:
    config = load_config(os.environ, Path(".env"))
    storage = Storage(Path("data") / "mentor.sqlite3", runtime_scope=config.runtime_scope)
    storage.initialize()
    server = create_server(storage, ChatService(storage, OpenAI(api_key=config.api_key), config.model))
    print("Open http://127.0.0.1:8765 in your browser. Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
