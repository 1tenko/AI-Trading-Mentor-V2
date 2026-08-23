"""Dependency closure for immutable source-derived knowledge records."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from mentor.derived_records import DerivedRecord, validate_record


_KINDS = frozenset({"source_revision", "derived_record"})


@dataclass(frozen=True, order=True)
class DependencyNode:
    kind: str
    identifier: str

    def __post_init__(self) -> None:
        if self.kind not in _KINDS or not isinstance(self.identifier, str) or not self.identifier:
            raise ValueError("invalid dependency node")


@dataclass(frozen=True, order=True)
class DependencyEdge:
    dependency: DependencyNode
    dependent: DependencyNode

    def __post_init__(self) -> None:
        if not isinstance(self.dependency, DependencyNode) or not isinstance(self.dependent, DependencyNode):
            raise ValueError("dependency edges require dependency nodes")
        if self.dependent.kind != "derived_record":
            raise ValueError("only derived records may consume dependencies")


class DependencyGraph:
    """A compact, directed input-to-consumer graph for one candidate snapshot."""

    def __init__(self, edges: Iterable[DependencyEdge]):
        values = tuple(sorted(set(edges)))
        if not all(isinstance(edge, DependencyEdge) for edge in values):
            raise ValueError("dependency graph requires typed edges")
        self.edges = values
        self._dependents: dict[DependencyNode, tuple[DependencyNode, ...]] = {}
        grouped: dict[DependencyNode, set[DependencyNode]] = defaultdict(set)
        for edge in values:
            grouped[edge.dependency].add(edge.dependent)
        self._dependents = {node: tuple(sorted(nodes)) for node, nodes in grouped.items()}

    @classmethod
    def from_records(cls, records: Iterable[DerivedRecord]) -> "DependencyGraph":
        record_values = tuple(records)
        record_ids = set()
        edges = []
        for record in record_values:
            validate_record(record)
            if record.record_id in record_ids:
                raise ValueError("duplicate derived record")
            record_ids.add(record.record_id)
            dependent = DependencyNode("derived_record", record.record_id)
            edges.extend(
                DependencyEdge(DependencyNode(dependency.kind, dependency.identifier), dependent)
                for dependency in record.dependencies
            )
        graph = cls(edges)
        graph.assert_acyclic()
        return graph

    def assert_acyclic(self) -> None:
        active: set[DependencyNode] = set()
        visited: set[DependencyNode] = set()

        def visit(node: DependencyNode) -> None:
            if node in active:
                raise ValueError("dependency graph contains a cycle")
            if node in visited:
                return
            active.add(node)
            for dependent in self._dependents.get(node, ()):
                visit(dependent)
            active.remove(node)
            visited.add(node)

        for node in sorted({node for edge in self.edges for node in (edge.dependency, edge.dependent)}):
            visit(node)

    def stale_record_ids(self, revision_ids: Iterable[str]) -> tuple[str, ...]:
        self.assert_acyclic()
        pending = [DependencyNode("source_revision", revision_id) for revision_id in revision_ids]
        if any(not revision.identifier for revision in pending):
            raise ValueError("revision IDs must be non-empty")
        visited = set(pending)
        stale: set[str] = set()
        while pending:
            node = pending.pop()
            for dependent in self._dependents.get(node, ()):
                if dependent not in visited:
                    visited.add(dependent)
                    pending.append(dependent)
                    stale.add(dependent.identifier)
        return tuple(sorted(stale))

    def rebuild_record_ids(self, revision_ids: Iterable[str]) -> tuple[str, ...]:
        return self.stale_record_ids(revision_ids)
