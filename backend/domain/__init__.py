"""Domain layer — the language of the system.

Pure, framework-agnostic value objects and entities. Has no knowledge of:
    - FastAPI / HTTP
    - SQLAlchemy / databases
    - filesystem paths, OS APIs, or environment

The domain layer is the contract between persistence and presentation.
Both adapt to it; it adapts to neither.
"""

from backend.domain.enums import NodeType
from backend.domain.exceptions import (
    DomainError,
    DuplicateNodeIdError,
    InvalidParentError,
    NodeNotFoundError,
    TreeCycleError,
)
from backend.domain.metadata import NodeMetadata
from backend.domain.node import Node, NodeId, new_node_id
from backend.domain.tree import Tree

__all__ = [
    "DomainError",
    "DuplicateNodeIdError",
    "InvalidParentError",
    "Node",
    "NodeId",
    "NodeMetadata",
    "NodeNotFoundError",
    "NodeType",
    "Tree",
    "TreeCycleError",
    "new_node_id",
]