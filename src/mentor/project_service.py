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
        }

    def _project(self, project_id: int) -> StrategyProject:
        project = self.storage.project(project_id)
        if project is None:
            raise LookupError("project not found")
        return project

    @staticmethod
    def _safe_summary(project: StrategyProject) -> dict[str, object]:
        return {
            "id": project.id,
            "name": project.name,
            "status": project.status.value,
            "summary": {
                "objective": None,
                "experiment": None,
                "progress": None,
                "next_action": None,
                "unresolved_question": None,
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
