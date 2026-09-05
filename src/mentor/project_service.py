"""Project lifecycle and ownership checks for Phase 6."""

import sqlite3

from mentor.project_models import ProjectStatus, StrategyProject, ThreadContext, ThreadSourceBehavior
from mentor.storage import Storage


class ProjectConflictError(ValueError):
    pass


class ProjectService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def create_project(self, name: str) -> StrategyProject:
        try:
            return self.storage.create_project(name)
        except sqlite3.IntegrityError:
            raise ProjectConflictError("a project with that name already exists") from None

    def update_project(self, project_id: int, *, status: str) -> StrategyProject:
        return self.storage.update_project_status(project_id, ProjectStatus(status))

    def create_project_thread(self, project_id: int, title: str) -> ThreadContext:
        project = self._project(project_id)
        if project.status is ProjectStatus.ARCHIVED:
            raise ProjectConflictError("archived projects cannot create conversations")
        thread_id = self.storage.create_thread(
            title, behavior=ThreadSourceBehavior.PROJECT, project_id=project.id
        )
        return self.storage.thread_context(thread_id)

    def project_thread(self, project_id: int, thread_id: int) -> ThreadContext:
        self._project(project_id)
        thread = self.storage.thread_context(thread_id)
        if thread is None:
            raise LookupError("conversation not found")
        if thread.project_id != project_id or thread.thread_source_behavior is not ThreadSourceBehavior.PROJECT:
            raise ValueError("conversation does not belong to this project")
        return thread

    def project_summaries(self) -> list[dict[str, object]]:
        return [self._safe_summary(project) for project in self.storage.projects()]

    def project_detail(self, project_id: int) -> dict[str, object]:
        project = self._project(project_id)
        return {
            **self._safe_summary(project),
            "threads": [self._thread_json(thread) for thread in self.storage.project_threads(project.id)],
            "roadmap": self.roadmap(project.id),
        }

    def apply_state_event(
        self,
        project_id: int,
        *,
        event_key: str,
        kind: str,
        payload: dict[str, object],
        origin_thread_id: int,
        origin_turn_number: int,
    ) -> dict[str, object]:
        project = self._project(project_id)
        if project.status is ProjectStatus.ARCHIVED:
            raise ProjectConflictError("archived projects cannot change coaching state")
        applied = self.storage.apply_project_state_event(
            project_id=project_id,
            event_key=event_key,
            kind=kind,
            payload=payload,
            origin_thread_id=origin_thread_id,
            origin_turn_number=origin_turn_number,
        )
        return {"status": "applied" if applied else "already_applied", "roadmap": self.roadmap(project_id)}

    def roadmap(self, project_id: int) -> dict[str, object]:
        self._project(project_id)
        return self.storage.project_roadmap(project_id)

    def project_context(self, project_id: int) -> dict[str, object]:
        roadmap = self.roadmap(project_id)
        blockers = roadmap["blockers"]
        mastery = roadmap["mastery"]
        return {
            "objective": _bounded(roadmap["objective"], 800),
            "experiment": _bounded(roadmap["experiment"], 800),
            "blockers": [_bounded(item, 250) for item in blockers[:4]],
            "blockers_truncated": len(blockers) > 4,
            "next_action": _bounded(roadmap["next_action"], 800),
            "mastery": [
                {
                    "concept": _bounded(item["concept"], 100),
                    "status": item["status"],
                    "reason": _bounded(item["reason"], 200),
                    "evidence_reference": _bounded(item["evidence_reference"], 100),
                }
                for item in mastery[:5]
            ],
            "mastery_truncated": len(mastery) > 5,
        }

    def general_summaries(self) -> list[dict[str, object]]:
        return [self._safe_summary(project) for project in self.storage.projects()]

    def _project(self, project_id: int) -> StrategyProject:
        project = self.storage.project(project_id)
        if project is None:
            raise LookupError("project not found")
        return project

    def _safe_summary(self, project: StrategyProject) -> dict[str, object]:
        roadmap = self.storage.project_roadmap(project.id)
        blockers = roadmap["blockers"]
        return {
            "id": project.id,
            "name": project.name,
            "status": project.status.value,
            "summary": {
                "objective": roadmap["objective"],
                "experiment": roadmap["experiment"],
                "progress": None,
                "next_action": roadmap["next_action"],
                "unresolved_question": blockers[0] if blockers else None,
            },
        }

    @staticmethod
    def _thread_json(thread: ThreadContext) -> dict[str, object]:
        return {
            "id": thread.id,
            "title": thread.title,
            "project_id": thread.project_id,
            "thread_source_behavior": thread.thread_source_behavior.value,
        }


def _bounded(value: object, limit: int) -> object:
    return value[:limit] if isinstance(value, str) else value
