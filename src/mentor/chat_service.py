"""Grounded, stateless Responses API chat for the Phase 1 proof."""

from dataclasses import dataclass
from typing import Any

from mentor.prompts import MENTOR_INSTRUCTIONS
from mentor.storage import Storage


@dataclass(frozen=True)
class Citation:
    file_id: str
    filename: str


@dataclass(frozen=True)
class Evidence:
    file_id: str
    filename: str
    excerpt: str
    year: str | None


@dataclass(frozen=True)
class Answer:
    text: str
    citations: list[Citation]
    evidence: list[Evidence]


class ChatService:
    def __init__(self, storage: Storage, client: Any, model: str = "gpt-5.6-sol"):
        self.storage = storage
        self.client = client
        self.model = model

    def reply(self, thread_id: int, question: str) -> Answer:
        question = _question(question)
        vector_store_id = self.storage.vector_store_id()
        if vector_store_id is None:
            raise RuntimeError("Import the Jacob transcripts before starting a chat.")

        user_item = {"role": "user", "content": [{"type": "input_text", "text": question}]}
        response = self.client.responses.create(
            model=self.model,
            instructions=MENTOR_INSTRUCTIONS,
            input=[*self.storage.thread_items(thread_id), user_item],
            tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
            include=["reasoning.encrypted_content", "file_search_call.results"],
            reasoning={"effort": "high"},
            store=False,
        )
        output = [_as_dict(item) for item in response.output]
        self.storage.append_thread_items(thread_id, [user_item, *output])
        return _answer(output)


def _question(question: str) -> str:
    question = question.strip()
    if not question:
        raise ValueError("Question cannot be blank.")
    if len(question) > 8_000:
        raise ValueError("Question is too long.")
    return question


def _as_dict(item: Any) -> dict:
    if isinstance(item, dict):
        return item
    return item.model_dump(mode="json")


def _answer(output: list[dict]) -> Answer:
    text_parts: list[str] = []
    citations: list[Citation] = []
    evidence: list[Evidence] = []
    for item in output:
        if item.get("type") == "file_search_call":
            for result in item.get("results") or []:
                attributes = result.get("attributes") or {}
                evidence.append(
                    Evidence(
                        file_id=result["file_id"],
                        filename=result.get("filename", "Unknown source"),
                        excerpt=result.get("text", ""),
                        year=attributes.get("year"),
                    )
                )
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        for content in item.get("content") or []:
            if content.get("type") != "output_text":
                continue
            text_parts.append(content.get("text", ""))
            for annotation in content.get("annotations") or []:
                if annotation.get("type") == "file_citation":
                    citation = Citation(
                        file_id=annotation["file_id"],
                        filename=annotation.get("filename", "Unknown source"),
                    )
                    if citation not in citations:
                        citations.append(citation)
    return Answer(text="".join(text_parts), citations=citations, evidence=evidence)
