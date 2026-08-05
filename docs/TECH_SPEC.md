# PerfectTaskTracker — Technical Specification

> **Status:** Phase 0 — Architecture (Living Document)
> **Last Updated:** 2026-08-05

---

## 1. Project Vision

### Purpose
PerfectTaskTracker is a workspace for organizing, connecting, and
visualizing tasks and ideas. The product treats every piece of work — a
story, a task, a note, a canvas — as a first-class node that can be
nested, linked, and explored either structurally (folders) or visually
(graph).

### Philosophy
- **Files are forever.** The user's data lives in plain folders and
  Markdown files on disk. No proprietary format. No lock-in. The app can
  be uninstalled and the data is still useful.
- **Generic over specific.** Rather than model "stories," "tasks," "tags,"
  "notes" as separate schemas, everything is a **Node** with a type. This
  keeps the system flexible and the code small.
- **Index, don't replace.** The filesystem is the source of truth.
  PostgreSQL is an *index* that speeds up queries; it can be rebuilt from
  disk at any time.
- **Simple to reason about.** A single in-memory tree mirrors what is on
  disk. The UI walks the tree; nothing hidden in a foreign ORM model.

### Design Goals
1. The user owns their data on their own filesystem.
2. Both keyboard-driven power use and casual browse-and-click use are
   supported.
3. Reads are instant (in-memory tree). Writes are durable (filesystem) and
   eventually consistent with the index (Postgres).
4. The graph view reveals structure the folder view hides, without ever
   contradicting it.

---

## 2. Glossary

| Term              | Definition                                                                                  |
|-------------------|---------------------------------------------------------------------------------------------|
| Workspace         | A root folder on disk that the app is pointed at. Contains Stories and configuration.       |
| Story             | A top-level grouping Node. Roughly a "project" or "chapter."                                |
| Node              | The generic atomic entity. Everything in the system is a Node.                               |
| Canvas            | A Markdown document attached to a Node for unstructured notes.                              |
| Folder View       | A traditional hierarchical tree for navigation by parent/child relationships.               |
| Graph View        | A force-directed visual layout of Nodes and their links, derived from the tree.             |
| Sidecar           | A `.node.json` file living next to a Node's folder, holding its structured metadata.         |
| Source of Truth   | The storage tier whose loss would mean data loss. Here: the filesystem.                      |
| Index             | A storage tier that accelerates queries but can be regenerated from the source of truth.    |
| Memory Tree       | The runtime in-process object graph mirroring the workspace on disk.                         |
| Service Layer     | The backend layer that owns all business logic and mutations.                               |
| API Layer         | The backend layer that handles HTTP concerns only; contains no business logic.              |
| Cross-link        | A reference from one Node's metadata to another Node's UUID. Not a parent relationship.     |
| Slug              | A filesystem-safe lowercase-hyphenated form of a Node's title.                              |

---

## 3. Core Concepts

| Concept      | What it is                                                              |
|--------------|-------------------------------------------------------------------------|
| Workspace    | The root. A folder on disk that the app is pointed at.                  |
| Story        | A top-level grouping node. Roughly a "project" or "chapter."            |
| Node         | The generic atomic unit. A task, a note, a sub-story — all are Nodes.   |
| Canvas       | A free-form Markdown document attached to a Node for unstructured notes.|
| Folder View  | A traditional tree of folders/files for navigation by hierarchy.        |
| Graph View   | A force-directed visual layout of Nodes and their links.                |

### Everything is a Node

There is exactly one entity in the data model: the **Node**.

A Node can be:
- a Story (parent of other Nodes)
- a Task (leaf, has a status)
- a Note (leaf, free text)
- (future) anything else we want to add

This is a deliberate choice. Instead of `Story`, `Task`, `Tag`, `Note`,
`Comment` tables, we have one `Node` type with a `type` field and a
flexible `metadata` field. The cost of this generality is small — most
differences between "kinds" of node live in metadata, not in code paths.

---

## 4. Architecture Principles

These are the broader philosophical commitments that shaped the design.
Where invariants are *rules* that must not be violated, principles are
*postures* that explain *why* the rules exist.

1. **User owns the data.** No data should be stored in a way that the
   user cannot read, copy, or back up with ordinary tools.
2. **One model, one path.** Every entity is a Node, and every operation
   on a Node goes through one service-layer code path. We resist
   type-specific shortcuts.
3. **Compute what you can; store only what you must.** The tree, the
   graph, the search index are all projections. The only stored truth
   is the filesystem.
4. **Indexes are expendable.** PostgreSQL can be wiped tomorrow without
   data loss. The system is built around that assumption.
5. **Failure isolation.** A corrupted sidecar must not corrupt the
   whole workspace. Each Node's metadata is independent.
6. **Optimize for read latency in the UI.** The in-memory tree exists
   specifically so the UI never has to wait on disk or DB for
   navigation.
7. **Boring technology where possible.** FastAPI, SQLAlchemy, vanilla
   Markdown. Avoid framework magic that obscures the data flow.
8. **Refactor before scale, not after.** The first version does not need
   to support a million Nodes. It needs to be readable.
9. **Domain first, adapters second.** The system speaks a single
   language — the domain layer. API, persistence, and search adapt to
   it; the domain adapts to nothing. This is what makes each tier
   replaceable.

---

## 5. Hybrid Architecture Rationale

Three architectures were considered. We chose the hybrid.

### Option A — Database-only

All Nodes live in PostgreSQL. No filesystem representation beyond an
export feature.

| Pros                       | Cons                                                    |
|----------------------------|---------------------------------------------------------|
| Fast SQL queries          | Data is not portable; users depend on the app + DB.     |
| Atomic transactions       | Conflicts when users edit files externally.             |
| Easy indexing             | Recovery requires a DB dump.                            |

**Rejected because:** violates our commitment that the user owns the data
on disk in a portable form.

### Option B — Filesystem-only

All Nodes live as folders and Markdown. No database at all.

| Pros                       | Cons                                                    |
|----------------------------|---------------------------------------------------------|
| Maximum portability       | Search across thousands of files is slow.               |
| Trivial backup             | Cross-cutting queries ("all tasks tagged X") are awkward. |
| Human-readable            | Aggregations (graph stats) require full scans.          |

**Rejected because:** the UI's responsiveness depends on a search index
and a runtime tree, both of which a filesystem alone cannot provide
efficiently.

### Option C — Hybrid (chosen)

Filesystem is the source of truth. The in-memory tree and Postgres are
indexes / projections.

| Pros                       | Cons                                                    |
|----------------------------|---------------------------------------------------------|
| Portable + fast + queryable| Two storage tiers to keep in sync (eventually consistent). |
| Indexes are rebuildable    | Slight startup cost to rebuild in-memory tree.          |
| Clean layering             | Disciplined write ordering required.                    |

**Why this gives us portability, performance, and maintainability:**

- **Portability** comes from the filesystem tier.
- **Performance** comes from the in-memory tree (UI traversal) and
  PostgreSQL (search/aggregations).
- **Maintainability** comes from the layering: each tier has a single
  responsibility and a well-defined contract with the service layer.

---

## 6. Domain Layer

The domain layer is the **language of the system**. It contains the
pure value objects and entities that every other layer is responsible
for translating into and out of: HTTP requests, SQL rows, JSON on disk,
graph edges.

### What lives in the domain

- `NodeId` — typed wrapper around a UUID string.
- `NodeType` — closed enum (`story` | `task` | `note`).
- `NodeMetadata` — validated, type-aware freeform dict.
- `Node` — the universal entity (see §11 for fields).
- `Tree` — in-memory collection of Nodes with structural invariants.
- Domain exceptions (`NodeNotFoundError`, `DuplicateNodeIdError`,
  `InvalidParentError`, `TreeCycleError`).

### What the domain must NOT know

- **No FastAPI / HTTP.** Routes are an adapter, not a domain concept.
- **No SQLAlchemy / databases.** Persistence is an adapter.
- **No filesystem paths or OS APIs.** The disk layout is an adapter
  concern.
- **No Pydantic in the core.** Schemas are API-layer types. Domain
  validation uses plain Python `dataclass` + `__post_init__`.

A test in `backend/tests/test_domain_purity.py` enforces these
boundaries; it fails the build if any forbidden import appears in the
domain package.

### Why this layer exists

Without it, three different layers would invent their own notion of
what a Node is — the API layer would have a Pydantic model, the DB
layer would have an ORM model, the filesystem layer would have a
dict-of-dicts. Drift between those three definitions is the most
common source of bugs in CRUD apps.

The domain layer is the single definition. Every other layer is an
adapter:

```
       ┌──────────────────────┐
       │        API           │   ← Pydantic schemas validate input,
       │ (FastAPI, schemas)   │     domain types own the rules.
       └──────────┬───────────┘
                  ▼
       ┌──────────────────────┐
       │      Services        │   ← orchestration, mutation sequencing.
       │ (TreeService, etc.)  │     No I/O.
       └──────────┬───────────┘
                  ▼
       ┌──────────────────────┐
       │       Domain         │   ← the language. Pure. Framework-free.
       │ (Node, Tree, …)      │
       └──────────┬───────────┘
                  ▼
       ┌──────────────────────┐
       │     Persistence      │   ← adapters (filesystem, Postgres).
       │  (FS, SQLAlchemy)    │     Translate domain ↔ storage.
       └──────────────────────┘
```

### Invariants enforced by the domain

- A Node cannot be its own parent.
- A Node's `title` cannot be empty.
- A Tree cannot have duplicate `NodeId`s.
- A Tree cannot have cycles (a Node cannot be reparented under one of
  its own descendants).
- A Node's metadata cannot contain `status` unless it is a `task`, and
  the status must be one of the allowed values.

Persistence layers may impose additional rules (e.g. UUID format
checks), but the structural and semantic rules above are the
domain's responsibility.

---

## 7. Filesystem Layer

The filesystem layer is the *only* persistence tier that talks to disk.
It sits below the service layer and above the OS, and exposes a small
interface that the rest of the codebase depends on (no module outside
this layer touches paths).

### Responsibilities

- **Validate and own the workspace root.** `WorkspaceRoot.open(path)`
  refuses paths that are missing, unreadable, unwritable, or do not
  contain a `.ptt/` workspace marker.
- **Translate domain ↔ disk.** Every read returns a `Node` (or a
  canvas string); every write accepts a `Node` (or canvas content).
  No method returns a raw dict.
- **Enforce the on-disk layout.** Each Node directory contains exactly
  `node.json`, `canvas.md`, and zero or more child directories.
- **Atomic writes.** All writes to `node.json` and `canvas.md` go
  through `atomic_write_*` (temp file in the same directory +
  `Path.replace`). A crash before the rename leaves the original file
  intact; a crash after leaves the new file in place. No partial JSON
  is ever observable.
- **Slug generation and de-duplication.** Directory names are derived
  from titles; collisions with existing siblings are auto-resolved
  by appending `-2`, `-3`, … (per TECH_SPEC §10 [Filesystem Layout]).
  Two Nodes can share
  a title as long as their directories differ.
- **JSON in one place.** All `node.json` read/write goes through
  `serialization.py`. The schema is documented inline there; no other
  module calls `json.load` / `json.dump` on a Node payload.
- **No global state.** Every `LocalFilesystem` instance is bound to a
  `WorkspaceRoot`. The service layer passes it explicitly through
  the request lifecycle.
- **No framework dependencies.** This layer imports only stdlib,
  `pathlib`, and the domain package. It does not import FastAPI,
  SQLAlchemy, Pydantic, or Alembic.

### Atomic Write Strategy

For every metadata or canvas write:

1. Compute the target path.
2. Open a sibling `*.tmp` file in the same directory.
3. Write content + fsync by virtue of `fh.write` then context exit.
4. `tmp.replace(path)` — atomic on POSIX; equivalent on Windows.
5. On any exception during steps 2–4, attempt to unlink the temp
   file so it doesn't linger.

The temp file is *next to* the target so the rename never crosses a
filesystem boundary (which would break atomicity).

### Serialization Format

`node.json` is human-editable JSON, indented with 2 spaces, with a
trailing newline. Field set:

```
{
  "id":          "<uuid-string>",
  "type":        "story" | "task" | "note",
  "title":       "<non-empty string>",
  "parent_id":   "<uuid-string>" | null,
  "children_ids": ["<uuid-string>", ...],   # ordered
  "metadata":    { ... type-specific freeform ... }
}
```

`canvas.md` is plain CommonMark/GFF Markdown. It is **not** stored in
`node.json` — content and structure are decoupled so canvas edits
don't touch metadata files (and vice versa).

### Corruption Handling Philosophy

The filesystem is the **last line of defense** for structural
correctness, not the first. It validates what it can observe:

- Missing `node.json` → directory is treated as anonymous; `load_node`
  by id returns `NodeNotFoundOnDiskError`. The directory is still on
  disk; an external tool can recover it.
- Invalid JSON in `node.json` → same as above. The walker skips the
  directory. The user can open the file in any editor to repair.
- Duplicate ids across directories → `walk()` raises
  `DuplicateNodeIdError` with both paths. Other operations on either
  Node continue to work (id lookup is by directory walk).
- Missing `canvas.md` on an existing Node → `read_canvas` raises
  `CanvasMissingError`. The Node itself loads fine.

**Recovery is always external.** The filesystem does not auto-heal;
that is the service layer's responsibility (a future phase). For
Phase 1.2, errors are surfaced; repair is manual.

---

## 8. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                            Frontend (SPA)                            │
│   ┌───────────────────────┐          ┌───────────────────────┐       │
│   │     Folder View       │          │      Graph View       │       │
│   └───────────────────────┘          └───────────────────────┘       │
│              │                                  │                     │
│              └──────────────┬───────────────────┘                     │
│                             ▼                                         │
│                  ┌──────────────────────┐                             │
│                  │   REST API Client    │                             │
│                  └──────────────────────┘                             │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ HTTP / JSON
┌──────────────────────────────▼───────────────────────────────────────┐
│                     FastAPI Backend (Python)                         │
│                                                                      │
│   ┌──────────────────────────────────────────────────────────────┐   │
│   │                      API Layer (Routes)                      │   │
│   │   • Request validation  • Serialization  • HTTP concerns     │   │
│   └──────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│   ┌──────────────────────────▼───────────────────────────────┐       │
│   │                   Service Layer                           │      │
│   │  • TreeService    • NodeService    • GraphService          │     │
│   │  • All business logic and mutations live here.             │     │
│   └────────────────────────────────────────────────────────────┘     │
│           │                  │                   │                   │
│           ▼                  ▼                   ▼                   │
│   ┌──────────────┐   ┌───────────────┐   ┌──────────────────┐         │
│   │  Filesystem  │   │  Memory Tree  │   │  PostgreSQL      │         │
│   │  (Source of  │◄──┤  (Runtime     ├──►│  (Index / Search)│         │
│   │   Truth)     │   │   Snapshot)   │   │                  │         │
│   └──────────────┘   └───────────────┘   └──────────────────┘         │
└──────────────────────────────────────────────────────────────────────┘
```

### Layers

1. **Frontend** — Single-page app. Folder view + graph view. Talks to the
   backend over REST. Never touches the filesystem directly.
2. **FastAPI Backend** — Four internal layers:
   - **API Layer (Routes)** — HTTP handlers, request validation,
     serialization. No business logic.
   - **Service Layer** — Business logic: tree ops, node ops, graph build,
     mutation orchestration. All writes flow through here.
   - **Repository Layer** — Translates between persistence and domain
     objects. Owns tree reconstruction from disk. The *only* component
     that calls `Filesystem` directly. Defined as a Protocol; see §18.
   - **Storage adapters** — Filesystem, in-memory tree, Postgres. Each
     has one job.
3. **Filesystem** — Source of truth. Markdown + JSON sidecars.
4. **In-Memory Tree** — Runtime representation. Rebuilt on startup.
5. **PostgreSQL** — Search and query index. Can be rebuilt from disk.

### Read-path sequence (loading a Node by id)

```
   Service                  Repository                Filesystem              Disk
   ───────                  ──────────                ──────────              ────
     │                          │                         │                   │
     │ load_node(id)            │                         │                   │
     │ ───────────────────────► │                         │                   │
     │                          │ load_node(id)           │                   │
     │                          │ ──────────────────────► │                   │
     │                          │                         │ read node.json    │
     │                          │                         │ ────────────────► │
     │                          │                         │ ◄──────────────── │
     │                          │ ◄─────────────────────  │                   │
     │ ◄─────────────────────── │                         │                   │
     │                          │                         │                   │
```

### Write-path sequence (creating a Node)

```
   Service                  Repository                Filesystem              Disk
   ───────                  ──────────                ──────────              ────
     │                          │                         │                   │
     │ save_node(node, parent)  │                         │                   │
     │ ───────────────────────► │                         │                   │
     │                          │ create_node(node, pid)  │                   │
     │                          │ ──────────────────────► │                   │
     │                          │                         │ mkdir parent/...  │
     │                          │                         │ ────────────────► │
     │                          │                         │ write node.json   │
     │                          │                         │ ────────────────► │
     │                          │                         │ write canvas.md   │
     │                          │                         │ ────────────────► │
     │                          │                         │ update parent.json│
     │                          │                         │ ────────────────► │
     │                          │ ◄─────────────────────  │                   │
     │ ◄─────────────────────── │                         │                   │
     │                          │                         │                   │
```

### Reconstruction-path sequence (boot-time)

```
   Service                  Repository                Filesystem              Disk
   ───────                  ──────────                ──────────              ────
     │                          │                         │                   │
     │ load_tree()              │                         │                   │
     │ ───────────────────────► │                         │                   │
     │                          │ walk() (recursive       │                   │
     │                          │   via _walk_resilient)  │                   │
     │                          │ ──────────────────────► │                   │
     │                          │                         │ read every node.   │
     │                          │                         │ json recursively   │
     │                          │                         │ ────────────────► │
     │                          │                         │ ◄──────────────── │
     │                          │ ◄─────────────────────  │                   │
     │                          │ Tree.add(n) for each    │                   │
     │                          │ Tree.replace_children() │                   │
     │ ◄─────────────────────── │                         │                   │
     │                          │                         │                   │
```

The reconstruction path is where the Repository earns its keep:
it absorbs every Filesystem-level concern (path walking, corrupt
directories, ordering) and returns a clean `Tree` to the Service.
Services never know what an on-disk directory looks like.

### Service Layer in detail

The Service layer is the API's only collaborator above the domain.
It coordinates use cases — `create_story`, `move_node`,
`load_workspace_tree`, etc. — and never knows how persistence
works. The architectural contract:

| Property                      | Rule                                                                       |
|-------------------------------|----------------------------------------------------------------------------|
| Depends on                    | `backend.domain`, `backend.repositories.protocol`, stdlib                   |
| MUST NOT import               | `backend.filesystem`, `sqlalchemy`, `fastapi`, `pathlib`, any I/O library   |
| Persistence orchestration     | At most **one** repository call per use case (ADR-0006)                    |
| Return type                   | Domain objects only — no dicts, no DTOs, no API models                      |
| Injection                     | Constructor injection against the `WorkspaceRepository` Protocol           |
| Exception surface             | ServiceError subclasses + `ValueError` for programmer errors              |

The dependency graph is a strict chain:

```
   API routes
       │
       ▼
   WorkspaceService        ← constructor(repo: WorkspaceRepository)
       │
       ▼
   WorkspaceRepository (Protocol)        ← single seam between use cases & persistence
       │
       ▼
   Filesystem (Protocol)
       │
       ▼
   LocalFilesystem / future adapters
```

Each arrow is a Protocol boundary — every layer can be replaced
or faked in tests. There is no arrow from the Service layer back
down through the Filesystem layer (services cannot reach around
the repository) and no arrow from the API layer past the Service
layer (routes cannot orchestrate use cases themselves).

#### Request lifecycle: API → Service → Repository → Filesystem

```
   FastAPI route          WorkspaceService        WorkspaceRepository       Filesystem
   ───────────            ────────────────        ──────────────────       ──────────
        │                       │                        │                    │
        │ service.create_       │                        │                    │
        │   child(parent, t)    │                        │                    │
        │ ────────────────────► │                        │                    │
        │                       │ validate title         │                    │
        │                       │ build Node             │                    │
        │                       │ repo.save_node(...)    │                    │
        │                       │ ─────────────────────► │                    │
        │                       │                        │ create_node(...)   │
        │                       │                        │ ──────────────────►│
        │                       │                        │                    │ mkdir / write
        │                       │                        │                    │ node.json /
        │                       │                        │                    │ canvas.md
        │                       │                        │ ◄─────────────────│
        │                       │ ◄──────────────────── │                    │
        │ ◄──────────────────── │                        │                    │
        │                       │                        │                    │
```

The route's only responsibility is HTTP plumbing: parse the
request, call the service, render the response. Any validation
beyond request shape is the service's job. Any persistence is
the repository's job. The Filesystem never appears in the route's
type hints.

#### Service-level exception hierarchy

The service layer translates domain and repository exceptions
into a stable surface for the API:

| Service exception              | Raised when                                       |
|--------------------------------|---------------------------------------------------|
| `ServiceError` (base)          | All service-layer errors                          |
| `WorkspaceEmptyServiceError`   | A use case requires Stories but there are none    |
| `NodeNotFoundServiceError`     | The requested Node id doesn't exist              |
| `StoryNotFoundServiceError`    | The requested id isn't a root Story              |
| `ParentNotFoundServiceError`   | A create/move referenced a non-existent parent   |
| `CycleInMoveServiceError`      | A move would create a cycle                      |
| `InvalidRenameServiceError`    | A rename was rejected by validation              |

The mapping rule: domain exceptions (`NodeNotFoundError`,
`InvalidParentError`, `TreeCycleError`) never escape the service
layer. The API layer can `except ServiceError` and never sees a
domain class.

---

## 8a. API Layer (Read-only, Phase 1.5)

The API is the outermost layer. It is intentionally a *thin adapter*
between HTTP and the Service layer: parse the request, call the
service, render the response. No business logic, no direct
persistence access, no domain mutation.

### Architectural contract

| Property                      | Rule                                                                       |
|-------------------------------|----------------------------------------------------------------------------|
| Depends on                    | FastAPI, services, schemas, mappers, dependencies                          |
| MUST NOT import               | `backend.filesystem`, `backend.repositories.impl`, concrete classes         |
| One place may bind concrete   | `backend.api.dependencies` is the *only* module that imports `LocalWorkspaceRepository` |
| Endpoint length               | A few lines — parse → call service → map response                          |
| Exception translation         | API layer owns the ServiceError → HTTPException mapping                    |
| Return type                   | Pydantic schemas (DTOs), never domain entities                             |
| Per-request scope             | FastAPI `Depends(...)` — services are constructed per request              |

### Why DTOs are separate from Domain entities

Domain entities are Python dataclasses optimized for internal
correctness (slots, immutability, structural invariants). Pydantic
DTOs are optimized for the wire (validation, JSON serialization,
OpenAPI generation). The two have different evolutionary pressures:

- A domain change (new field, renamed invariant) should not
  require a coordinated frontend deploy.
- An API change (new optional field, deprecated endpoint, format
  change for backwards compatibility) should not pollute the
  domain.

A mapper function lives in `backend.api.mappers` for every entity
the API exposes. It's the one place the wire format is decided.

### Endpoints (Phase 1.5)

| Method | Path                              | Returns            | Purpose                          |
|--------|-----------------------------------|--------------------|----------------------------------|
| GET    | `/api/v1/health`                  | `HealthResponse`   | Liveness only                    |
| GET    | `/api/v1/workspace`               | `WorkspaceTreeResponse` | Full workspace tree          |
| GET    | `/api/v1/stories/{story_id}`      | `NodeResponse`     | Single root Story               |
| GET    | `/api/v1/nodes/{node_id}`         | `NodeResponse`     | Single Node (any type)          |
| GET    | `/api/v1/nodes/{node_id}/children`| `list[NodeResponse]` | Ordered children              |
| GET    | `/api/v1/nodes/{node_id}/canvas`  | `CanvasResponse`   | Canvas content                  |

Write endpoints (POST/PATCH/DELETE) land in Phase 1.6.

### Exception → HTTP status mapping

| Service exception              | HTTP status | Body code             |
|--------------------------------|-------------|-----------------------|
| `NodeNotFoundServiceError`     | 404         | `node_not_found`      |
| `StoryNotFoundServiceError`    | 404         | `story_not_found`     |
| `WorkspaceEmptyServiceError`   | 404         | `workspace_empty`     |
| `ParentNotFoundServiceError`   | 404         | `parent_not_found`    |
| `CycleInMoveServiceError`      | 409         | `cycle_in_move`       |
| `ServiceError` (any other)     | 500         | `service_error`       |

The body is a uniform JSON object: `{"code": str, "message": str, ...}`. The
frontend can switch on `code` without parsing free-form messages.

### Request lifecycle (read path)

```
   HTTP Request       FastAPI Route       WorkspaceService       WorkspaceRepository
   ───────────        ──────────────      ────────────────       ──────────────────
        │                  │                     │                       │
        │ GET /workspace   │                     │                       │
        │ ───────────────► │                     │                       │
        │                  │ service =           │                       │
        │                  │   Depends(...)      │                       │
        │                  │ load_workspace_tree │                       │
        │                  │ ──────────────────► │                       │
        │                  │                     │ load_tree             │
        │                  │                     │ ────────────────────► │
        │                  │                     │ ◄──────────────────── │
        │                  │ ◄────────────────── │                       │
        │                  │ mapper.tree_to_dto  │                       │
        │                  │ return response     │                       │
        │ ◄────────────────│                     │                       │
        │ 200 + JSON       │                     │                       │
```

The route is three lines. Persistence is invisible. The service is
HTTP-agnostic. The repository never sees a request scope.

### DI seam: `backend.api.dependencies`

This module is the **only** API-layer module that imports concrete
classes (`LocalFilesystem`, `LocalWorkspaceRepository`). It binds
the Protocol to the implementation. Tests override
`get_workspace_service` with `app.dependency_overrides[...]` to
inject a fake — every API integration test does exactly that.

A structural test (`backend/tests/test_api_isolation.py`) walks
`backend/api/` and asserts that no module other than
`dependencies.py` references any concrete class. The seam is
enforced, not just documented.

---

## 8b. Write API (Phase 1.6)

Phase 1.6 extends the API layer with seven mutation endpoints.
Every endpoint follows the same rule as the read endpoints:
**parse → call service → map response**. The Service layer
remains the only place that decides what a write *means*; the
API is HTTP-only.

### Endpoints (Phase 1.6)

| Method | Path                                     | Status | Returns              | Purpose                                          |
|--------|------------------------------------------|--------|----------------------|--------------------------------------------------|
| POST   | `/api/v1/stories`                        | 201    | `NodeResponse`       | Create a root Story                              |
| POST   | `/api/v1/nodes/{parent_id}/children`     | 201    | `NodeResponse`       | Create a child under any Node (default `task`)   |
| PATCH  | `/api/v1/nodes/{node_id}`                | 200    | `NodeResponse`       | Rename a Node (title only)                       |
| PATCH  | `/api/v1/nodes/{node_id}/canvas`         | 200    | `CanvasResponse`     | Overwrite canvas content                         |
| PATCH  | `/api/v1/nodes/{node_id}/metadata`       | 200    | `NodeResponse`       | Set a single metadata key/value                  |
| DELETE | `/api/v1/nodes/{node_id}`                | 204    | —                    | Recursive delete                                 |
| POST   | `/api/v1/nodes/{node_id}/move`           | 200    | `NodeResponse`       | Reparent + optional position                     |

### Request DTOs

Every body uses `extra='forbid'` so the API rejects unknown
fields at the boundary, before they reach the service. This is
the first line of defense against contract drift.

| DTO                  | Notes                                                           |
|----------------------|-----------------------------------------------------------------|
| `CreateStoryRequest` | `title: str` (non-empty after trim)                             |
| `CreateChildRequest` | `title: str`, `type: Literal["task", "note"]` (defaults to `task`) |
| `PatchNodeRequest`   | `title: str` (empty patch allowed → no-op)                      |
| `PatchCanvasRequest` | `content: str` (empty string clears canvas)                     |
| `PatchMetadataRequest`| `key: str`, `value: Any`                                       |
| `MoveNodeRequest`    | `new_parent_id: str \| None`, `position: int \| None (≥ 0)`     |

The DTOs are deliberately minimal. Multi-field updates would
encourage clients to send "patch bombs" with stale state; one
field per endpoint keeps the wire contract explicit and the
OpenAPI schema readable.

### Status code policy

| Outcome                                         | Status |
|-------------------------------------------------|--------|
| Resource created                                | 201    |
| Resource updated, returned                      | 200    |
| Resource deleted                                | 204    |
| Service-layer validation (whitespace, etc.)     | 422 with `code: "validation_error"` |
| Pydantic validation (missing field, bad type)   | 422 with `detail`                  |
| Domain "not found"                              | 404    |
| Domain "would cycle"                            | 409    |
| Unhandled internal failure                      | 500    |

Note that whitespace-only titles reach the service, which
raises `ValueError`; the registered `ValueError` handler
returns 422 with the uniform envelope. This keeps the
service layer free of HTTP concerns.

### Exception → HTTP status mapping (additions over §8a)

| Service exception              | HTTP status | Body code             |
|--------------------------------|-------------|-----------------------|
| `InvalidRenameServiceError`    | 422         | `validation_error`    |
| `InvalidMetadataServiceError`  | 422         | `validation_error`    |

These are the service's "this input shape is wrong" errors.
They map to 422 — the same status as Pydantic validation
failures — so the frontend has a single code path for
"rejected by validation."

### What the API layer does NOT do

- No conditional logic in handlers. Every endpoint is five
  lines: signature, service call, mapper, return.
- No try/except. Errors flow through registered handlers.
- No `Node(...)` construction. The domain builds Nodes; the
  API only validates the *request* shape.
- No domain mutation. Every mutation is a single service
  call. Multi-step orchestration belongs in the service
  (and ultimately in the repository — ADR-0006).

This is enforced by `test_endpoint_files_are_thin` in
`backend/tests/test_api_isolation.py`. The test forbids inline
construction of `Node` and forbids inline `except ServiceError`
in endpoint files.

### Request lifecycle (write path)

```
   HTTP Request       FastAPI Route       WorkspaceService       WorkspaceRepository       Filesystem
   ───────────        ──────────────      ────────────────       ──────────────────        ──────────
        │                  │                     │                       │                      │
        │ POST /stories    │                     │                       │                      │
        │ {"title":"X"}    │                     │                       │                      │
        │ ───────────────► │                     │                       │                      │
        │                  │ pydantic validates  │                       │                      │
        │                  │ service.create_     │                       │                      │
        │                  │   story("X")        │                       │                      │
        │                  │ ──────────────────► │                       │                      │
        │                  │                     │ repo.save_node(       │                      │
        │                  │                     │   node, parent_id)    │                      │
        │                  │                     │ ────────────────────► │                      │
        │                  │                     │                       │ atomic write          │
        │                  │                     │                       │ ────────────────────►│
        │                  │                     │                       │ ◄────────────────────│
        │                  │                     │ ◄──────────────────── │                      │
        │                  │ ◄────────────────── │                       │                      │
        │                  │ mapper.node_to_dto  │                       │                      │
        │                  │ return 201 + JSON   │                       │                      │
        │ ◄────────────────│                     │                       │                      │
```

The route is five lines. The service is unaware of HTTP. The
repository is unaware of requests. The filesystem is unaware
of anything above the byte level.

### Endpoint-to-service mapping

| Endpoint                                     | Service method              |
|----------------------------------------------|------------------------------|
| `POST   /api/v1/stories`                     | `create_story(title)`        |
| `POST   /api/v1/nodes/{id}/children`         | `create_child(parent_id, title, type)` |
| `PATCH  /api/v1/nodes/{id}`                  | `rename_node(node_id, title)` |
| `PATCH  /api/v1/nodes/{id}/canvas`           | `write_canvas(node_id, content)` |
| `PATCH  /api/v1/nodes/{id}/metadata`         | `update_metadata(node_id, key, value)` |
| `DELETE /api/v1/nodes/{id}`                  | `delete_node(node_id)`       |
| `POST   /api/v1/nodes/{id}/move`             | `move_node(node_id, new_parent_id, position)` |

One endpoint per service method (with the exception of
`PATCH /metadata` and `POST /move`, which wrap two-arg
service methods). This 1:1 mapping is what keeps the API
layer thin.

### Known limitations deferred to later phases

- **Partial metadata updates** — `PATCH /metadata` is single-key
  for now. Bulk updates (and value-type validation) land with
  the search index, which needs to read every metadata key
  anyway.
- **`update_metadata` round-trips through `rename_node`** in the
  current service. This is a Phase 1.4 workaround because the
  repository has no first-class partial-update method. Tracked
  in §17; the right shape is a dedicated repository operation
  in Phase 4 (see TODO in `backend/repositories/protocol.py`).
- **No optimistic concurrency.** A second writer can clobber a
  first writer's update. Acceptable for the single-user
  filesystem case; will need ETags or version vectors when
  multi-user access lands.
- **No batch operations.** Bulk-create / bulk-move will be
  added when the UI needs them, not before.

---

## 9. Storage Model

### Three storage tiers, three purposes

| Tier              | Role                  | Why it exists                                |
|-------------------|-----------------------|----------------------------------------------|
| Filesystem        | Source of truth        | User ownership, durability, human-readable.  |
| In-memory tree    | Runtime representation | O(1) traversal for UI; no DB round-trips.    |
| PostgreSQL index  | Fast queries / search | Full-text search, graph aggregations, tags.  |

### Why each exists

- **Filesystem.** The user can browse, back up, edit with any tool, and
  sync (Dropbox, git, rsync). The app is optional; the data is not.
- **In-memory tree.** The UI needs to walk the entire hierarchy on every
  keystroke for folder view and on every zoom/pan for graph view. Doing
  this against Postgres on every render is wasteful. A single Python
  object graph is the fastest thing we can give the UI.
- **PostgreSQL.** Search and cross-cutting queries ("all tasks mentioning
  'deploy'") are awkward on a filesystem. SQL with `tsvector` and indexes
  is the right tool. Importantly, **Postgres can be wiped and rebuilt
  from disk** — it is an index, not the source.

### Consistency model
- Writes go to disk first.
- The in-memory tree is updated synchronously after a successful write.
- Postgres is updated best-effort and reconciled by a periodic rescan or
  on-startup rescan.

---

## 10. Filesystem Layout

```
workspace/
├── .ptt/
│   ├── config.yaml              # Workspace-level config
│   └── index.sqlite             # (Optional) local index cache
│
├── <story-slug>/                # One folder per Story
│   ├── .node.json               # Metadata sidecar for the Story node
│   ├── README.md                # Default canvas for the Story
│   │
│   ├── <task-slug>/             # Nested Task folder
│   │   ├── .node.json
│   │   ├── README.md            # Default canvas for the Task
│   │   └── notes.md             # Extra free-form canvas
│   │
│   └── another-task/
│       ├── .node.json
│       └── README.md
│
└── another-story/
    └── ...
```

### Conventions

- **Story folders** — Each top-level child of the workspace root is a
  Story. Detected by being a directory under the workspace root.
- **Task folders** — Any child directory of a Story (or another Task) is
  a Node. Nesting depth = node depth in the tree.
- **Markdown canvas** — A `README.md` per folder is the default canvas
  for that Node. Additional `.md` files are alternate canvases.
- **Metadata file** — A `.node.json` sidecar holds the structured
  metadata: UUID, type, parent reference, links, custom fields.

---

## 11. Node Model

```python
Node:
    uuid:        UUID            # Stable across renames.
    title:       str              # Display name.
    type:        NodeType         # story | task | note | ...
    parent:      Optional[UUID]   # None for root Stories.
    children:    List[UUID]       # Ordered.
    canvas:      Optional[str]    # Relative path to default markdown.
    metadata:    dict             # Type-specific freeform fields.
```

### Fields explained

- **uuid** — Stable across renames and moves. Used as the key in the
  in-memory tree and Postgres. Never reused, never recycled.
- **title** — Human-readable. May change. Folder name is derived from
  the slugified title but is not required to match.
- **type** — Discriminator. New types can be added without schema
  changes by extending the enum and adding UI affordances.
- **parent** — Single parent. The tree is a tree, not a graph.
  Cross-links live in `metadata.links` and become edges in the graph.
- **children** — Ordered list of child UUIDs. Order is preserved across
  reads.
- **canvas** — Path to the default Markdown file. Optional: some Nodes
  are pure structural (a "folder").
- **metadata** — Free-form dict. Type-specific fields live here
  (status, due date, tags, links to other Nodes).

### Why every entity is a Node

A separate `Story` table, `Task` table, `Note` table would multiply code
paths: load-by-id, save, delete, move, list-children — each would need
to be polymorphic or duplicated. With one Node model and a `type`
discriminator:
- One CRUD path.
- One tree-traversal path.
- One serialization path.
- Adding a new "type" is a metadata change, not a migration.

The trade-off: type-specific behavior lives in metadata and is read by
the UI rather than enforced by the schema. That is acceptable — the UI
is the contract for behavior; the schema is the contract for identity.

---

## 12. Data Lifecycle

The ordered pipeline below applies to **every** write. Skipping a tier
or reordering tiers is a bug.

```
  API Layer  ──►  Service Layer  ──►  Filesystem  ──►  Memory Tree  ──►  Postgres Index
```

### Why the ordering matters

1. **Filesystem before everything else.** The disk is the source of
   truth. If a write fails on disk, it must not exist anywhere else.
2. **Memory tree immediately after disk.** The UI must reflect the
   change in the same request that performed it. Async propagation
   would mean stale reads.
3. **Postgres last, best-effort.** If Postgres is down, the user can
   still work. The index can be reconciled later.

### Lifecycles

#### Application startup
1. Load `config.yaml` from `.ptt/`.
2. Connect to Postgres (best-effort; warn on failure).
3. Walk the filesystem; for each Node folder, read `.node.json` and
   `README.md`.
4. Build the in-memory tree; verify UUIDs are unique.
5. Schedule a Postgres reconciliation pass (full rescan → upsert).
6. Serve health endpoint.

#### Create Node
1. **API:** `POST /nodes` with `{ parent_uuid, title, type }`.
2. **Service:** validate parent exists; mint UUID; slugify title;
   resolve folder path under parent.
3. **Filesystem:** create folder; write `.node.json`; write
   `README.md` (empty canvas); update parent's `.node.json`
   `children[]`.
4. **Memory tree:** insert Node; update parent node's `children[]`.
5. **Postgres:** upsert Node row.

#### Rename Node
1. **API:** `PATCH /nodes/{uuid}` with `{ title }`.
2. **Service:** derive new slug; check no sibling collision.
3. **Filesystem:** rename folder to new slug; update Node's
   `.node.json` `title`; *do not* change `uuid`.
4. **Memory tree:** update Node's `title`.
5. **Postgres:** upsert Node row (title change).

#### Move Node
1. **API:** `POST /nodes/{uuid}/move` with `{ new_parent_uuid,
   position }`.
2. **Service:** validate both parents exist; refuse moves that would
   create a cycle.
3. **Filesystem:** remove from old parent's `children[]`; insert into
   new parent's `children[]` at `position`; physically move folder.
4. **Memory tree:** update `parent`; rewire `children[]` on both
   parents.
5. **Postgres:** upsert Node row; update parent FK.

#### Delete Node
1. **API:** `DELETE /nodes/{uuid}`.
2. **Service:** recursively gather descendant UUIDs; refuse if any
   descendant is protected (future: tags, locks).
3. **Filesystem:** remove folder subtree; remove from parent's
   `children[]`.
4. **Memory tree:** drop subtree.
5. **Postgres:** delete rows for the subtree.

#### Update Canvas
1. **API:** `PUT /nodes/{uuid}/canvas` with `{ content }`.
2. **Service:** load Node; identify canvas path (default or named).
3. **Filesystem:** write Markdown file atomically (temp + rename).
4. **Memory tree:** no structural change (canvas is content, not
   shape); bump a `canvas_rev` counter on the Node.
5. **Postgres:** `tsvector` column updated; `canvas_rev` upserted.

#### Update Metadata
1. **API:** `PATCH /nodes/{uuid}` with `{ metadata: {...} }`.
2. **Service:** deep-merge with existing metadata; reject unknown
   type-discriminator fields if validation rules exist.
3. **Filesystem:** rewrite `.node.json` (atomic temp + rename).
4. **Memory tree:** replace Node.metadata.
5. **Postgres:** upsert Node row (JSONB column).

---

## 13. Development Roadmap

### Phase 0 — Architecture *(current)*
- Define core concepts (Node, Story, Canvas, Views).
- Decide storage tiers and roles.
- Document filesystem layout and Node model.
- Capture open questions, non-goals, and invariants.

### Phase 1 — Core Backend
- FastAPI project skeleton.
- Settings/config loading.
- Health endpoint.

### Phase 2 — Filesystem Layer
- Workspace scanner.
- Markdown + sidecar reader/writer.
- Slug generator.

### Phase 1.3 — Repository Layer (persistence coordination)
- `WorkspaceRepository` Protocol in `backend/repositories/`.
- `LocalWorkspaceRepository` — disk-backed implementation that owns
  tree reconstruction from disk and the multi-step coordination of
  writes.
- Repository returns only domain types; never imports `pathlib` or
  exposes filesystem paths.
- Tests for round-trip persistence, ordering independence, and
  identical tree reconstruction after a complete reload.
- Reconstruction baselines recorded for 100 / 1000 / 5000 nodes.

### Phase 3 — Memory Tree
- Build tree from filesystem on startup.
- Read APIs that walk the tree.
- Mutation APIs that update disk + tree together.

### Phase 4 — PostgreSQL Index
- Schema for Nodes.
- Indexer that scans disk and upserts rows.
- Rebuild-from-disk command.
- Search endpoint.

### Phase 5 — REST API
- CRUD for Nodes.
- Move / reorder.
- Canvas read/write.

### Phase 6 — Graph Builder
- Edge extraction from `metadata.links`.
- Force-directed layout (frontend).

### Phase 7 — Frontend
- Folder view.
- Graph view.
- Editor for canvases.

### Future Phases
*(See Future Decisions and Open Questions.)*

---

## 13. Index Layer (Phase 2.0)

The Postgres index is a **derived projection** of the workspace
tree. It exists to answer metadata questions without walking
the filesystem on every request — and it must always be
fully rebuildable from the filesystem. Dropping the database
loses nothing the user wrote.

### What the index stores, and what it deliberately doesn't

| Stored column          | Why it's indexed                            |
|------------------------|---------------------------------------------|
| `node_id`              | Primary key. Stable UUID, never changes.    |
| `parent_id`            | "Who are my siblings?" queries.            |
| `story_id`             | "Everything under story X" — the most common scoped search. |
| `title`                | Display + `ILIKE` queries.                 |
| `node_type`            | Discriminator with a CHECK constraint (`story` / `task` / `note`). |
| `filesystem_path`      | Lets a rebuild re-locate the Node without re-walking the tree. *Not* used for queries. |
| `created_at`           | Stable sort key inside a story; never updated. |
| `updated_at`           | Last write timestamp; managed by DB `onupdate=`. |
| `search_text`          | Reserved for Phase 4 full-text search. Empty in Phase 2.0. |

What the index **does not** store:

- The Node's body / metadata payload — only the fields you
  need to *decide* whether a Node is relevant. The body
  itself is loaded from the filesystem when the UI opens a
  Node.
- Canvas content — canvases can be tens of KB; duplicating
  them in the database is wasted I/O.
- Cross-Node relationships beyond parent/child — those live
  in `backend.graph` (Phase 6+).

### Architectural contract

| Property                  | Rule                                                         |
|---------------------------|--------------------------------------------------------------|
| Depends on                | Domain (`NodeId`), Database (`Base`, `session`), Settings   |
| MUST NOT import           | `backend.api`, `backend.services`, `backend.repositories` (the filesystem-tree repository) |
| One place wires DI        | A future phase will add a `get_index_repository` to `backend.api.dependencies`. |
| Implementation ownership  | `backend.index.protocol.IndexRepository` + impls under `backend.index.impl`. |
| Tables                    | Owned by Alembic; hand-written migrations under `backend/alembic/versions/`. |
| Layering                  | Index is a **leaf**. Nothing in this package imports from elsewhere except domain/database/settings. |

The two isolation tests under `backend/tests/index/test_isolation.py`
enforce this contract via AST walking: every import in an index
module must be on the index-side allowlist, and no file under
`backend/api/`, `backend/services/`, or `backend/repositories/`
may reference any index symbol.

### Rebuild path (lifecycle)

```
   Filesystem                    IndexRepository                  Postgres
   ──────────                    ────────────────                 ────────
        │                              │                              │
        │  read_tree()                 │                              │
        │  ──────────►                 │                              │
        │   Tree (domain)              │                              │
        │ ───────────────────────────► │                              │
        │                              │                              │
        │                              │  truncate()                  │
        │                              │ ───────────────────────────► │
        │                              │                              │
        │                              │  for each Node:              │
        │                              │   upsert(IndexRecord)        │
        │                              │ ───────────────────────────► │
        │                              │                              │
```

`truncate` + `upsert_many` is the only path through which
the index becomes consistent. There's no incremental
synchronisation in Phase 2.0 — every rebuild walks the full
filesystem and replaces the index wholesale. The
`IndexRepository.truncate()` method exists *only* for this
rebuild path; it never appears in normal write paths.

### Boot-time rebuild (Phase 3+, not implemented here)

```
   app startup
        │
        │  filesystem.read_tree()
        │     │
        │     ▼
        │  convert every Node → IndexRecord
        │     │
        │     ▼
        │  index.truncate() + index.upsert_many(records)
        │
        ▼
   ready for queries
```

The startup reconcile owns zero business logic: it walks
disk, projects records, replaces index contents. The result
is a usable index that's provably consistent with the
workspace on disk. If the DB is unreachable, the reconcile
is best-effort — the workspace still loads from disk, and
search queries surface "index unavailable" (Phase 4
behaviour, not yet defined).

### When the index will gain writes (not Phase 2.0)

The repo pathway today is:

```
   API  ──►  Service  ──►  WorkspaceRepository  ──►  Filesystem
```

Phase 3+ will add a **side-channel write** so each
`WorkspaceService` mutation also enqueues an `IndexRecord`
for the reconciler. The reconciler is what knows how to
batch, retry, and catch up after downtime. The service
layer never imports the index — that would invert the
dependency direction (ADR-0011).

### What lives outside the index (today)

- The filesystem-backed `WorkspaceRepository` (Phase 1.2+).
  It owns the source-of-truth tree.
- The in-memory `Tree` mirror (Phase 3+ planned).
- Anything in `backend.services.*`, `backend.api.*`,
  `backend.repositories.*` — these layers reach the index
  only via a future DI seam, never by direct import.

---

## 13a. Reconciler (Phase 2.1 — Offline Rebuild)

The Reconciler is the only component in the system that pulls
from the filesystem tree *and* writes to the Postgres index —
and yet it depends on neither the Filesystem implementation nor
the SQLAlchemy ORM. It exists to prove the load-bearing
architectural invariant:

> **The Postgres index is fully derivable from disk; dropping
> it loses nothing; rebuilding it produces a deterministic
> rowset.**

### What the Reconciler is

```
   WorkspaceRepository              IndexRepository
   ──────────────────               ────────────────
        │                                │
        ▼                                ▼
    load_tree()        replace_all(records)
        │                                ▲
        ▼                                │
   IndexReconciler.rebuild()  ────────────┘
        │
        ▼
   ReconcileReport
```

`IndexReconciler` is constructed with **two Protocol
dependencies** (not concrete classes):

    IndexReconciler(
        workspace_repo: WorkspaceRepository,
        index_repo:     IndexRepository,
        path_provider:  FilesystemPathProvider,   # Phase 3 hook
    )

It pulls the canonical domain `Tree` via `workspace_repo`, walks
it **sorted lexically by node_id** (so the rowset is a pure
function of the node set, not of how the filesystem was
walked), maps every `Node` to an `IndexRecord`, and replaces
the index contents via `index_repo.replace_all(records)` in a
single transaction.

The path-provider is the third seam: the Reconciler must never
know which side of a Node is on disk. Phase 3+ will bind this
to the LocalWorkspaceRepository's path accessor.

### Sequence diagram (full rebuild)

```
   caller              IndexReconciler       WorkspaceRepo       IndexRepo
   ──────              ───────────────       ─────────────       ────────
      │                      │                    │                 │
      │ rebuild()            │                    │                 │
      │ ───────────────────► │                    │                 │
      │                      │ load_tree()        │                 │
      │                      │ ─────────────────► │                 │
      │                      │ ◄───────────────── │                 │
      │                      │                                    │
      │                      │ for each NodeId (sorted):          │
      │                      │   _project_tree(...)               │
      │                      │                                    │
      │                      │ all_node_ids()                     │
      │                      │ ──────────────────────────────────►│
      │                      │ ◄──────────────────────────────────│
      │                      │                                    │
      │                      │ replace_all(records)               │
      │                      │ ──────────────────────────────────►│
      │                      │ ◄──────────────────────────────────│
      │                      │                                    │
      │ ◄────────────────── │                                    │
      │  ReconcileReport    │                                    │
```

### Determinism strategy

| Decision                   | Why                                                 |
|----------------------------|-----------------------------------------------------|
| Sort by `node_id` lexically | Isolates the output from filesystem walk order.   |
| Single shared `now` per rebuild | Cross-row `updated_at` is uniform within a pass. |
| `replace_all` is atomic   | The index is either fully consistent or untouched — there is no in-between. |
| Pure project step          | `(tree, node, path) → IndexRecord`. No I/O.       |
| No partial rebuilds        | A path-lookup failure aborts the rebuild before the index is touched. |

Two runs against the same workspace therefore produce
identical IndexRecord sets. Wall-clock duration and per-row
`updated_at` change; row count, ids, titles, paths, and
parents do not. The contract test
`test_rebuild_is_deterministic_across_construction_order`
asserts this property.

### Failure modes (explicit behaviour)

The Reconciler never produces a partial index. Possible
outcomes from `rebuild()`:

| Failure                                 | Result                                              |
|-----------------------------------------|-----------------------------------------------------|
| `load_tree()` raises                    | `errors` populated, no records processed, index untouched. |
| Path lookup fails for one Node          | `errors` populated, **rebuild aborted**, index untouched. |
| `_project_node` raises for one Node     | `errors` populated, **rebuild aborted**, index untouched. |
| `replace_all` raises (DB error, etc.)   | The repository rolls back; the reconciler surfaces it in `errors`. |
| `all_node_ids()` raises                 | Treated as soft (`errors` populated but rebuild may still proceed). |

The asymmetries are deliberate:

- We treat *projection* failures as hard because a Node we
  can't index is data we silently lost — a user-visible bug.
- We treat *id-listing* failures as soft because the
  rebuild itself doesn't depend on knowing the pre-existing
  ids; deletion count is best-effort.

### ReconcileReport shape

```python
@dataclass(slots=True, frozen=True)
class ReconcileReport:
    nodes_scanned: int        # every Node observed
    records_built: int        # IndexRecords produced
    records_inserted: int     # rows written by replace_all (== records_built on success)
    records_updated: int      # 0 for full rebuild (reserved for incremental)
    records_deleted: int      # pre-existing ids that did NOT survive
    elapsed_seconds: float
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def is_success(self) -> bool: ...
```

The contract: `records_deleted` reflects the precise
intersection (using pre-existing ids minus ids in the rebuilt
set). `records_updated == 0` for full rebuilds; the field is
reserved so the incremental variant lands without changing
the report shape.

### What the Reconciler deliberately does NOT do

- **No incremental sync.** Every rebuild is full.
  Incremental tracking belongs to a future Reconciler
  variant; the brief is explicit about not building it now.
- **No event hooks.** The Reconciler does not subscribe to
  filesystem events or service-layer mutations. It runs on
  demand (or, in Phase 3+, on boot).
- **No background workers.** Calling `rebuild()` is the
  entire reconciliation.
- **No startup integration.** Boot-time reconciliation
  belongs to `backend.main` (Phase 3+). The Reconciler is
  transport-agnostic; main.py wires it onto the lifespan.
- **No business logic.** All logic is "project → replace";
  the projection is the only place domain knowledge lives
  and the projection is a pure function.

### Architectural rule

The Reconciler's isolation is enforced by
`backend/tests/index/test_isolation.py`. Two guarantees:

1. **The Reconciler may import Protocols, not
   concretes.** It pulls `IndexRepository` from
   `backend.index.protocol` and `WorkspaceRepository` from
   `backend.repositories.protocol` — both type-only
   dependencies. Reaching for a concrete class triggers an
   isolation failure in the test suite.

2. **No reverse dependency.** Neither the API layer, the
   service layer, nor the filesystem-tree repository
   references any index symbol. The Reconciler stands alone.

---

## 14. Non-Goals for V1

The following are explicitly **out of scope** for the first release.
Mentioning them here is not a roadmap commitment — it is a guard
against scope creep.

- **Multi-user authentication.** The workspace is local and single-user.
- **Cloud sync.** Local-only for V1.
- **Real-time collaboration.** No CRDT, no operational transform, no
  presence indicators.
- **AI features.** No summarization, suggestion, or LLM integration.
- **Mobile / responsive UI.** Desktop browser only.
- **Plugin / extension API.** No public extension surface.
- **Reminders / notifications.** No scheduler, no push notifications.
- **External-edit conflict resolution.** A rescan reconciles; no merge
  UI.
- **Backlinks UX.** Backlinks are *indexed* but no backlinks UI in V1.
- **Markdown extensions beyond CommonMark + GFM.** No custom render
  pipeline.
- **Versioning / history.** No git-like history of Nodes or canvases.
- **Import / export formats.** Other than "the workspace is already on
  disk and importable as folders."

---

## 15. Architectural Invariants

These rules are non-negotiable. Every future implementation must be
evaluated against them. A change that violates an invariant is not a
code change — it is an architecture change and must be discussed before
it is merged.

1. **The filesystem is the only persistent source of truth.**
   All durable state lives on disk in plain folders and files. The
   application is replaceable; the data is not.

2. **PostgreSQL is an index only.**
   It must always be completely rebuildable from the filesystem. Losing
   the database must never cause data loss — only a (potentially
   expensive) rebuild.

3. **Every Node has exactly one parent.**
   The hierarchy is a tree. Cross-links are *references* recorded in
   metadata and surfaced as graph edges; they do not imply ownership
   and must not create second parents.

4. **The frontend never accesses or manipulates the filesystem directly.**
   All reads and writes are routed through the backend. The UI
   receives rendered views and POSTs mutations; it never opens files,
   edits `.node.json`, or assumes a local disk path.

5. **Every mutation flows through the backend service layer.**
   API handlers (routes) validate input and shape responses. They do
   not contain business logic. A mutation that bypasses the service
   layer is a bug.

6. **Every Node owns a stable UUID for its entire lifetime.**
   Renames, moves, type changes — none of these change the UUID. UUIDs
   are never reused or recycled. This is what makes external links and
   cross-references survive reorganization.

7. **The graph is always derived from the tree.**
   The graph view is a computed projection of the tree (plus
   cross-link references from metadata). It is never stored as an
   independent source of truth and must always be reconstructible from
   disk.

8. **Business logic must never live inside the API layer.**
   Routes, request/response models, and HTTP concerns belong to the
   API layer. Business rules — invariants, derived state, ordering,
   conflict policies — belong to the service layer. A test that
   imports a route module to exercise business logic is a smell.

---

## 16. Open Questions

The following architectural decisions are intentionally postponed.
They are marked **TBD** and will be revisited after Phase 0 review.
These are decisions that *must* be made before V1 ships, but not
before relevant phases begin.

- [TBD] Authentication / multi-user support — required for any non-local
  deployment; deferred for V1.
- [TBD] Real-time collaboration — only after single-user UX validates.
- [TBD] AI features — explicitly excluded; would require their own
  spec.
- [TBD] Plugin system — needs an API surface definition first.
- [TBD] Reminders / notifications — deferred.
- [TBD] Mobile / responsive frontend — desktop-first.
- [TBD] Sync strategy — local-only for now; cloud sync is a separate
  question.
- [TBD] Conflict resolution when filesystem is edited externally —
  rescan handles it for V1; merge UI later if needed.
- [TBD] Backlinks UX — captured in the index; no UI yet.

---

## 17. Future Decisions

This is the planning backlog — decisions we know are coming but are
not yet ready to make. Each entry should graduate to an ADR when the
relevant phase begins.

| Decision                            | Triggers when…                          |
|-------------------------------------|-----------------------------------------|
| Choice of frontend framework        | Phase 7 begins                          |
| Choice of graph layout library      | Phase 6 begins                          |
| Search semantics (tsquery vs LIKE)  | Phase 4 begins                          |
| Postgres hosting (local vs remote)  | Deployment planning                     |
| Workspace discovery / multi-workspace | Multi-workspace feature requested     |
| Backup / snapshot strategy          | Sync story begins                       |
| Markdown editor (CodeMirror / Monaco / TipTap) | Phase 7 frontend |
| Canvas attachment model (one canvas per Node vs many named) | Phase 5 |
| Future search backend (SQLite FTS / PG tsvector / Tantivy / Meilisearch) — replace the IndexRepository implementation, never SearchService | A search backend is selected (post-Phase 4) |

### Technical debt

| Item                                  | Tracking                               | Resolve when…                                |
|---------------------------------------|----------------------------------------|----------------------------------------------|
| `update_metadata` round-trips through `rename_node` in the Service layer | TODO(phase-4) in `backend/repositories/protocol.py` | Persistence/indexing phase adds a dedicated `update_metadata` repository method that writes only `node.json` |
| `LocalFilesystem._unique_slug` cannot detect id collisions on save | Phase 1.3 placeholder test | Phase 4 adds a constraint / index in Postgres that rejects duplicate ids |
| `Tree._nodes` directly mutated in test fake | Code smell in `InMemoryWorkspaceRepository` | Phase 4 if a richer test-helper surface emerges (low priority) |

---

## 18. Architecture Decision Log (ADR-lite)

Future architectural decisions are recorded here using the template
below. Each entry is immutable once accepted; superseded entries link
to their replacements.

### Template

```
### ADR-XXXX — <Title>

- **Date:** YYYY-MM-DD
- **Status:** Proposed | Accepted | Superseded by ADR-YYYY
- **Decision:** <What was decided, in one sentence.>
- **Rationale:** <Why. Tie to invariants and principles where possible.>
- **Alternatives Considered:**
  - <Option A> — rejected because …
  - <Option B> — rejected because …
- **Consequences:** <What this enables; what it forecloses.>
- **Supersedes:** (optional) ADR-XXXX
```

### Decisions

#### ADR-0001 — Domain layer separation
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** The project will include a dedicated `backend/domain/`
  package that owns the value objects and entities of the system, with
  zero imports from FastAPI, SQLAlchemy, Pydantic, Starlette, or
  Alembic. Enforced by a structural purity test.
- **Rationale:** Three adapters (HTTP, DB, filesystem) defining their
  own Node type each is the most common source of drift in CRUD apps.
  One domain layer is the single source of truth that all adapters
  conform to. Aligns with Architecture Principle §4.9 ("domain first,
  adapters second").
- **Alternatives Considered:**
  - No domain layer; let Pydantic models serve everywhere — rejected
    because the filesystem layer cannot depend on Pydantic (it has no
    network/JSON use), and a Pydantic-only model leaks HTTP concerns
    into the data layer.
  - Shared "model" package that mixes ORM and domain — rejected because
    SQLAlchemy leakage forces the domain to know about session
    lifecycles.
- **Consequences:** All persistence adapters translate between
  storage types and domain types; service-layer code is pure Python
  with no I/O. Adding a new field type (e.g. a new metadata key)
  requires updating one dataclass and one validator, not three ORM
  mappings.
- **Supersedes:** —

#### ADR-0002 — httpx removed from runtime deps; starlette TestClient usage moved to dev extras
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** `httpx` is not a production runtime dependency. It is
  installed only when the `[dev]` extra is selected, to support
  `starlette.testclient.TestClient`.
- **Rationale:** httpx was originally added as a speculative dep for
  future `/ready` probes (Phase 1.0). Per Architecture Principle §4.8
  ("refactor before scale, not after") and the Phase 1.0 review, we
  avoid speculative deps. starlette's TestClient transitively requires
  httpx for ASGI dispatch, so the dependency is split: runtime stays
  minimal; dev/test gets httpx via extras.
- **Alternatives Considered:**
  - Keep httpx in runtime — rejected because the dependency is not
    exercised by any current code path.
  - Replace TestClient with a hand-rolled ASGI dispatcher — rejected
    because TestClient is the standard, well-supported tool and a
    hand-roll would be more code than the dep savings justify.
- **Consequences:** CI must install `uv sync --extra dev` (or
  equivalent) before running tests. Production containers install
  only the base extras. This is documented in `README.md` and the
  `pyproject.toml` extras definition.

#### ADR-0003 — Filesystem abstraction via `typing.Protocol` + dedicated LocalFilesystem
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** Persistence to disk goes through a `Filesystem`
  protocol. The disk-backed implementation is `LocalFilesystem`. All
  paths use `pathlib.Path`. JSON is owned by a single module.
  Writes are atomic via temp-file + rename.
- **Rationale:** Per Invariant §1, the filesystem is the source of
  truth; per Architecture Principle §4.9, adapters conform to the
  domain rather than the other way around. A Protocol-based interface
  keeps the rest of the codebase (services, tests) decoupled from the
  on-disk layout. Atomic writes are required by TECH_SPEC §7 to ensure
  no partial JSON survives a crash.
- **Alternatives Considered:**
  - Direct calls to `LocalFilesystem` from services without a
    Protocol — rejected because tests would need a real disk (or a
    mock), and a future remote filesystem would touch every caller.
  - JSON owned by the ORM layer — rejected because there is no ORM
    yet (Phase 4) and the disk schema should be readable without
    SQLAlchemy present.
- **Consequences:** The service layer (Phase 3+) depends on the
  protocol; tests can pass any object that satisfies it. Future
  remote or in-memory adapters plug in without changing call sites.
  Corruption is surfaced, not silently healed; recovery remains
  manual until a future phase adds a repair service.

#### ADR-0004 — Filesystem corruption is surfaced, not auto-healed
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** When the filesystem layer encounters a missing or
  invalid `node.json`, a missing `canvas.md`, or duplicate ids, it
  raises a typed exception instead of attempting to repair the data.
- **Rationale:** Auto-healing can destroy what the user wanted. A
  half-written JSON that "looks recoverable" might encode a state the
  user deliberately saved just before the crash. Silent recovery
  makes that state disappear; explicit failure leaves the file on
  disk for the user to inspect. The filesystem is the source of
  truth — the source of truth must not lie about itself.
- **Alternatives Considered:**
  - Auto-rename an unknown directory to `recovery-<uuid>` and skip —
    rejected because the user may have *intended* that directory
    layout and the rename would surprise them.
  - Auto-regenerate node.json from the directory name (treat the
    directory name as the source of truth) — rejected because it
    loses metadata, links, and ordering without warning.
  - Quarantine on next read by moving the suspect directory to
    `.ptt/quarantine/` — deferred; might be added in a future phase
    as an opt-in repair workflow.
- **Consequences:** A corrupted workspace is visible to the user
  (error messages name the directory). No silent data loss. Future
  repair tooling can be added explicitly without breaking this
  contract.

#### ADR-0005 — Repository layer between Service and Filesystem
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** A `WorkspaceRepository` Protocol (with
  `LocalWorkspaceRepository` as the disk-backed implementation) sits
  between the Service Layer and the Filesystem Protocol. Services
  depend only on the repository; the repository is the only
  component that calls `Filesystem` directly.
- **Rationale:** Three concerns naturally separate into three layers,
  even though two of them are persistence-shaped:

  1. **Domain** owns the entities and their invariants (Phase 1.1).
  2. **Filesystem** owns the on-disk layout: directory slugs,
     `node.json` shape, atomic-rename writes, and the byte-level
     mapping from disk to `Node` (Phase 1.2). It is correct to ask
     "given a Node, write it to disk" — but not "given a workspace,
     reconstruct the tree."
  3. **Repository** owns the bridge: tree reconstruction, multi-step
     write coordination, and translating persistence-layer
     exceptions to domain exceptions. It returns only domain types.

  Without this layer, the Service Layer would import
  `LocalFilesystem` directly and become coupled to both the
  on-disk layout *and* the tree-walking algorithm. The Service would
  re-implement reconstruction for every consumer (Postgres indexer,
  API handlers, background jobs), and tests would have to mock the
  filesystem instead of mocking the repository.

  With this layer, the Service depends on a Protocol; tests can
  swap in a fake repository without touching disk. The
  reconstruction algorithm has exactly one home, and any future
  adapter (Postgres indexer, remote workspace, in-memory workspace
  for tests) plugs in by implementing the same Protocol.

  Architectural Invariants §2 ("services must not call filesystem
  directly") would be unenforceable without this layer — there
  would be no boundary to enforce.
- **Alternatives Considered:**
  - **Repository = Filesystem.** Make the Filesystem Protocol itself
    include tree reconstruction. Rejected because reconstruction
    needs to coordinate across many Filesystem operations (and
    tolerate partial corruption), which is a different abstraction
    from "given a Node, read or write one directory." Mixing them
    would inflate every Filesystem implementation with
    reconstruction code it doesn't need (e.g. an in-memory
    Filesystem has nothing to reconstruct).
  - **Repository = Service.** Fold the bridge into the Service
    Layer. Rejected because every Service would then carry its own
    reconstruction logic, the on-disk layout would leak into
    business code, and mocking for tests would require mocking the
    filesystem (slow, brittle).
  - **No repository; Services call Filesystem directly.** The
    "obvious" answer for a small project. Rejected because
    Invariant §2 explicitly forbids it, and because the project
    already anticipates multiple consumers (API, indexer, future
    background jobs) that would each re-implement the bridge.
- **Consequences:**
  - Every service-layer module depends on `WorkspaceRepository`,
    never on `LocalFilesystem` or `Filesystem`.
  - `LocalWorkspaceRepository.load_tree()` is the single owner of
    tree reconstruction; changes to reconstruction logic land in
    one place.
  - Tests can pass an in-memory repository fake (Phase 3+ will
    add one), making service tests fast and deterministic.
  - The repository layer is a natural place to add observability
    later (timing, error counters) without touching the filesystem.
  - Adds one extra layer of indirection for newcomers. Mitigated
    by clear Protocol docstrings and the sequence diagrams in §8.

#### ADR-0006 — Repositories are the only persistence orchestrator
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** Multi-step persistence operations — anything that
  reads or writes more than one Filesystem call to satisfy one
  logical operation — are the exclusive responsibility of the
  Repository layer. Neither the Service layer nor the Filesystem
  layer may perform persistence orchestration.
- **Rationale:** Three layers have three jobs:

  - **Filesystem** maps single Node operations to disk: one
    `node.json` read, one directory rename, one atomic write. It
    does not know that "moving a node" is actually three writes
    (detach from old parent's children_ids, rename directory,
    append to new parent's children_ids). That orchestration
    belongs one level up.

  - **Repository** owns multi-step persistence: tree reconstruction
    (read every `node.json` and re-wire parent links), delete
    (recursively walk descendants and remove), and any future
    "compound write" we add. The repository returns only domain
    types — services never see paths or `node.json` shapes.

  - **Service** owns the *use case*: it sequences domain operations
    (validate, mutate the in-memory tree, call the repository to
    persist, raise on domain failure). The service must NOT
    sequence persistence operations; if a service method needs to
    call two repository methods, those two calls together form
    one repository method (move-into-tree is one logical operation,
    not two).

  This rule exists because each layer's correctness argument
  depends on the layer below it being the only place where its
  concern lives:

  - If Services orchestrate persistence, then any new consumer
    (Postgres indexer, background job, future CLI) must
    re-implement the same orchestration. We get drift.
  - If Filesystem orchestrates persistence, then any new
    Filesystem implementation (in-memory, remote) must re-implement
    the same orchestration, and the orchestration's correctness
    argument gets split across two layers.
  - If Repositories orchestrate, then *every* consumer — Service,
    API handler, indexer, CLI — calls the same method. The
    orchestration has one home, one test surface, one place to
    evolve.

  This also gives a clean answer to "where does this go?":

  | Operation                                    | Layer        |
  |----------------------------------------------|--------------|
  | Read one `node.json`                         | Filesystem   |
  | Atomically write one `canvas.md`             | Filesystem   |
  | Rename one directory                         | Filesystem   |
  | Recursively delete a subtree                 | Repository   |
  | Reconstruct the full tree from disk          | Repository   |
  | Create node + wire parent's children_ids     | Repository   |
  | Validate a rename is safe + call repo        | Service      |
  | Sequence multiple use cases                  | Service      |
- **Alternatives Considered:**
  - **Allow Services to make multiple repository calls.**
    Rejected because compound operations (delete-with-children,
    move-with-detach) must be one logical step or we get partial
    failures on disk that are hard to detect and recover from.
    "Two repository calls" is also where the rule breaks down:
    every service author will think their two calls are simple
    enough, and slowly orchestration leaks back upward.
  - **Allow Filesystem to expose higher-level helpers.**
    Rejected because the Filesystem Protocol's contract is "one
    logical disk operation." If we add `delete_subtree` to
    Filesystem, every other Filesystem implementation must
    re-implement it. Keeping it in the Repository means the
    Filesystem Protocol stays minimal and replaceable.
  - **Allow Services to call Filesystem directly for "simple"
    reads.** Rejected because there's no such thing as a
    simple read once you start caring about consistency: a
    Service that reads from Filesystem directly bypasses the
    reconstruction invariants the Repository guarantees.
- **Consequences:**
  - Adding a new persistence-backed operation (e.g. "duplicate a
    subtree") requires adding a Repository method first; the
    Service then calls *one* repository method.
  - The Repository is now the only place where partial-write
    recovery logic can live — which is correct, because that's
    the layer that knows the on-disk layout.
  - Future event publishing (Phase 4+) plugs in at the
    Repository boundary as documented extension points: the
    Repository emits `node.moved`, `node.deleted`, etc., and the
    Service stays oblivious to the event bus.
  - The Repository's surface area grows as the application
    grows. Mitigated by keeping the Protocol focused on domain
    use cases (move, rename, create) rather than CRUD primitives.
  - The rule is enforceable: a structural test in CI greps
    `backend/services/` for `Filesystem` and `LocalFilesystem`
    imports and fails the build if found (deferred to Phase 1.4
    once the Service layer exists to be checked).

#### ADR-0007 — Services depend on repository protocols, not implementations
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** `WorkspaceService` (and any future service class)
  is constructor-injected with the `WorkspaceRepository` Protocol,
  never with `LocalWorkspaceRepository` (or any other concrete
  implementation). The dependency is declared in the type hint, and
  a structural test enforces it across the whole `backend/services/`
  package.
- **Rationale:** Three reasons compound:

  1. **Testability.** Service tests run against an in-memory fake
     repository (`InMemoryWorkspaceRepository`) that implements
     the Protocol — no disk, no fixtures, sub-millisecond. If the
     service depended on `LocalWorkspaceRepository`, every test
     would either hit a real tmp_path (slow, flaky) or monkey-patch
     the implementation (brittle).

  2. **Replaceability.** Phase 4 will add a Postgres-backed
     repository. The service layer doesn't need to change at all
     — production wires `LocalWorkspaceRepository` today, will
     wire the Postgres-backed one tomorrow, and tests never see
     the difference. The Protocol is the seam.

  3. **Discipline.** A concrete dependency is a footgun: any new
     service author who imports `LocalWorkspaceRepository` directly
     has bypassed the layering. The structural test
     (`test_services_isolation.py`) catches this at CI time
     before the violation can land.

  The Protocol isn't an abstraction-for-its-own-sake: it has
  exactly one implementation today and will have two tomorrow.
  That's the right time to introduce a Protocol — early enough
  to be load-bearing, late enough that we know what it should
  contain.
- **Alternatives Considered:**
  - **Depends on concrete `LocalWorkspaceRepository`.** Rejected:
    every test would need disk fixtures; the Postgres adapter in
    Phase 4 would require editing every service file; nothing
    prevents the next contributor from reaching past the seam.
  - **Depends on a base class instead of a Protocol.** Rejected:
    structural typing (`typing.Protocol`) is the idiomatic Python
    seam for this, requires no inheritance, and matches how the
    Filesystem layer is already shaped (ADR-0003). A base class
    would force every fake to inherit from `LocalWorkspaceRepository`,
    which then forces the fake to inherit production-side
    dependencies.
  - **Use FastAPI's `Depends(...)` for service construction.**
    Rejected: that ties the service to FastAPI's DI, which makes
    the service untestable without a request scope. Constructor
    injection keeps the service plain — FastAPI wires it up in
    `dependencies.py` (a future phase) without coupling.
- **Consequences:**
  - `WorkspaceService.__init__(self, repository: WorkspaceRepository)`
    is the only seam. No global state, no module-level singleton,
    no classmethod that mints a repo.
  - Production wiring lives in `backend/api/dependencies.py`
    (Phase 1.5), which is the *only* place that imports the
    concrete `LocalWorkspaceRepository`. The structural test
    forbids importing it from anywhere else.
  - The structural test also forbids importing `backend.filesystem.*`
    anywhere in `backend/services/` — that boundary is enforced
    by the same test suite.
  - The Protocol's surface must stay focused on use cases, not
    CRUD primitives. As we add compound repository methods
    (Phase 4: search, index sync), the Protocol grows by use
    case, not by persistence operation.
  - Fakes must be honest. `InMemoryWorkspaceRepository` is a
    faithful in-memory implementation of the Protocol — not a
    stub that returns canned answers. Service tests rely on
    the fake having realistic error semantics (cycle detection,
    parent-missing, etc.).

#### ADR-0008 — API DTOs are intentionally separated from Domain entities
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** The API layer never returns Domain entities
  directly. Every entity the API exposes has a Pydantic DTO
  (`backend.schemas.*`), and a mapper function
  (`backend.api.mappers.*`) translates Domain ↔ DTO at the boundary.
- **Rationale:** Three reasons compound:

  1. **Different evolutionary pressures.** Domain entities are
     shaped by internal correctness: structural invariants,
     `slots=True`, immutability, ordering semantics. DTOs are
     shaped by the wire format: field renames for clarity,
     deprecation cycles, versioning, optional fields for
     forward compatibility, plain string enums instead of
     Python Enums. Letting the two evolve together couples the
     frontend's deploy schedule to backend domain changes.

  2. **Pydantic ≠ Domain.** The domain can't depend on
     Pydantic (ADR-0001 keeps it framework-free). Returning
     `Node` from a FastAPI handler would either require
     hand-rolled JSON serialization (brittle, drift-prone)
     or violating the domain-purity invariant.

  3. **Versioning.** A future v2 of the API can introduce
     a new DTO shape (`NodeV2`) and a new mapper, while the
     Domain stays unchanged. Without DTOs, "version the API"
     becomes "version the Domain," which then forces every
     consumer (CLI, indexer, future services) to follow.

  The mapper functions are tiny — `node_to_dto(node)` is six
  lines. That's the right shape: a small, mechanical translation
  surface that absorbs every wire-format decision.
- **Alternatives Considered:**
  - **Return domain entities directly, let FastAPI serialize
    via Pydantic adapters.** Rejected: requires either making
    the domain depend on Pydantic (breaks ADR-0001) or
    building a separate adapter layer per endpoint (more code
    than a centralized mapper).
  - **Return dicts.** Rejected: dicts lose type safety for
    the frontend (the OpenAPI schema is what gives the
    frontend its types), and every consumer has to read the
    source to know the shape. DTOs generate OpenAPI schemas
    automatically.
  - **One DTO per use case, not per entity.** Rejected: it
    fragments the wire format and makes the OpenAPI schema
    harder to read. Per-entity DTOs compose cleanly across
    endpoints (`WorkspaceTreeResponse` embeds `NodeResponse`,
    not a use-case-specific shape).
- **Consequences:**
  - Every endpoint's response_model is a Pydantic class from
    `backend.schemas`. The OpenAPI schema is generated from
    these classes — they're the API's contract document.
  - Adding a field to the wire format is a one-line change
    in `backend.schemas.node.NodeResponse`. Adding a field
    to the Domain is a one-line change in `backend.domain.node.Node`.
    The two are no longer coupled.
  - Field renames in the Domain don't break the wire format
    (and vice versa).
  - The mapper layer is a natural place to add filtering,
    e.g. "never expose `metadata.internal_note` in the API
    response." That logic lives in the mapper, not in the
    Domain.
  - Endpoint tests assert on the DTO shape, not the Domain
    entity. Drift between the two is caught by tests, not
    by users.

#### ADR-0009 — API is intentionally thin; all orchestration lives in the service layer
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** Every FastAPI endpoint handler is
  **parse → call service → map response**. No conditional
  branching, no inline domain construction, no try/except,
  no multi-step orchestration in routes. The service layer
  owns *what a write means*; the API owns *what an HTTP
  request looks like*.
- **Rationale:** Three reasons compound:

  1. **Single point of truth for invariants.** If two endpoints
     both need to "create a child," but each handler builds the
     `Node` differently, the invariants of `Node` get
     duplicated. One service method = one implementation =
     one place to fix a bug.

  2. **Testability.** Service tests run against a fake
     repository, no HTTP, no JSON parsing. API tests run
     against a fake service, no filesystem. The two halves
     can fail independently, and a thin API means the API
     tests don't need to re-test invariants the service
     already covers.

  3. **Future transport flexibility.** When (not if) we add
     gRPC, CLI commands, or background workers that perform
     the same operations, they call the same service methods.
     The endpoint layer stays HTTP-specific; the service
     layer stays transport-agnostic.

  This is enforced by `test_endpoint_files_are_thin` in
  `backend/tests/test_api_isolation.py`. The test forbids
  inline construction of `Node` in endpoint files and forbids
  inline `except ServiceError` (those must flow through
  registered handlers).

- **Alternatives Considered:**
  - **Per-endpoint orchestration** (each handler does its
    own multi-step work). Rejected: invites invariant
    duplication and makes service tests insufficient.
  - **An "API service" layer between routes and the domain
    service** (a thin wrapper just to satisfy the
    orchestrator). Rejected: empty indirection. The mapper
    functions already separate the wire format from the
    domain; adding another layer buys nothing.
- **Consequences:**
  - Endpoints are 5 lines on average. Bloat here is a
    code-review red flag.
  - New use cases add a service method first, then an
    endpoint. The reverse order is suspicious.
  - Conditional logic in endpoints (status-code branches,
    business rule checks) is rejected at review.
  - The ServiceError → HTTP mapping lives in
    `backend.api.exception_handlers` and is the API's only
    type-aware code. Adding a new service error means
    adding a handler — not a try/except.

#### ADR-0010 — API versioning: why `/api/v1`, what is breaking, how v2 lands
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** Every endpoint is namespaced under `/api/v1/`
  from day one. The version is part of the URL — not a header,
  not a `?version=` query string, not content negotiation. New
  endpoints are added to v1 freely. A v2 is introduced only
  for changes that meet the breaking-change threshold below.
- **Rationale:** Three reasons compound:

  1. **URL-versioning is the simplest stable contract.**
     Proxies, browsers, curl, OpenAPI tooling, and clients of
     every kind handle `/api/v1/...` without ceremony. Header
     versioning adds two extra round-trips per bug report
     ("send `Accept: application/vnd.api+json;version=2`"),
     and content-type versioning is invisible in the address
     bar during dev — both cost more friction than they save.

  2. **Concrete endpoints are stable on day one.** We chose
     `v1` from the start, not `v0` and not unversioned
     (`/api/...`). Without a version in the path, the first
     breaking change after launch forces a v1 retro-name —
     which breaks every existing client of the old
     `/api/...` URL anyway. Start versioned, stay versioned.

  3. **The repo is the source of truth.** The filesystem
     format evolves under its own versioning
     (TECH_SPEC §12). API versioning is about *wire format*
     only; these two version lines drift independently on
     purpose.

- **What counts as a breaking change (v1 → v2):**
  - Removing an endpoint.
  - Renaming a field in a response or a request body.
  - Changing a field's type (`str` → `int`).
  - Tightening validation (was optional, becomes required).
  - Changing the meaning of an existing success code (200
    used to mean "saved," now means "queued").
  - Changing the shape of an error envelope.

- **What does NOT count as breaking:**
  - Adding a new endpoint.
  - Adding a new optional field to a response.
  - Adding a new error code (clients that ignore unknown
    codes keep working).
  - Loosening validation (was required, becomes optional).
  - Performance work that doesn't change observable
    behavior.

- **How v2 lands:**
  - v1 endpoints are *not* deleted when v2 ships. We run v1
    and v2 in parallel for at least one release.
  - v1 gets bug fixes only — no new features land there.
  - Migration guides name each breaking change with a
    1-to-1 mapping to the v2 endpoint or field.
  - v1 is retired only after a documented grace period
    (target: 6 months) and only when v2 has parity on
    everything v1 had.

- **Alternatives Considered:**
  - **No version in URL.** Rejected: see (2) above.
  - **Header-based versioning.** Rejected: tooling cost
    without a real benefit; the only thing this would
    enable is serving multiple versions from one URL,
    which we can do via URL versioning anyway.
  - **Date-based versioning (`/api/2026-08-05/...`).**
    Rejected: noisy URLs and no useful semantics. The point
    of a version is "what contract?" not "what release?"
- **Consequences:**
  - The route prefix `/api/v1` is constant in
    `backend.api.v1.router`. Changing it is an architecture
    change, not a code change — it requires a new ADR.
  - A field rename in `NodeResponse` is a v1 → v2 event,
    not a same-version refactor. Same-version changes add
    *new* fields; they never *rename* an existing one.
  - OpenAPI documents are versioned by URL: `/openapi.json`
    is v1; v2 would live at a separate path
    (e.g. `/openapi-v2.json`) generated by a second router.

#### ADR-0011 — Index layer is intentionally decoupled from persistence (Phase 2.0)
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** The Postgres index is a **standalone
  subsystem** that depends only on the domain, database, and
  settings — and on nothing else. The service layer, the API
  layer, and the filesystem-tree repository do not depend on
  the index; the index does not depend on them either. Phase
  2.0 ships no integration at all between the index and any
  write path.
- **Rationale:** Three reasons compound:

  1. **Drop-the-DB safety.** Phase 2.0 must satisfy the
     brief: "dropping every database table must not lose user
     data." That's only true if no production code path
     *requires* the database to complete. Decoupling now is
     what makes that property a structural invariant — not
     a discipline to remember.

  2. **Different evolutionary pressures.** The filesystem
     repository has to reason about corruption, ordering,
     and atomic writes. The index has to reason about
     SQL queries, btree layout, and rebuild cost. Each
     layer's tests are different; each layer's failure modes
     are different. A shared abstraction between them
     would force both to grow at the same pace.

  3. **Future synchronisation is a separate problem.** When
     (not if) we wire write-path side-channel updates into
     the index, the right shape is a **reconciler**: a
     process that listens for filesystem events and batches
     index writes. That reconciler will sit *above* both
     repositories and depend on both — it will not be either
     one. Building the boundary now keeps that option open.

  Enforced by `backend/tests/index/test_isolation.py`. Both
  directions are checked: every import in an index module must
  appear on a curated allowlist, and no file under
  `backend/api/`, `backend/services/`, or
  `backend/repositories/` may reference any index symbol.

- **Alternatives Considered:**
  - **Index as a second source of truth** (write to both
    repositories from the service). Rejected: doubles the
    failure modes (what if they disagree?) and breaks
    "drop-the-DB safety." Filesystem is the source of
    truth.
  - **Index as a SQL-backed fast read layer called from the
    service.** Rejected for now: the service must remain
    testable against an in-memory fake. Wiring the service
    to SQL means every service test boots Postgres.
    When the read paths need real latency gains (Phase 4+),
    a service-level read-cache can be added *behind* the
    WorkspaceRepository Protocol — not in it.
  - **Same package as the filesystem repository.** Rejected:
    the index is *projection* of disk; lumping them
    together would imply they have the same correctness
    contract, which they don't (one is durable, one is
    derivable).
- **Consequences:**
  - `backend.index` and `backend.repositories` are siblings,
    not parent/child. The dependency graph stays a DAG.
  - Phase 3+ will introduce a `Reconciler` (a new module
    that imports both index and filesystem repository). That
    imports the index — but the index stays unaware of the
    reconciler.
  - The Alembic migration lives in `backend/alembic/versions/`
    even though nothing in the runtime path uses Alembic at
    import time. This is deliberate: schema is decoupled from
    code so a DBA can review the DDL without importing the
    Python app.
  - Adding a new queryable field is a two-step change: model
    + migration. (Adding a *not-queryable* derived field,
    like `search_text`, only takes the model — but the
    column needs a Postgres-side default so old rows still
    load. The first migration here establishes that pattern.)
  - The isolation tests will fail loudly the day someone
    reaches from `backend.services` to `backend.index`. The
    failure is the deliverable: it forces the import into
    code review where the trade-off (and there is one)
    becomes explicit.

#### ADR-0012 — Rebuild is implemented before incremental synchronisation (Phase 2.1)
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** Phase 2.1 ships a **full** rebuild
  Reconciler only. No incremental indexing, no event
  hooks, no write-path side-channels, no background
  workers. Incremental sync lands in a later phase, after
  we have proven the architectural invariant (the index
  can be dropped and rebuilt) at least once end-to-end.
- **Rationale:** Three reasons compound:

  1. **Architectural invariant first.** "Drop the index
     and rebuild from disk without losing user data" is
     the load-bearing property. Until it has been
     *exercised*, every guarantee derived from it is
     hypothetical. A working rebuild proves:
        - the projection (`Node → IndexRecord`) is sound,
        - `replace_all` is atomic in the SQL and in-memory
          implementations,
        - the dependency inversion holds (index →
          WorkspaceRepository Protocol, never → concrete
          filesystem),
        - determinism survives every ordering accident the
          filesystem can produce.
     These four properties are the foundation of every
     later "the index is consistent" claim. They have to
     exist before the synchroniser — otherwise we'd build
     a sync layer that *can't* be repaired by rebuild.

  2. **Reconciler is the right abstraction regardless.**
     Even with incremental indexing, the full rebuild is
     the safety net — every "the index drifted" recovery
     path is just "drop and rebuild." Building it now is
     not throwaway work; it's the same code that runs
     during cleanup operations forever after.

  3. **Incremental sync is harder to design correctly.**
     Incremental indexing requires us to know:
        - when the filesystem changes (an FS watcher, with
          all its edge cases),
        - what changed (content hashing, mtime races,
          atomic-write detection),
        - which writes need to be queued vs. applied
          synchronously,
        - how to recover from a queue backlog or worker
          crash.

     Each of those is its own design problem with subtle
     ordering traps. Trying to solve them *and* the basic
     "the index is rebuildable" question in the same
     phase is the kind of two-front war that produces
     neither feature correctly. Doing them in sequence —
     basic rebuild first, incremental later — is the
     cheaper path to a robust system.

- **Why the rebuild is deterministic (not just full):**

  Determinism is enforced by:

    - Traversing the Tree sorted by `node_id` lexically.
      Sorting isolates the rowset from filesystem walk
      order — a feature the WorkspaceRepository's natural
      `Tree` iteration cannot guarantee on its own.
    - Using a single shared `now` per rebuild for all
      `updated_at` values. A second run produces the
      same rowset except for `updated_at`.
    - Projecting via a pure function
      `(tree, node, path) → IndexRecord`. No I/O during
      projection.
    - Replacing the index atomically through
      `IndexRepository.replace_all`, which holds a
      transaction across `truncate + insert_many`.

  These choices earn their keep: rebuilding twice on the
  same workspace produces the same rowset, which makes
  "did anything change?" trivial to answer with `count()`
  plus an id-by-id diff.

- **Failure semantics are explicit, not convenient:**

  Three kinds of failures exist, each handled distinctly:

    - `load_tree` failure — surface, abort, no records
      emitted. The index is untouched.
    - Path-lookup failure — abort before
      `replace_all`. We never silently drop a Node from
      the index; that's data loss.
    - `replace_all` failure — the repository rolls back;
      the Reconciler surfaces the error in `errors`. The
      index is untouched.

  The brief was explicit ("explicit failure behavior for
  corrupt repository state") and the Reconciler honours
  it: one rebuild is either a fully-committed new index
  or none at all.

- **Alternatives Considered:**
  - **Build incremental sync now.** Rejected: see (3)
    above. The first attempt at incremental would
    almost certainly need a "drop and rebuild"
    recovery path anyway; building the rebuild first
    means that path is *itself* exercised in CI.
  - **Build the rebuild as a one-shot CLI tool, not a
    Python module.** Rejected: a CLI is a *caller* of
    the rebuild. Production needs programmatic
    reconciliation too (boot, drift recovery, manual
    admin endpoints). Shipping only a CLI forces a
    rewrite when those needs land.
  - **Skip the rebuild entirely until search endpoints
    need it.** Rejected: "drop and rebuild" is the
    invariant. Without a tested rebuild, every later
    claim about index correctness is unbacked.

- **Consequences:**
  - `IndexReconciler.rebuild()` is the *only* way the
    index ever gets populated in Phase 2.1. No write path
    touches the index. Every Phase 2.1 test that ends
    with "the index has these rows" runs through
    `rebuild()`.
  - The Protocol gained `replace_all` and
    `all_node_ids`. Both are simple; both are explicitly
    justified by the rebuild. Future phases may add
    `add_listener` / `record_event` when sync lands; no
    reasoning in Phase 2.1 anticipates that shape.
  - `ReconcileReport` is consumed today only by
    logs/tests. When admin endpoints land (later
    phase), they'll surface the same shape. The
    contract is therefore load-bearing for the whole
    reconciliation pipeline, not just the current
    caller.
  - The boot-time reconcile (Phase 3) will be a one-
    liner in `lifespan`: `IndexReconciler(...).rebuild()
    in best-effort try/except`. Building it now means
    that wiring lands without refactoring the rebuild
    path.
  - If incremental sync is ever proposed as a
    *replacement* for rebuild (rather than an addition),
    this ADR should be revisited before adopting that
    direction. Rebuild is structurally simpler and
    must remain available as a recovery tool.

## 13b. Synchroniser (Phase 2.2 — Incremental Sync)

The Synchroniser is the live counterpart to the
Reconciler. Where the Reconciler rebuilds the index from
scratch, the Synchroniser updates it one mutation at a time.
Both share the same domain objects and the same
`IndexRepository` Protocol — but they sit at opposite ends
of the consistency spectrum.

### What the Synchroniser is

```
        WorkspaceRepository write
        ─────────────────────────
                 │
                 ▼  (after fs success)
        IncrementalIndexSynchronizer
                 │
                 ├─ on_node_created   → upsert 1 row
                 ├─ on_node_renamed   → upsert (title only)
                 ├─ on_node_moved     → upsert subtree
                 ├─ on_node_deleted   → delete 1 row
                 ├─ on_metadata_updated → upsert (title)
                 └─ on_canvas_updated → upsert (touched_at)
                 │
                 ▼
            IndexRepository
```

`IncrementalIndexSynchronizer` is constructed with **three
Protocol dependencies**:

    IncrementalIndexSynchronizer(
        index_repo:      IndexRepository,
        tree_provider:   WorkspaceTreeProvider,
        path_provider:   FilesystemPathProvider,
    )

No Filesystem. No API. No Services. No ORM models.
`backend.index.sync` depends only on `backend.domain`,
`backend.index.protocol`, and `backend.index.types` — same
allowlist as the Reconciler (Phase 2.1).

### Sequence: a `move_node` flow

```
   service          LocalWorkspaceRepository        fs              sync
   ──────           ────────────────────────        ──             ────
     │                       │                       │               │
     │ move_node(id, parent) │                       │               │
     │ ────────────────────► │                       │               │
     │                       │ move_node             │               │
     │                       │ ────────────────────► │               │
     │                       │ ◄──────────────────── │               │
     │                       │ on_node_moved(id,p)                    │
     │                       │ ─────────────────────────────────────► │
     │                       │                                    subtree →
     │                       │                                    upsert each
     │                       │ ◄──────────────────────────────────── │
     │ ◄──────────────────── │                                       │
     │  Node (moved)         │                                       │
```

### Stale-index concept

A new invariant: **the index is not necessarily
consistent.** Until proven otherwise, every read against
the index may return stale data.

The Synchroniser flips an in-process flag on every
failure. Reads that care about freshness can call
`synchroniser.is_stale()` and route to the filesystem
instead. The flag is reset by a successful rebuild.

This is not a problem we hide — it is an invariant we
document. The filesystem is the source of truth; the
index is a queryable cache; the gap between them is
acceptable for the foreseeable future, and observable
through `is_stale()` for callers that need it.

### Failure semantics

The brief: "Any index failure must NEVER roll back
filesystem persistence."

Two layers enforce this:

    ┌─────────────────────────────────────────────┐
    │ LocalWorkspaceRepository._invoke_sync       │  swallows +
    │  (defence in depth — even if a future        │  logs
    │   maintainer regresses the sync impl)        │
    └─────────────────────────────────────────────┘
                          │
                          ▼
    ┌─────────────────────────────────────────────┐
    │ IncrementalIndexSynchronizer._run           │  swallows +
    │  (catches everything inside the sync pass,  │  flips flag
    │   records errors in SyncReport)             │
    └─────────────────────────────────────────────┘

A failure never propagates past the repository boundary.
The user sees their filesystem operation succeed; the
index may have drifted; the staleness flag says so.

### Subtree move correctness

Moving a Node under a different Story must re-root the
entire moved subtree's `story_id`. `on_node_moved`
walks `tree.subtree(node_id)` (the Node + every
descendant), resolves each path via the
`FilesystemPathProvider`, and upserts each row. The
projection uses the *current* `parent_id` from the
Tree (which has already been rewired by the filesystem
write), so descendants inherit the new root
automatically.

Tests `test_on_node_moved_updates_subtree_paths_and_story_ids`
and `test_on_node_moved_to_root_drops_story_id_to_self`
cover both common cases (move under a different Story,
move to root).

### Repository contract

The Repository (`LocalWorkspaceRepository`) gained an
optional `sync` constructor argument:

    LocalWorkspaceRepository(fs, sync=incremental_sync)

When provided:

    - `save_node`     → `on_node_created`
    - `rename_node`   → `on_node_renamed`
    - `move_node`     → `on_node_moved` (subtree)
    - `delete_node`   → `on_node_deleted` (per Node, deepest first)
    - `write_canvas`  → `on_canvas_updated`

When None (the default for pre-Phase-2.2 tests), the
repository behaves exactly as it did before.

### What the Synchroniser deliberately does NOT do

- **No background workers.** Synchronous, in the same call frame as the write.
- **No event hooks.** No pub/sub, no message bus.
- **No retry queue.** Failures are logged + flagged; recovery is the rebuild.
- **No cross-process coordination.** Staleness is per-process.
- **No full rebuild.** The Reconciler owns that.
- **No persistence for the staleness flag.** Persisting "we know we're stale" is pointless; the rebuild inspects the index itself.

### Architectural rule

The Synchroniser's isolation is enforced by
`backend/tests/index/test_isolation.py` —
`test_sync_does_not_reach_concrete_repositories` mirrors
the Phase 2.1 Reconciler test. Two guarantees:

1. **The Synchroniser may import Protocols, not
   concretes.** It pulls `IndexRepository` from
   `backend.index.protocol` and uses local `Protocol`
   declarations for `FilesystemPathProvider` and
   `WorkspaceTreeProvider` (to avoid coupling to the
   Reconciler's modules). Reaching for a concrete
   class triggers an isolation failure.

2. **The repository → index seam is permitted, but
   nothing else.** Only `LocalWorkspaceRepository` may
   reference the synchroniser. API endpoints, services,
   other repositories, and the protocol package stay
   decoupled from the index layer.

#### ADR-0013 — Synchronous incremental sync precedes an event-driven architecture (Phase 2.2)
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** Phase 2.2 ships a **synchronous, in-process**
  incremental synchroniser. No message bus, no background
  worker, no event hook. Repository writes invoke the
  synchroniser directly, after the filesystem call
  succeeds.
- **Rationale:** Four reasons compound:

  1. **Index consistency is local, not distributed.**
     The Synchroniser is in the same process as the
     repository. There is no second machine whose view
     of "what changed" we need to capture. A function
     call from the repository is the right primitive.
     A message bus would add network failure modes,
     at-least-once delivery semantics, and idempotency
     keys for problems we don't have yet.

  2. **Sync failure must not roll back the filesystem.**
     The brief is explicit. The simplest implementation
     that satisfies this is "the synchroniser catches its
     own exceptions; the repository catches anything
     that escapes." A background worker would invert
     this: the repository fires-and-forgets, the worker
     is the only thing that knows about failures, and
     "fire-and-forget" leaks the failure surface into
     the rest of the system.

  3. **Synchronous makes the order obvious.** The
     repository writes the filesystem, then invokes
     the synchroniser. The dependency direction is
     exactly what ADR-0006 prescribes: the repository
     orchestrates; the persistence layers are passive.
     A queue would invert this: the synchroniser would
     have to *ask* for events, and "what events exist"
     becomes its own design problem.

  4. **Reconciler already handles drift.** If the
     synchroniser ever drops a write or misorders a
     row, the index drifts. The Reconciler (Phase 2.1)
     rebuilds from disk in one transaction. A
     queue-backed sync layer would *also* need a
     rebuild safety net — we're not gaining reliability
     by adding one, we're adding two paths that have
     to agree.

- **Why this is replaceable:**

  The Synchroniser's contract is `IncrementalIndexSynchronizer`,
  injected into `LocalWorkspaceRepository.__init__`.
  A future Phase 4 implementation could swap in a
  queue-backed variant (`QueueBackedIndexSynchronizer`)
  that publishes to a worker pool without changing
  the repository's interface. The brief explicitly
  calls this out: "Keep the synchroniser completely
  replaceable via protocol injection."

  But the *current* sync impl is in-process because:

    - the failure surface is small (one Postgres session),
    - the failure recovery is well-defined (rebuild),
    - the ordering is guaranteed by the call frame,
    - and the cost is one DB round-trip per write.

  Each of these is a different problem a queue has to
  solve; we'd be paying five infrastructure costs for
  one feature.

- **Failure mode: stale index, not rollback.**

  The Synchroniser's documented failure mode is
  **staleness**, not rollback. A failed sync leaves the
  filesystem authoritative; the index may be one or more
  writes behind; the staleness flag tells callers.

  This is not a bug. It is the system explicitly
  acknowledging the index's role: a queryable cache,
  rebuildable on demand, not a transactional mirror of
  disk.

- **Alternatives Considered:**
  - **Event bus now.** Rejected: see (1)–(4) above.
    The first attempt at queue-based sync would
    almost certainly need the same rebuild safety
    net; building it now means we have two paths
    that have to agree.
  - **Write-path index update inline (in the
    repository).** Rejected: the brief explicitly
    distinguishes the Synchroniser from the
    repository. Putting the projection logic inside
    the repository couples it to the index layer's
    concerns; today the repository only knows
    "the synchroniser has hooks" — which is the
    minimum surface needed to honour the seam.
  - **Trigger-based (Postgres triggers on the
    filesystem metadata DB).** Rejected: the index
    is the *only* Postgres artefact in Phase 2.2;
    the filesystem has no DB to attach triggers to.
    Phase 4+ might revisit.

- **Consequences:**
  - `IncrementalIndexSynchronizer.is_stale()` is the
    public staleness signal. Production callers (Phase
    3+) can route reads through it: stale → filesystem,
    fresh → index.
  - `LocalWorkspaceRepository.sync=None` is the
    default. Existing tests that predate Phase 2.2
    continue to pass without modification — the
    optional argument is the entire migration.
  - `SyncReport` is consumed today by tests and logs.
    Future admin endpoints (Phase 3+) will surface
    the same shape.
  - The "stale index" concept is documented in
    §13b above and codified in the `is_stale()`
    API. Future phases inherit the invariant; they
    cannot accidentally turn "stale" into "inconsistent"
    without an ADR.
  - If event-driven sync is ever adopted (Phase 4+),
    this ADR should be revisited. The Synchroniser
    contract stays; only the implementation changes.
    Today's code is *not* throwaway work — the contract
    is the API the queue-backed variant will implement.

#### ADR-0015 — Startup is a single-owner subsystem with a four-state outcome (Phase 2.3 follow-up)
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** Two architectural changes ship together:
  1. **Single owner.** Application startup is owned by
     `StartupSubsystem` (in `backend.core.startup_subsystem`).
     The FastAPI lifespan is a thin caller that invokes
     `StartupSubsystem.run()` once and stashes the result on
     `app.state`. No other code path constructs the indexing
     stack or decides to rebuild.
  2. **Four-state outcome.** `StartupOutcome` is widened
     from three values to four: `HEALTHY`, `RECOVERING`,
     `DEGRADED`, `FAILED`. `RECOVERING` is the label for any
     boot where a rebuild was required AND succeeded.
- **Rationale:**

    - **Single owner prevents drift.** Before this ADR, the
      Phase 2.3 `StartupCoordinator` was invoked from the
      lifespan directly, but the lifespan also built the
      filesystem, the repository, and the synchroniser.
      That's two places reasoning about "what is startup".
      Consolidating into one subsystem class makes the
      question "where do I add X to bootstrap?" trivially
      answerable: there is exactly one `run()` method.

    - **Single owner is enforced by code review, not by a
      structural test.** The structural test for the index
      layer still forbids concrete-class imports from the
      index package; that's the right boundary for the
      *index* but it's not the right boundary for *startup*
      (which is allowed to import concretes — it's the seam
      where they get wired). The subsystem's contract is
      verified by `backend/tests/core/test_startup_subsystem.py`,
      which checks that the subsystem carries every
      collaborator and that `run()` is non-mutating.

    - **`RECOVERING` matches the failure shapes we have.**
      A successful rebuild is not the same as "no rebuild
      needed" — the rebuild is evidence that something was
      wrong, and the system may want to drain or warm caches
      before declaring itself fully healthy. Folding this
      into `HEALTHY` would conflate "always healthy" with
      "healthy after recovery", which makes downstream
      decisions (e.g., "should I show a 'rebuilding'
      banner?") harder. The fourth state gives the boot a
      place to land between "fine" and "broken".

    - **`RECOVERING` is promotion-eligible, not final.**
      A future Phase 4+ verification step (read back a
      random sample of rebuilt rows; confirm the index row
      count matches the workspace tree) can promote
      `RECOVERING` → `HEALTHY`. The promotion is additive —
      it does not change the four-state classification
      logic.

- **Alternatives considered:**

    - **Three-state outcome (HEALTHY / DEGRADED / FAILED)
      as in the original brief.** Rejected: loses the
      distinction between "no rebuild needed" and "rebuild
      succeeded". A future Phase 4+ banner or admin UI
      would need to re-derive this from `rebuild_attempted`
      on every page load, which is brittle.

    - **Two-state outcome (HEALTHY / DEGRADED), with
      `FAILED` folded into `DEGRADED`.** Rejected:
      `FAILED` is a startup-raise condition; `DEGRADED`
      means "started but degraded". Folding them together
      means a missing workspace directory and a missing
      Postgres row both look the same to operators, which
      is misleading.

    - **Each subsystem owns its own bootstrap.** Rejected:
      the brief explicitly requires "exactly one place in
      the codebase that owns startup orchestration". Each
      subsystem owning its own bootstrap would force every
      collaborator to know the order; that's the opposite
      of the dependency direction we want (collaborators
      below the subsystem, not above it).

- **Consequences:**

    - The lifespan module is short on purpose. Any new
      bootstrap logic belongs in `StartupSubsystem` — not
      in the lifespan, not in the coordinator, not in any
      service or endpoint.

    - `StartupReport.is_recovering` is a new property. Callers
      branching on outcome should handle four cases, not
      three. The brief's failure classification table is
      updated accordingly (§13c above).

    - Tests for the coordinator (`backend/tests/index/test_startup.py`)
      and the subsystem (`backend/tests/core/test_startup_subsystem.py`)
      cover all four outcomes.

    - If a future Phase adds a fifth state (e.g.,
      `PROVISIONING` while waiting for first-run admin
      setup), this ADR should be revisited and the
      classification table updated; today the four-state
      shape is the contract.

#### ADR-0015a — Separation of responsibilities: Coordinator / Reconciler / Synchronizer (Phase 2.3 review note)
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** The three index-side components have **distinct,
  non-overlapping** responsibilities. They must never be merged
  and they must never acquire each other's responsibilities.

      StartupCoordinator  — decides WHAT should happen at boot.
                            Owns: ordering, decision tree, the
                            StartupReport.

      IndexReconciler     — knows HOW to rebuild the index from
                            disk. Owns: walking the tree, diffing
                            against the existing index, writing
                            the replacement.

      IncrementalSync     — keeps the index current during runtime.
      (Synchronizer)        Owns: per-write hooks, subtree-move
                            re-projection, the staleness flag.

- **Rationale:**

    - **The Coordinator does not know how to rebuild.** It
      only knows the decision tree (index_unavailable /
      is_stale / count == 0 → rebuild). Moving the rebuild
      logic into the Coordinator would couple boot ordering
      to filesystem walking, which is the wrong direction.

    - **The Reconciler does not know when to rebuild.** It
      only knows the rebuild algorithm. Moving the decision
      logic into the Reconciler would mean every place that
      calls `rebuild()` (today: just the Coordinator) has
      to re-derive the same decision. That re-derivation
      is where bugs hide.

    - **The Synchronizer does not know either decision.**
      It only knows "this write happened; update the index
      accordingly". Putting either decision in the
      Synchronizer would either duplicate the Coordinator's
      decision tree or duplicate the Reconciler's walk.

- **Consequences:**

    - A future Phase that wants online reconciliation (a
      scheduler that periodically calls rebuild) does NOT
      add scheduling logic to the Reconciler. It adds a
      new component that uses the Coordinator's decision
      tree (or a subset of it) to decide WHEN to call
      the Reconciler.

    - A future Phase that wants richer per-write hooks
      (analytics, audit log, notifications) does NOT add
      hooks to the Synchronizer. Per the original
      Phase 2.2 brief: if additional side effects appear,
      introduce a proper event dispatcher rather than
      adding more repository hooks.

    - Tests that exercise the decision tree live in
      `backend/tests/index/test_startup.py`. Tests that
      exercise the rebuild walk live in
      `backend/tests/index/test_reconciler.py`. Tests that
      exercise the per-write hooks live in
      `backend/tests/index/test_sync.py`. Keeping them
      separate is the practical enforcement of this ADR.

### Numbering

ADRs use a simple sequential 4-digit format:

- `ADR-0001`, `ADR-0002`, `ADR-0003`, …
- Never renumber an existing ADR.
- When an ADR is superseded, its replacement gets a new number and the
  old entry links to the new one via `Supersedes:` / `Superseded by:`.

---

## 13c. Startup Bootstrap (Phase 2.3 — Bootstrapping & Startup Reconciliation)

The `StartupSubsystem` (in `backend.core.startup_subsystem`)
is the **single owner** of application bootstrap. It owns:

    - Construction of every collaborator (filesystem,
      workspace repository with the Phase 2.2 synchroniser
      seam, index repository, reconciler, coordinator).
    - The deterministic startup sequence (see below).
    - The decision to rebuild.
    - Exposing the resulting `StartupReport` to the
      application.

The FastAPI lifespan (`backend.core.lifespan`) is a thin
caller — it invokes `StartupSubsystem.run()` once and
stashes the result on `app.state`. The lifespan itself does
not construct, decide, or classify; doing so elsewhere would
violate the "single owner" rule documented below.

### Deterministic startup sequence

    1. Load configuration.
    2. Validate workspace root (filesystem reachable).
    3. Initialise repository layer (workspace repo wired
       with the synchroniser seam).
    4. Initialise index layer (lazy — never raises here).
    5. Determine index health/state (probe `count()`).
    6. Decide whether reconciliation is required
       (index_unavailable / sync.is_stale() / index_empty).
    7. Execute reconciliation if necessary.
    8. Expose final startup state to the application
       (StartupReport + collaborators on `app.state`).

Each step is logged with a structured event so the boot
trail is reproducible from logs alone.

### Four-state classification

| Outcome     | When                                                            |
|-------------|-----------------------------------------------------------------|
| HEALTHY     | No rebuild needed; index is healthy                              |
| RECOVERING  | Rebuild attempted AND succeeded                                  |
| DEGRADED    | Rebuild attempted AND failed, OR index unreachable               |
| FAILED      | Filesystem unreachable (coordinator re-raised)                   |

`RECOVERING` exists because a successful rebuild still
deserves a transitional state — the app may want to
drain pending requests, surface a banner, or warm caches
before declaring itself fully healthy. A future Phase 4+
verification step can promote `RECOVERING` → `HEALTHY`
once it confirms the rebuilt index.

### What the subsystem deliberately does NOT do

- **No background workers.** Single synchronous pass.
- **No automatic retries.** A failed rebuild is logged; the
  next boot is the recovery path.
- **No scheduling.** Boot-time only.
- **No periodic health checks.** Boot-time only.
- **No request-time decision-making.** Components in the
  runtime path (services, API endpoints) must never trigger
  reconciliation themselves — that's the single-owner rule.
- **No search endpoints.** Out of scope for Phase 2.3.

### Architectural rule: single owner of startup

There must be **exactly one place** in the codebase that
constructs the indexing stack and decides whether to
rebuild. That place is `StartupSubsystem.run()`.

This rule is enforced by code review, not by a structural
test (the structural test for the index package still
forbids concrete-class imports from the index layer). The
test for the subsystem itself
(`backend/tests/core/test_startup_subsystem.py`) verifies
that:

    - The subsystem is a frozen dataclass with every
      collaborator on it (no lazy globals).
    - `run()` returns a NEW subsystem with the report
      populated — the original is left for forensic
      comparison.
    - The subsystem carries the `StartupReport`; the
      properties (`is_healthy`, `is_recovering`,
      `is_degraded`) read from it consistently.

### Legacy two-/three-state section preserved below

The original Phase 2.3 brief requested three outcomes
(HEALTHY / DEGRADED / FAILED). The follow-up brief
expanded this to four with `RECOVERING`. The
`StartupCoordinator` (inside the subsystem) still
implements the three-state decision logic; the fourth
state (`RECOVERING`) is the standard success label for
any boot where a rebuild was needed.

### Sequence: a healthy boot

```
Lifespan                  Coordinator                 Filesystem        Index           Reconciler       Synchroniser
   │                          │                          │               │                  │                │
   │ coordinator.run()        │                          │               │                  │                │
   ├─────────────────────────>│                          │               │                  │                │
   │                          │ fs.root                  │               │                  │                │
   │                          ├─────────────────────────>│               │                  │                │
   │                          │<── WorkspaceRoot ───────│               │                  │                │
   │                          │ index.count()            │               │                  │                │
   │                          ├───────────────────────────┬─────────────>│                  │                │
   │                          │<────── 0 rows ────────────┴──────────────│                  │                │
   │                          │ sync.is_stale()                            │                  │                │
   │                          ├──────────────────────────────────────────────────────────────┼───────────────>│
   │                          │<────── false ──────────────────────────────────────────────┼────────────────│
   │                          │ rebuild decision: True (index_empty)       │                  │                │
   │                          │ rebuild()                                  │                  │                │
   │                          ├────────────────────────────────────────────┼─────────────────>│                │
   │                          │<────── ReconcileReport (ok) ──────────────┼──────────────────│                │
   │                          │ sync.clear_staleness()                     │                  │                │
   │                          ├──────────────────────────────────────────────────────────────┼───────────────>│
   │                          │ return StartupReport(outcome=HEALTHY)     │                  │                │
   │<── StartupReport ───────│                          │               │                  │                │
   │ app.state.startup_report│                          │               │                  │                │
```

### Sequence: index unavailable (degraded mode)

```
   │                          │ index.count()            │               │                  │                │
   │                          ├───────────────────────────┬─────────────>│                  │                │
   │                          │<──── ConnectionError ─────┴──────────────│                  │                │
   │                          │ log warning, set index_unavailable=True   │                  │                │
   │                          │ rebuild decision: True (index_unavailable)│                  │                │
   │                          │ rebuild()                                  │                  │                │
   │                          ├────────────────────────────────────────────┼─────────────────>│                │
   │                          │<──── ReconcileReport (ok) ─────────────────┼──────────────────│                │
   │                          │ return StartupReport(outcome=DEGRADED)    │                  │                │
```

### Sequence: filesystem unavailable (startup fails)

```
   │                          │ fs.root                  │               │                  │                │
   │                          ├─────────────────────────>│               │                  │                │
   │                          │<──── PermissionError ────│               │                  │                │
   │                          │ log.exception, RE-RAISE                   │                  │                │
   │<── raises ───────────────│                          │               │                  │                │
```

### Architectural rule

The `StartupCoordinator` depends ONLY on Protocols:

- `FilesystemLike` — local Protocol for the `root` accessor.
- `WorkspaceRepository` — from `backend.repositories.protocol`.
- `IndexRepository` — from `backend.index.protocol`.
- `IndexReconciler` (the concrete class is constructed by the
  lifespan; the coordinator only sees its `.rebuild()` method).
- `_SyncFlagProbe` — local Protocol for `is_stale()` /
  `clear_staleness()`.

The `lifespan` (`backend/core/lifespan.py`) is the *only* place
where concrete classes are wired together. The coordinator
itself never imports `Filesystem`, `LocalWorkspaceRepository`,
or `SQLAlchemyIndexRepository`. This is enforced by the
isolation test `backend/tests/index/test_isolation.py`
(`test_index_module_only_imports_allowed_backends` already
covers `backend.index.startup`).

### What the StartupCoordinator deliberately does NOT do

- **No background workers.** Single synchronous pass.
- **No automatic retries.** A failed rebuild is logged; the
  next boot is the recovery path.
- **No scheduling.** Boot-time only.
- **No periodic health checks.** Boot-time only.
- **No search endpoints.** Out of scope for Phase 2.3.

These omissions are intentional. A future Phase 4+ that needs
any of them should introduce them as separate components (a
`HealthMonitor`, an `IndexRebuildScheduler`, etc.) rather than
expand the StartupCoordinator's responsibilities.

---

#### ADR-0014 — Reconciliation occurs during startup, not on a timer (Phase 2.3)
- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** The full `IndexReconciler` runs at application
  startup. There is no background scheduler, no periodic
  reconciliation, and no automatic retry. The decision tree is:

      index_unavailable → rebuild (best-effort)
      sync.is_stale()   → rebuild
      index empty       → rebuild
      otherwise         → skip

  On success, the synchroniser's staleness flag is cleared.
- **Rationale:** Five reasons compound:

    1. **The reconciler is expensive but cheap enough to be a
       one-shot.** A single-user workspace has tens to low
       hundreds of Nodes; the rebuild walks the filesystem and
       does a `replace_all` SQL transaction. At that scale the
       rebuild runs in milliseconds. Putting it on a timer adds
       operational complexity (a watchdog, a jitter policy, an
       admin endpoint to inspect queue depth) for no observable
       benefit.

    2. **The synchroniser keeps drift tiny between boots.**
       Phase 2.2's incremental path catches every write on the
       hot path. Drift accumulates only across *crashes* — i.e.
       an OS kill, a power loss, a Postgres outage mid-write.
       Those happen at boot anyway. Tying the rebuild to boot
       means the recovery cost is paid by the process that
       benefits from it.

    3. **Boot-time is the only moment when the system can
       decide honestly whether it has the prerequisites.**
       At boot we know whether the workspace is reachable,
       whether the index row count is zero, and whether the
       synchroniser tripped during the *previous* run. All
       three signals are in-process state. A background
       scheduler would have to re-derive them and would be
       racing the very writes it is trying to reconcile.

    4. **Three-state classification fits the failure shapes
       we have.** Filesystem unavailable → startup fails
       (no disk, no service). Index unavailable → degraded
       (we can serve most reads from filesystem metadata; the
       index, when reachable, augments). Rebuild failed →
       degraded (the filesystem is authoritative; a failed
       rebuild leaves the index empty or partial, which is the
       same observable state as a fresh install). No fourth
       state is needed.

    5. **Degraded mode is observable.** A future `/readyz`
       endpoint (or admin UI badge) can read
       `app.state.startup_report.outcome` and surface it. A
       timer-driven rebuild hides behind a worker whose state
       is harder to inspect.

- **Alternatives considered:**

    - **Background scheduler that reconciles every N minutes.**
      Rejected: adds a worker, a timer, a queue, and an admin
      surface; pays a constant CPU cost for an event that only
      matters at boot.

    - **Reconciliation on every write (synchronous path).**
      Rejected: Phase 2.2 already does this for the common
      case via the Synchroniser. Adding the *full* reconciler
      to the write path duplicates work and breaks the
      "filesystem never rolls back" invariant — the reconciler
      re-reads the filesystem and could disagree with the
      synchroniser mid-flight.

    - **Trigger reconciliation on a `/healthz` request.**
      Rejected: makes a health check have side effects, which
      is the wrong shape for a health check. Health checks
      should answer "is this process up?" not "did the last
      write succeed?".

    - **Skip startup reconciliation entirely; rely on the
      Synchroniser alone.** Rejected: defeats the point of
      Phase 2.2's staleness flag. The flag exists *because*
      there are cases where the Synchroniser can't catch up
      (a crash mid-write, a Postgres outage). Without a
      rebuild path the flag becomes a permanent degraded
      state.

- **Consequences:**

    - The `StartupCoordinator` is intentionally small — one
      pass, four stages, three outcome states. Anything new
      (per-tenant bootstrapping, dry-run mode, stronger
      health checks) would be additive fields on
      `StartupReport`, not changes to the run shape.

    - The lifespan is the only place that constructs concrete
      classes; the coordinator sees only Protocols. The
      isolation test suite enforces this.

    - If a future Phase 4+ adds online reconciliation
      (e.g. a periodic catch-up worker), this ADR should be
      revisited. Today's code is *not* throwaway — the
      `StartupReport` is the API the online variant would
      also produce (one `ReconcileReport` per pass).

    - The coordinator never blocks a request. Boot-time
      only. If boot gets slow because the workspace grows,
      the answer is to fix the reconciler (incremental
      checkpointing, parallel scans), not to schedule it
      later.

---

## 13d. Runtime Workspace Cache (Phase 3.0)

The **runtime workspace cache** is the in-process mirror of the
domain tree that services every read at O(1) after startup. The
cache lives at `backend.workspace` and is the only structural
change to the runtime model between Phase 2.3 and Phase 3.

### What the cache is

A `Tree` reference plus a parallel `dict[NodeId, Node]` for
constant-time lookup. Both are populated exactly once at boot
(by `StartupSubsystem.run()`), then mutated by the repository
on every write via the invalidation API.

The cache is a **runtime optimisation, never a source of truth**.
Per ADR-0016:

    - The filesystem is the only source of truth.
    - The cache may be discarded and rebuilt at any time
      without data loss.
    - The cache exists to optimise latency only, never
      durability or correctness.

If a process loses its cache (hot reload, manual clear, OOM),
the next read falls through to disk via the repository's
self-healing miss path. No read request fails because the
cache is empty or stale.

### Three-way Protocol split

The cache exposes three Protocols in `backend.workspace.protocol`:

    WorkspaceCache        — read-only; the rest of the codebase
                            sees only this surface (or nothing).
    MutableWorkspaceCache — extends read with invalidate /
                            invalidate_many / subtree_ids / clear.
                            Only the repository implementation
                            imports this.
    CacheSeeder           — startup-only populate() API. Only
                            StartupSubsystem may import this.

Why three Protocols (per ChatGPT's Phase 3.0 review):

    - The service layer must never mutate cache state.
      Splitting `invalidate` into a separate Protocol
      makes "the service layer can't accidentally invalidate"
      a structural guarantee, not a code-review hope.
    - `populate()` is a once-per-boot operation. Putting
      it on the read Protocol would let any consumer
      repopulate; putting it on a separate Protocol
      restricts it to the subsystem at the type level.
    - All three are `@runtime_checkable` Protocols — future
      implementations (distributed cache, shared memory,
      LRU-sharded) can satisfy them structurally without
      inheritance.

### Mutation order: fs → cache → sync

Every repository write follows a deterministic three-step
order (per ChatGPT's Phase 3.0 refinement #5):

    1. Filesystem persistence (source of truth). On
       success, the operation is committed.
    2. Cache invalidation (best-effort). The repository
       catches every cache exception in a single guard
       (`_invalidate_cache`); on failure it logs a
       structured warning with `operation`, `node_ids`,
       `error_type`, `error_message`, and `fs_was_committed`
       flags and continues.
    3. Index synchronisation hook (best-effort). Same
       swallow pattern as step 2.

Failure semantics mirror the existing index-sync philosophy:
filesystem persistence is authoritative; everything else is
best-effort with structured observability. A cache failure
after a successful fs write does NOT roll back; the
self-healing read path will repopulate the cache from disk
on the next miss.

### Cache and index are independent subsystems

The cache maintains its OWN `_dirty` flag (independent of the
synchroniser's `is_stale` flag, per ChatGPT's refinement
"cache and index are independent subsystems"). The flag is
set True on construction and after `clear()`; cleared by a
successful `populate()`. The repository never reads the
flag — reads self-heal — but the startup subsystem and ops
diagnostics may consult it.

The synchroniser's `WorkspaceTreeProvider` Protocol is now
satisfied by `CacheBackedTreeProvider(cache)`, replacing the
earlier `ponytail:`-marked back-reference
(`synchroniser._tree_provider = repository`). The
synchroniser reads through the cache; the cache is the only
place that holds the live `Tree`.

### Canvas is not cached

The cache deliberately covers only Node attributes (title,
type, parent_id, children_ids, metadata). Canvas content is
not cached because:

    - The cache is a Node mirror; canvas is a separate file.
    - Canvas content is unbounded (markdown blobs,
      potentially megabytes per Node).
    - Canvas reads have an RPC-style on-demand pattern that
      would balloon the cache for negligible latency benefit.

`read_canvas` and `write_canvas` continue to hit the
filesystem directly.

### Self-healing reads

When the cache returns `CacheConsistencyError` (an id was
invalidated, or never present in the populated tree), the
repository's read method:

    1. Catches the exception.
    2. Logs a structured warning
       (`repository.cache_miss_self_heal`) with
       `operation`, `node_id`, `error_type`,
       `error_message`.
    3. Falls through to the filesystem.
    4. Returns the disk-read Node to the caller.

The caller never sees the cache miss. From the service
layer's perspective, `load_node` is just "get the Node."

Self-healing reads are an *expected* degraded path (per
ChatGPT's refinement #3). They increment the cache's
`misses` counter so an operator can spot a misconfigured
cache via `cache.stats()`.

### Startup ordering with the cache

`StartupSubsystem.run()` executes the deterministic
sequence with the cache populate step slotted in:

    1. Load configuration.
    2. Validate workspace root (filesystem reachable).
    3. Initialise repository layer.
    4. Initialise index layer (lazy).
    5. Determine index health (probe count()).
    6. Decide whether reconciliation is required.
    7. Execute reconciliation if necessary.
    8. **Populate the cache** from `repository.load_tree()`.
       The populate call runs once, is idempotent on
       success, and runs best-effort — a populate failure
       degrades startup (every read goes to disk) but does
       not fail startup. The `cache_populate_seconds`
       field on `StartupReport` records the elapsed time;
       a populate failure appends to `warnings`.
    9. Expose final state to the application.

A `CacheNotInitializedError` raised during step 8 is a
programming error (the cache should have been populated
before any runtime path is reachable). A
`CacheNotInitializedError` raised during runtime (step 9+
in the request path) is treated as a warning — the
self-healing miss path handles it.

### Structural isolation rules

The cache lives in its own package, `backend.workspace`.
The isolation tests in `backend/tests/workspace/test_isolation.py`
enforce:

    - `backend.workspace` may only import `backend.domain`,
      `backend.core.logging`, and intra-package symbols.
      It must not reach filesystem / repositories / index /
      api / services / config.
    - Only construction sites (`backend.core.lifespan`,
      `backend.core.startup_subsystem`, and the repository
      implementation itself) may import the concrete
      `InMemoryWorkspaceCache` class. API, services, and
      index must depend on Protocols only.
    - Only `LocalWorkspaceRepository` may *mutate* cache
      state at runtime (call `.invalidate(`,
      `.invalidate_many(`, `.subtree_ids(`).
    - Only `StartupSubsystem.run()` may call
      `cache.populate()`.

These rules are the practical enforcement of the
single-mutation-boundary rule (ADR-0016 / ChatGPT
refinement #6).

### Sequence: a hot read after warmup

```
[Service]   load_node("node-123")
   │
   ▼
[Repository] _cache.is_loaded() → True
   │
   ▼
[Cache]   load_node("node-123")
   │
   ▼
[Repository]   return cached Node (O(1))
```

Zero disk reads. The cache's `hits` counter increments.

### Sequence: a self-healing miss

```
[Service]   load_node("node-123")
   │
   ▼
[Repository]   _cache.load_node → CacheConsistencyError
   │            (logged: repository.cache_miss_self_heal)
   ▼
[Filesystem]  load_node("node-123")
   │
   ▼
[Repository]   return disk Node; cache.stats.misses += 1
```

The caller never sees the cache miss.

### Sequence: a write that invalidates

```
[Service]   move_node(node_id, new_parent)
   │
   ▼
[Repository]
   ├─ fs.move_node(…)           # 1. filesystem (committed)
   ├─ ids = cache.subtree_ids(node_id)
   ├─ cache.invalidate_many(ids) # 2. cache (best-effort)
   └─ sync.on_node_moved(…)      # 3. sync (best-effort)
```

If step 2 raises, the repository catches it, logs a warning,
and continues. Step 3 still runs. The disk state is unchanged.

#### ADR-0016 — Runtime cache is repository-owned; synchroniser reads through it (Phase 3.0)

- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** The runtime workspace cache is a
  **repository-owned runtime optimisation that exists to
  optimise latency only — never durability or correctness**.
  It is not a source of truth, not a durability layer, not
  a correctness guarantee. The filesystem remains the
  single source of truth; the cache may be discarded and
  rebuilt at any time without data loss.

  Three architectural rules:

      1. **Single mutation boundary.** Only
         `LocalWorkspaceRepository` may mutate cache state
         at runtime. The repository's writes follow the
         deterministic order: filesystem → cache → sync.
         Cache mutations are best-effort; a cache failure
         after a successful filesystem write logs a
         structured warning and continues (the read path
         will self-heal).

      2. **Single populate site.** Only
         `StartupSubsystem.run()` may call `populate()`.
         The cache is populated exactly once at boot.

      3. **Independent subsystems.** The cache and the
         index are independent subsystems. The cache has
         its own `_dirty` flag, never shared with the
         synchroniser's `is_stale` flag. The synchroniser
         reads the cache through `CacheBackedTreeProvider`;
         the cache does not know the index exists.

- **Rationale:**

    - **Latency, not durability.** Today's every-read-
      goes-to-disk model is O(N) per `GET` request, which
      is the primary latency bottleneck. The cache
      eliminates that. It does NOT provide a durability
      guarantee the filesystem doesn't already provide,
      and it does NOT replace the index's correctness
      role. Treating it as anything more is over-engineering
      that risks data loss (e.g., a future engineer trusting
      the cache as the source of truth instead of the disk).

    - **One mutation boundary.** A single seam means
      invariant reasoning is local: "every change to the
      cache flows through the repository." Splitting the
      seam across multiple files means invariants are
      distributed and bugs hide in the gaps.

    - **Three Protocols.** `WorkspaceCache` (read-only),
      `MutableWorkspaceCache` (invalidate), `CacheSeeder`
      (populate) make "the service layer can't mutate the
      cache" a type-level guarantee, not a code-review
      hope. The Protocol split also makes a future
      distributed implementation drop-in: a Redis-backed
      cache satisfies the same Protocols structurally.

    - **Cache and index are independent.** Sharing a
      health flag would couple two subsystems that have
      nothing in common: one mirrors the on-disk Tree, the
      other mirrors the Postgres index. A cache failure
      should not flag the index as stale; an index failure
      should not flag the cache as dirty. The independent
      flags make each subsystem's health independently
      observable in production.

- **Consequences:**

    - **Cache and disk divergence is observable but
      benign.** A `cache.invalidate(id)` followed by a
      fs-read returns the on-disk Node; the cache will
      repopulate on the next read miss. The divergence
      is logged as a warning (`repository.cache_miss_self_heal`)
      so ops can spot patterns (e.g., a hot-reload loop
      repeatedly wiping the cache).

    - **A future distributed cache implementation is a
      drop-in.** A Redis-backed cache that satisfies the
      same three Protocols replaces `InMemoryWorkspaceCache`
      in `backend.core.lifespan` without touching the
      repository, services, or API. Phase 3.0 does not
      implement this; the Protocols permit it.

    - **A future cache eviction policy is independent
      from correctness.** Phase 3.0 holds every Node for
      the worker's lifetime. A future LRU eviction would
      invalidate by reference count or recency; the
      repository's self-healing miss path makes eviction
      safe (the disk read fills the gap).

    - **Per-worker cache assumption.** The cache is
      in-process; multiple workers see multiple cache
      instances. The filesystem is the cross-worker
      consistency boundary. A future distributed cache
      would change this assumption; the Protocols already
      support that swap.

    - **Tests live in `backend/tests/workspace/`.** Unit
      tests for `InMemoryWorkspaceCache` live in
      `test_in_memory_cache.py`. Isolation tests
      enforcing the single-mutation-boundary rule live
      in `test_isolation.py`. Integration tests with a
      real workspace + real cache live in
      `test_repository_integration.py`. Disk-read
      assertions live in
      `test_disk_reads_after_warmup.py`. Threading
      tests live in `test_threading.py`.

    - **No automatic re-population on write failure.**
      Per the brief: self-healing is read-driven
      (miss → fs read → cache re-population on the
      next populate cycle). The startup subsystem
      may be invoked manually (in a future Phase) to
      force a full re-populate.

---

#### ADR-0017 — Runtime cache is an optimization, never an authority (Phase 3.0 close-out)

- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** The runtime workspace cache is
  **authoritatively disposable**. The cache may be lost,
  cleared, corrupted, or never populated — and the
  application must continue to operate correctly. The
  filesystem is the only authority; the cache is a
  performance optimisation layered on top.

  Three concrete rules:

      1. **Every cache entry is reproducible from the
         repository/filesystem.** A `cache.load_node(id)` miss
         followed by a `LocalWorkspaceRepository.load_node(id)`
         returns the same `Node` value (modulo the
         filesystem-driven fields). The cache is a
         memoisation, not a derivation.

      2. **Cache loss must never cause data loss.** A
         `cache.clear()` (or any other full-state wipe) leaves
         the on-disk workspace untouched. Reads slow down
         (self-heal from disk on miss) but no Node is missing
         from any return value. The repository's read path is
         cache-first with self-healing miss, so partial cache
         loss is invisible to the API.

      3. **Cache corruption must only affect performance —
         never correctness.** A cache that holds a stale or
         partially-invalid state (e.g., a `Node` whose title
         was renamed but the cache wasn't invalidated) can
         only cause a wrong read on a hit. The hit returns a
         stale Node; the next reset/populate converges. The
         repository's self-healing miss path guarantees that
         a hit followed by a write converges on the next
         read of the same id.

- **Rationale:**

    - **Why this ADR exists separately from ADR-0016.**
      ADR-0016 establishes the architectural *position*
      (cache is repository-owned, never authoritative).
      ADR-0017 establishes the *operational contract* — the
      observable behaviour when the cache fails or disappears.
      Together they form the full guarantee: position +
      behavioural evidence.

    - **Reproducibility is the proof.** Any cache entry
      must be derivable from the filesystem. If you can
      discard the cache and rebuild it from `repository.load_tree()`
      without losing data, the cache is a memoisation. If
      you can't, the cache has become a derivation — and
      derivations need their own consistency story.

    - **Cache loss must be free.** The application must
      survive `cache.clear()` being called at any moment
      (the test suite does this after every test).
      Anything that can't survive that has implicitly
      become a dependency.

    - **Corruption is bounded by time + writes.** A stale
      cache entry is wrong until the next write to that
      id (or until the next full reset). Because the
      repository invalidates on every write, the staleness
      window is bounded by the gap between writes.

- **Consequences:**

    - **Cache is disposable.** The startup subsystem can
      `cache.clear()` and re-populate at any time without
      coordinating with callers. The lifespan wires a
      fresh cache on every boot — no carry-over between
      processes.

    - **No cache without a fallback.** Every read path
      that goes through the cache has a corresponding
      disk-read fallback (the repository's self-healing
      miss path). The cache is an *acceleration* of that
      read, not a substitute.

    - **No cache-derived state elsewhere.** The
      synchroniser does not depend on the cache's
      internal dict; the index does not depend on the
      cache's tree. The cache is a *consumer* of the
      repository, not a peer.

    - **Test proof.** `test_disk_reads_after_warmup.py`
      proves the cache-miss path returns correct data.
      `test_cache_failure_does_not_roll_back_filesystem`
      proves the cache-failure path leaves the filesystem
      intact. `test_threading.py` proves concurrent
      reads while a write invalidates produce no torn
      state.

    - **Multi-process consistency is the filesystem's
      job.** Two workers with two caches see the same
      disk. The cache is per-worker; the filesystem is
      the cross-worker authority. A future distributed
      cache would change this and would need its own
      ADR (call it ADR-0019 when it lands).

---

## 13e. Search Layer (Phase 3.1)

### What it is

The search layer is a query interface over the
PostgreSQL-backed index. It is a clean
abstraction; it is not yet full-text search. The
exposed surface is a single `SearchRequest`/`SearchResult`
round-trip with:

    - **exact title lookup** (case-insensitive equality),
    - **prefix lookup** (case-insensitive `startswith`),
    - **filter by node_type, parent_id, story_id**,
    - **sort by `updated_at` (asc/desc) or `title` (asc/desc)**,
    - **pagination** (page + page_size, clamped).

A single `SearchRequest` carries the filter
parameters; the service picks the strategy
internally (exact / prefix / list) based on which
fields are set. Title and prefix are mutually
exclusive.

### Layering

    ┌─────────────────┐
    │  API (Phase 3.2)│  ← not yet wired
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │ SearchService   │  ← Phase 3.1
    │  (Protocol)     │
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │ IndexRepository │  ← Phase 2.0
    │  (Protocol)     │
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │ InMemory / SQL  │  ← concrete impls
    └─────────────────┘

The search layer depends ONLY on `IndexRepository`.
It does NOT depend on:

    - `WorkspaceRepository` (no filesystem writes),
    - the workspace cache (search is independent),
    - any I/O library directly (no SQLAlchemy /
      FastAPI / pathlib).

The boundary is enforced by `test_isolation.py`
in `backend/tests/search/`.

### Eventually consistent

`SearchService` reads the index. The index lags the
filesystem by the synchroniser's flush cadence
(Phase 2.2). A write that has not yet been
synchronised will not appear in search results.

This is documented as a feature, not a bug:
search results reflect "what is durable enough to
have been indexed," which is exactly what users
expect from a search box. The staleness is bounded
by the synchroniser's flush frequency and is
observable via `IndexRecord.updated_at`.

### Search semantics

    - Title match: case-insensitive equality. The
      index doesn't currently expose a per-title
      lookup method (Phase 3.2 will add one); the
      service uses a full scan + filter. This is the
      Phase 3.1 ceiling; the SQL index will be
      materially faster.
    - Prefix match: case-insensitive `startswith`.
      Same full-scan caveat.
    - Empty title/prefix → invalid query.
    - Page size > 200 → invalid query.
    - Negative page → invalid query.
    - Filters AND together; sort applies after
      filtering, before pagination.

### Test surface

`backend/tests/search/` has:

    - `test_search_service.py` — 35 tests covering
      validation, exact/prefix/list, all filters,
      all sort orders, pagination, immutability,
      Protocol satisfaction, and the
      "no filesystem access" claim.
    - `test_isolation.py` — 12 structural tests
      enforcing the dependency boundary.

### Benchmarks (in-memory index, ceiling numbers)

Measured with `InMemoryIndexRepository` (the SQL
index will be at-or-below):

| Operation | 100 nodes | 1,000 nodes | 5,000 nodes |
|---|---|---|---|
| Exact title lookup | 17 µs | 130 µs | (skipped, RUN_LARGE_BENCH=1) |
| Prefix lookup (`Node-0001`) | 22 µs | 160 µs | (skipped) |
| Paginated list (page_size=50) | 32 µs | 170 µs | (skipped) |
| Filter by node_type (1000 nodes, 500 matches) | n/a | 159 µs | n/a |

The 1000-node numbers are realistic for a developer
workspace; the SQL implementation will be lower
because the index will route through Postgres
queries instead of in-process dict walks.

### Files added

    - `backend/search/__init__.py`
    - `backend/search/exceptions.py`
    - `backend/search/types.py`
    - `backend/search/protocol.py`
    - `backend/search/service.py`
    - `backend/tests/search/__init__.py`
    - `backend/tests/search/test_search_service.py`
    - `backend/tests/search/test_isolation.py`
    - `backend/tests/benchmarks/test_search_benchmark.py`

No other files modified. The search layer is
additive — it does not touch the repository, the
workspace cache, the services, or the index
implementations.

---

#### ADR-0018 — Search is built exclusively on the index, not the workspace tree (Phase 3.1)

- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** The search subsystem (`backend.search`)
  depends only on the `IndexRepository` Protocol. It
  does NOT depend on the workspace tree, the
  workspace cache, the filesystem, or any tree
  traversal. Search is a query over a derived
  projection; the search latency is bounded by the
  index, not the workspace size.

  Three concrete rules:

      1. **Search depends only on `IndexRepository`.**
         The `SearchService` constructor takes one
         parameter: `index: IndexRepository`. No
         `WorkspaceRepository`, no `WorkspaceCache`, no
         filesystem object. The Isolation test
         (`test_isolation.py`) fails if a future
         change adds a second dependency.

      2. **Search is eventually consistent by design.**
         The index lags the filesystem by the
         synchroniser's flush cadence. A write that
         hasn't been synchronised will not appear in
         search. Operators monitoring search quality
         should look at `IndexRecord.updated_at` and
         the synchroniser's `SyncReport`.

      3. **Search is add-only.** The search service
         never writes to the index. The index is
         updated by the synchroniser (Phase 2.2) and
         the reconciler (Phase 2.1) — search is a
         *consumer* of the index, not a peer.

- **Rationale:**

    - **Why search on the index, not the tree.**
      Three reasons:

         1. **Latency.** Walking the workspace tree
            for every search query is O(N) on the
            disk. The index is a flat projection
            that's O(log n) on a B-tree (Postgres
            primary key) and O(1) on a hash (in-memory
            test fake). Search latency stops scaling
            with workspace size.

         2. **Consistency.** The index is a *view*
            of the workspace. Search running on the
            index is the same view that the rest of
            the application's read path sees. A
            search that walks the tree would see
            in-flight writes; a search on the index
            sees the consistent, committed state.

         3. **Decoupling.** Search on the tree would
            require the search layer to import the
            workspace tree — coupling the two
            subsystems. Search on the index lets the
            search layer depend on a narrow Protocol
            (`IndexRepository`) that has zero
            knowledge of the workspace tree.

    - **Why a Protocol, not a concrete class.**
      Mirrors the rationale for `WorkspaceRepository`
      and `IndexRepository`: future implementations
      (Elasticsearch, Meilisearch, a dedicated
      search database) drop in by satisfying the
      Protocol. The API layer (when it lands in
      Phase 3.2) depends on the Protocol, not the
      concrete `DefaultSearchService`.

    - **Why one search method, not three.**
      A single `search(request)` with optional
      fields keeps the API surface one-shaped. The
      service validates the request and picks the
      strategy internally. Future extensions
      (autocomplete, faceted counts) land as new
      methods on the Protocol, not as new fields
      on `SearchRequest`.

- **Consequences:**

    - **Search is independent of the workspace
      cache.** Cache failures do not affect search;
      search failures do not affect the cache. The
      two subsystems are siblings under the
      application layer.

    - **Adding a per-query repo method is a Phase
      3.2 concern.** Once `IndexRepository.find_by_title`
      and `find_by_prefix` land, the service will
      route the title/prefix paths through them
      instead of the in-process scan. The
      `SearchService` interface doesn't change.

    - **Full-text search is a Phase 4 concern.**
      The `IndexRecord.search_text` field exists
      (reserved, currently empty) for tsvector
      integration. Phase 3.1 leaves it untouched.

    - **No HTTP endpoints in Phase 3.1.** The
      search layer is internal. The API layer
      (Phase 3.2) will add endpoints that wrap
      `SearchRequest` → Pydantic DTO and stream
      `SearchResult` → Pydantic response.

    - **Tests cover the "no filesystem" claim.**
      `test_search_does_not_consult_filesystem` in
      `test_search_service.py` proves the search
      returns records that exist ONLY in the index,
      with no filesystem backing. The isolation
      test enforces the import boundary statically.

    - **Benchmarks at 1000 nodes are the realistic
      ceiling.** 130 µs (exact title) / 160 µs
      (prefix) / 170 µs (paginated) on the
      in-memory index. The SQL implementation will
      be lower; the in-memory numbers are the upper
      bound for a per-call cost.

---

#### ADR-0019 — Search as a Query Boundary (Phase 3.1 close-out)

- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** The search subsystem is a **pure query
  boundary**. It is held to the Command-Query Separation
  principle: every operation on `SearchService` is a
  query (read-only); no operation mutates state. The
  correctness of search results is defined by the
  index, not the filesystem.

  Three concrete rules:

      1. **CQS — no mutations.** `SearchService.search`
         returns a `SearchResult` and raises nothing
         that mutates state. There is no `upsert`,
         `delete`, `cache`, or `warmup` method on the
         Protocol. Future extensions (autocomplete,
         faceted counts) MUST add new query methods,
         never mutate the index.

      2. **Search correctness is defined by the index.**
         The filesystem may be ahead of the index by
         the synchroniser's flush cadence. A search
         result that omits a freshly-written node is
         *correct* (the index hasn't caught up yet),
         not stale. The boundary is: the index is
         authoritative for search; the filesystem is
         authoritative for everything else.

      3. **Search is replaceable via `IndexRepository`.**
         A future search backend (SQLite FTS, PG
         tsvector, Tantivy, Meilisearch) replaces the
         `IndexRepository` implementation — not the
         `SearchService`. The Protocol surface is the
         contract; the backend is downstream.

- **Rationale:**

    - **Why CQS matters here.** Mixing reads and
      writes in a single service is the most common
      cause of "search accidentally indexed a node"
      bugs. The split — search is read-only, write
      paths go through the synchroniser — keeps the
      invariant local: "if I called `SearchService`,
      I didn't change anything." That's a useful
      invariant for reasoning, debugging, and
      reproducing.

    - **Why the index is the source of truth for
      search.** The index is a projection of the
      filesystem. The filesystem is the source of
      truth for *what exists*; the index is the
      source of truth for *what's searchable*. The
      difference is the synchroniser's flush cadence.
      Bypassing the index ("search the filesystem
      directly to get fresh results") couples the
      search layer to the disk and breaks the
      eventual-consistency model.

    - **Why replaceable via IndexRepository.** The
      service depends on the Protocol, not the
      implementation. A new search backend is a
      satisfying impl of `IndexRepository` (or
      possibly a new Protocol layered on top of
      it, for full-text-specific queries). The
      `SearchService` doesn't change. The API
      endpoints (Phase 3.2) don't change. The
      tests don't change. This is the same
      pattern as `WorkspaceRepository` →
      `LocalWorkspaceRepository` (Phase 1.3).

    - **Why no "fallback" reads.** A tempting
      shortcut when a record isn't in the index is
      "fall back to the workspace tree." This
      breaks the boundary: it makes the search
      layer see in-flight writes, leaves the
      synchroniser's correctness story unclear
      (does the index need to be eventually
      consistent if search bypasses it?), and
      breaks the test for "no filesystem access
      during search." The rule is: search reads
      the index, full stop.

- **Consequences:**

    - **Endpoint behaviour.** `GET /api/v1/search`
      (Phase 3.2) is a pure read. The HTTP
      semantics are `200 OK` with a possibly-empty
      `hits` array, never `404`. A query for an
      absent node is a valid result.

    - **No mutation methods on `SearchService`.**
      The Protocol's only method is `search`. We
      don't add `index_document`, `create_record`,
      `delete_record`, etc. Writes go through the
      repository / synchroniser / reconciler.

    - **Future search backends are
      `IndexRepository` swaps.** The boundary
      between the search layer and the index
      layer is the Protocol. When a future
      backend is selected, the change is local
      to `backend.index.impl` (a new
      `MeilisearchIndexRepository` or similar).
      The search layer, the API layer, and the
      tests do not change.

    - **Search is a sibling of the cache, not
      a child.** The cache (Phase 3.0) and the
      search service (Phase 3.1) are both
      consumers of the index / repository. They
      don't know about each other. Replacing the
      cache backend doesn't affect search;
      replacing the index backend doesn't affect
      the cache.

    - **The architecture is layered cleanly:**
      API → SearchService → IndexRepository →
      DB. Each edge is a Protocol; each layer
      is replaceable in isolation.

---

#### ADR-0020 — End-to-end verification is an architectural requirement (Phase 3.2 close-out)

- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** A phase is NOT complete until its externally
  observable behavior has been exercised against the real
  running backend (real Postgres, real filesystem, real
  runtime cache, real startup subsystem, real synchroniser,
  real search index) via `scripts/verify_backend.sh` (or its
  per-phase equivalent). Unit tests, structural isolation
  tests, and TestClient integration tests are necessary but
  not sufficient.
- **Rationale:** The Phase 3.2 close-out found five real
  defects that none of the existing test layers would have
  caught:

      A. Per-request repository construction bypassed the
         lifespan wiring (synchroniser/cache dropped).
      B. Filesystem `NodeNotFoundOnDiskError` escaped the
         repository on mutation paths.
      C. PATCH /metadata routed through `rename_node`,
         silently dropping the new metadata (data loss).
      D. Pydantic 422 returned FastAPI default envelope
         instead of the service's stable envelope.
      E. Stale `.pyc` masked otherwise-correct code.

  Every one of these is a **wiring-level defect**: the
  unit tests passed, the isolation tests passed, the
  TestClient integration tests passed. The defects only
  manifested when the real FastAPI process booted, the
  real lifespan ran, the real Postgres connected, and a real
  HTTP request hit a real handler. The conclusion: backend
  correctness has a phase dimension that no unit-level test
  can see. That dimension must be exercised as part of
  the definition of done.

  Aligns with Architectural Principle §4.9 ("domain first,
  adapters second") by treating the **adapter wiring**
  (lifespan → repository → cache → synchroniser → index →
  Postgres) as a first-class correctness concern, not a
  deployment detail.
- **Alternatives Considered:**
  - **Rely on TestClient + DI overrides alone.** Rejected —
    TestClient doesn't run the lifespan the same way the
    production uvicorn process does (e.g., it shares the
    test thread), so lifespan-ordering bugs and process-level
    state never surface. The Phase 3.2 pass is the proof.
  - **Add an in-process smoke test that boots the lifespan
    inside pytest.** Rejected — it short-circuits the same
    wiring (pytest's `TestClient(app).__enter__()` still
    uses the same in-process lifespan). The bug must
    surface in a real subprocess, not a real function call.
  - **Treat verification as a one-off QA activity.** Rejected
    — the bugs found during Phase 3.2 were caught by the
    process itself, not by an out-of-band audit. Future
    contributors would re-introduce them under the same
    one-off framing. Codifying the harness as part of every
    phase's Definition of Done is the only durable answer.
  - **Add load / chaos testing instead.** Rejected for this
    phase — load tests measure throughput, not correctness.
    They are a future concern once the correctness gate is
    green. The two are complementary, not substitutable.
- **Consequences:**
  - Every phase must include a `scripts/verify_phase_X.sh`
    (or extend `verify_backend.sh`) that boots the real
    backend and exercises every changed endpoint, both
    success and failure paths.
  - The pytest suite is the **fast feedback loop** (seconds);
    the verification script is the **release-candidate gate**
    (minutes). Both must be green before merge.
  - Bugs found during verification are **fixed-then-tested**:
    a regression test is added to pytest BEFORE the fix, the
    test fails on the broken code, the fix is applied, the
    test passes. This loop is what makes the bugs stick
    across future refactors.
  - The verification harness is treated as production code:
    changes to it go through the same review process as
    changes to the runtime. The harness is the project's
    executable specification of "what a working backend
    looks like."
- **Supersedes:** None.

---

## 13f. Live Verification (Phase 3.2 milestone)

### Why this section exists

Phases 1.0 through 3.1 followed the standard backend-discipline
workflow: structural isolation tests, unit tests, API integration
tests with `TestClient`, an extensive suite of parametrized
contracts. The Phase 3.2 close-out exposed the gap: a TestClient
plus a faked repository can pass everything green while the
real backend is broken in five independent ways.

This section captures the **end-to-end verification philosophy**
adopted as a permanent architectural requirement (see ADR-0020)
and describes the reusable harness that operationalizes it.

### The verification philosophy

> *Architecture complete ≠ backend validated.*

Unit tests prove the building blocks are correct. They do NOT
prove that the building blocks are wired correctly when they
boot a real process. The Phase 3.2 validation pass surfaced:

    1. Synchroniser dropped (per-request repository construction
       bypassed the lifespan wiring — every request used
       `sync=None`).
    2. Filesystem exceptions escaping the repository (no
       FS → domain translation on mutation paths).
    3. Silent metadata data loss (PATCH /metadata routed through
       `rename_node`, which used `with_title()` and dropped the
       new metadata).
    4. Pydantic 422s returning FastAPI's default `{"detail": [...]}`
       instead of the stable service-error envelope.
    5. Stale `.pyc` masking otherwise-correct code from a
       preceding fix.

None of these would have been caught by structural isolation
tests, by TestClient-based integration tests, or by
unit tests on the `LocalWorkspaceRepository`. They are all
**wiring-level** defects: problems that only surface when the
real FastAPI process boots, runs the real lifespan, serves real
HTTP, and operates against the real Postgres + filesystem +
runtime cache + synchroniser + reconciler + index.

The verification philosophy is therefore:

    **Every externally observable behavior of the backend
    must be exercised against the real running process before
    a phase is considered done.**

This is a permanent project rule (see ADR-0020). It applies
to every phase that adds, changes, or removes an HTTP endpoint,
an error envelope, an integration with the index, an integration
with the runtime cache, or an integration with the startup
subsystem.

### The verification harness

`scripts/verify_backend.sh` is the reusable harness. It:

    1. **Resets state.** Confirms Postgres is reachable at
       `127.0.0.1:5433` (ptt/ptt/ptt), wipes
       `data/verify_workspace/`, truncates the `node_index` table,
       and prepares `data/verify_evidence/` for capture.

    2. **Boots the real backend** on a dedicated port (18000)
       via `.venv/bin/uvicorn backend.main:app` — the same
       entry point production uses. No special test-mode flag.

    3. **Waits for /readyz** (the lifespan's `startup.complete`
       event) before running any requests.

    4. **Captures startup logs** to
       `data/verify_evidence/01_startup.log` for inspection.

    5. **Runs the endpoint matrix** — every public endpoint,
       success path + failure path, with field-level assertions
       (not just status codes). On failure, the captured
       request/response bodies are written to
       `data/verify_evidence/last_<METHOD>_<PATH>.json` so
       diagnosis is one `cat` away.

    6. **Restarts the backend mid-run** to exercise restart
       persistence (workspace + index survive a process
       restart), captures the second boot log to
       `02_restart_startup.log`.

    7. **Reports the pass/fail count** with `RESULT: N/M checks
       passed` and writes the full report to
       `data/verify_evidence/REPORT.md`.

The script exits non-zero on any failure. CI can therefore
gate on it directly.

### What "exercised" means

A bug is **NOT** fixed until:

    - The harness reproduces it on the broken code (proves the
      harness can catch this class of defect).
    - The fix is applied.
    - The harness passes cleanly (proves the fix works).
    - A regression test is added to `pytest` (so a future
      refactor can't reintroduce the bug without breaking CI).

The Phase 3.2 validation pass added 18 regression tests to
`backend/tests/api/test_verify_pass_regressions.py` covering
the five bugs above. The pytest count went from 378/6 to
396/6 — every regression test is named after the bug it pins.

### What is NOT in scope (deliberate)

    - **Load testing.** The harness verifies correctness, not
      throughput. Load tests are a separate concern (ADR-0020
      §Alternatives Considered).
    - **Frontend integration.** The harness exercises the HTTP
      API; the frontend is out of scope for the backend
      validation gate.
    - **Multi-worker race conditions.** The harness runs a
      single backend process. Multi-worker invariants are
      covered by the cache's existing threading tests
      (`backend/tests/workspace/test_threading.py`).
    - **External integrations.** No third-party services
      (email, file storage) are exercised — the project has
      none yet.

### Definition of Done (mandatory from Phase 3.2 onward)

A phase is NOT done until **all** of the following are true:

    1. **Architecture complete.** The architectural
       decisions are recorded (ADRs), the layering is
       enforced (structural isolation tests), and the
       invariants are honoured (§15).
    2. **Tests complete.** The pytest suite is green
       (including any new tests the phase requires).
    3. **Live backend verification complete.**
       `scripts/verify_backend.sh` (or the next-phase
       equivalent) reports all checks passing against a
       freshly booted real backend. No mocked, in-memory,
       or TestClient shortcuts.
    4. **Regression tests added for every bug fixed.** A
       bug found during verification gets a pytest test
       named after the bug it pins. The pytest count
       must therefore GROW with each phase that finds
       defects.
    5. **Verification script updated.** If the phase adds
       endpoints or changes envelopes, the script grows
       the corresponding assertions. The harness must
       keep pace with the API surface.
    6. **Evidence captured.** Startup logs, request/response
       bodies, and the report (`REPORT.md`) are written to
       `data/verify_evidence/`. The evidence is the
       deliverable — the script's pass/fail exit code is a
       summary, the captured artefacts are the proof.
    7. **No new warnings or tracebacks.** A clean boot
       (`01_startup.log`) must show zero Traceback blocks
       and zero `level="error"` log entries.

A phase missing any of these is incomplete and cannot be
merged.

### Living this rule

Future contributors: when you start a phase, copy
`scripts/verify_backend.sh` as `scripts/verify_phase_X.sh`,
add your phase's endpoint matrix to it, and make it the last
thing you run before declaring done. When you find a bug,
add a regression test (item 4) before fixing it — write
the failing test, see it fail, then fix the code, then see
it pass. That loop is what makes the bugs stick.

---

## 19. Code Review Checklist (Appendix)

Before merging any change, verify:

**Invariants**
- [ ] Does this violate any Architectural Invariant (§15)? If yes, it is
      an architecture change, not a code change — discuss first.
- [ ] Does the filesystem remain the only source of truth?
- [ ] Is Postgres still completely rebuildable from disk?
- [ ] Does the frontend still avoid touching the filesystem directly?
- [ ] Does every mutation still flow through the service layer?
- [ ] Is the Node's UUID preserved across the change?

**Write ordering**
- [ ] Does every write follow
      `API → Service → Filesystem → Cache invalidation → Postgres sync`?
      (Phase 3.0: cache sits between filesystem and sync.
       Cache failure must NEVER roll back the filesystem.)
- [ ] Is the disk write atomic (temp + rename) where applicable?
- [ ] Is Postgres reconciliation handled (best-effort + rescan path)?
- [ ] If a runtime cache mutation was added, was the single-mutation-
      boundary rule preserved (only the repository mutates cache state
      at runtime)? See ADR-0016.

**Architecture fit**
- [ ] Is business logic in the service layer, not in routes?
- [ ] Is the change traceable to a section of this spec?
- [ ] Has `TECH_SPEC.md` been updated if an architectural decision
      changed?
- [ ] Does the change require a new ADR? If yes, add one.

**Quality**
- [ ] Are tests included where appropriate (logic, lifecycle, error path)?
- [ ] Are new dependencies justified (no "could be useful" additions)?
- [ ] Are structured logs emitted at the right boundaries?
- [ ] Is the diff the smallest one that solves the actual problem?

**Scope**
- [ ] Is anything in the Non-Goals list (§14) being added by accident?
- [ ] Is the Open Questions list (§16) being respected, not silently
      decided?

---

## 13g. Configuration Layer (Phase 3.3)

### Why this section exists

Configuration accumulated ad-hoc through Phases 1.0–3.2: hardcoded
workspace paths in the API layer, dead `HOST`/`PORT` fields in
Settings, `LOG_LEVEL` typos silently coerced to `INFO`, mutable
Settings with no validator, `POSTGRES_*` documented in `.env.example`
but unread by the app. The Phase 3.2 close-out (§13f, ADR-0020)
exposed how brittle this is: the verify harness caught five
wiring-level defects, and a future wiring defect in the
lifespan/config boundary would slip past unit tests exactly the
same way. Configuration is the next wiring boundary to harden.

This section captures the **configuration-as-typed-boundary**
philosophy that replaces the implicit-paths-and-env-passthrough
approach of earlier phases.

### The configuration layer in one picture

```
                  ┌────────────────────────────────────────┐
                  │ process.env + .env file (Pydantic)     │
                  └────────────────┬───────────────────────┘
                                   │
                                   ▼
                  ┌────────────────────────────────────────┐
                  │ backend.config.settings.Settings       │
                  │ (frozen=True, validated, immutable      │
                  │  after startup)                        │
                  │  - app_env, app_name, log_level        │
                  │  - workspace_path                       │
                  │  - database: DatabaseSettings           │
                  └────────────────┬───────────────────────┘
                                   │  via get_settings() (lru_cache)
                                   ▼
                  ┌────────────────────────────────────────┐
                  │ backend.core.lifespan                   │
                  │ (the SINGLE consumer at boot)           │
                  └─────┬──────────┬──────────┬─────────────┘
                        │          │          │
                        ▼          ▼          ▼
                 configure_   build_filesystem   create_app
                 logging()    (workspace_path)  app.state.X
                        │          │          │
                        ▼          ▼          ▼
                  ┌────────────────────────────────────────┐
                  │ backend.main:create_app                │
                  │ bootstrap entrypoint                   │
                  └────────────────┬───────────────────────┘
                                   │
                                   ▼
                  ┌────────────────────────────────────────┐
                  │ app.state.{repository, index_repo, ...} │
                  │ request handlers read app.state ONLY   │
                  └────────────────────────────────────────┘
```

### Configuration lifecycle (Environment → Settings → DI → Collaborators)

Configuration has four phases. A new contributor adding a config
key MUST walk through this diagram to place the value at the
right boundary:

```
    ┌──────────────┐      ┌──────────────┐     ┌──────────────┐    ┌──────────────────┐
    │ 1. ENV       │      │ 2. SETTINGS  │     │ 3. DI        │    │ 4. COLLABORATORS │
    │ Environment  │ ───▶ │ Settings     │ ──▶ │ Lifespan /   │ ─▶ │ Repository,      │
    │ (process +   │      │ (typed,      │     │ FastAPI deps │    │ Cache, FS,       │
    │  .env file)  │      │  frozen,     │     │  (DI / app.  │    │ Sync, Engine,    │
    │              │      │  validated)  │     │   state)     │    │ Logger           │
    └──────────────┘      └──────────────┘     └──────────────┘    └──────────────────┘
            │                                                               │
            │  production: Settings reads directly via BaseSettings          │
            │  requests: collaborators read app.state, NOT Settings          │
            └───────────────────────────────────────────────────────────────┘
```

What this enforces:

- **Step 1 → 2** happens once, at construction. Pydantic validates
  the field. Anything malformed raises here.
- **Step 2 → 3** happens once, at the lifespan. The lifespan wires
  slices of Settings into collaborators (filesystem path into
  `build_filesystem`, log level into `configure_logging`, database
  URL into the lazy engine constructor).
- **Step 3 → 4** happens at request time. Collaborators are
  constructor-injected or read from `app.state`. The request
  handler never calls `get_settings()`.

This is the only path. If you find yourself adding a new module
that reads `Settings()` from a request handler, you've broken the
diagram — refactor to read from `app.state` instead.

### Hot-reload policy (V1)

**Every setting is RESTART ONLY.** Construction-time validation is
the bound on misuse. Future phases MAY introduce a `reload_signal`
plumbing (SIGHUP handler + a per-subsystem `reload(settings)` method)
for fields that have a documented hot-reload path. None exist today.

| Setting | Reload model |
| --- | --- |
| `app_env` | RESTART ONLY — affects logging format + health endpoint output. Hot-reload would mean stale health responses. |
| `app_name` | RESTART ONLY — application identity. |
| `log_level` | RESTART ONLY (V1) — technically hot-reloadable via `logging.basicConfig` re-call, but the change would race with in-flight structured log lines. |
| `workspace_path` | RESTART ONLY — cache populated once at boot from this path; runtime change would orphan the cache. |
| `database_url` (or POSTGRES_*) | RESTART ONLY — engine constructed once; runtime swap requires connection-pool draining. |

Process-supervisor config (HOST, PORT) is intentionally OUT of scope
of `Settings`. See ADR-0021.

### Database precedence rule

```
DATABASE_URL set explicitly   → use it
DATABASE_URL unset, POSTGRES_* all set     → synthesise DATABASE_URL
DATABASE_URL unset, POSTGRES_* partial     → ConfigError at startup
DATABASE_URL unset, no POSTGRES_*          → use the default DATABASE_URL
```

Production deploys MUST set either DATABASE_URL or the full
POSTGRES_* set. The default is dev-only.

### The boundary the AST test enforces

`backend/tests/test_config_isolation.py` AST-walks `backend/` and
fails on `os.environ` / `os.getenv` / `os.getenvb` references
outside `backend/config/`. The allowlist covers:

    - `backend/config/settings.py`     (the configuration boundary)
    - `backend/config/database.py`     (DatabaseSettings sub-model)
    - `backend/database/session.py`    (lazy engine; runs before
                                        app.state exists)
    - `backend/alembic/env.py`         (standalone tool; not in
                                        the request path)

Tests/benchmarks (`RUN_LARGE_BENCH`) are excluded by directory —
they're gates for expensive benchmarks, not application config.

### Verification strategy

Per the standing rule from the Phase 3.2 close-out (ADR-0020):
every subsystem MUST include a Verification Strategy section.

Phase 3.3 verifies the configuration layer at four layers:

1. **Unit tests** (fast feedback):
   - `test_settings_freeze.py` — mutation raises `ValidationError`.
   - `test_log_level_validator.py` — `Literal[...]` accepts known
     levels, rejects unknown.
   - `test_database_settings.py` — POSTGRES_* → DATABASE_URL builder;
     explicit DATABASE_URL wins; partial POSTGRES_* raises; default
     falls back to dev DSN.
   - `test_workspace_path_from_settings.py` — `WorkspaceRoot` opens
     the settings-driven path; explicit kwarg wins.

2. **Structural tests** (architectural enforcement):
   - `test_config_isolation.py` — AST-walks backend/, fails on
     env-var reads outside `backend/config/`. Detector self-tests
     assert the walker recognizes `os.environ`, `os.getenv`, and
     `from os import environ / getenv`.
   - existing `tests/test_api_isolation.py` allowlist grows to
     include `backend.config.database` and `backend.config.types`.

3. **Integration tests** (TestClient — fast, mocked):
   - `tests/api/test_health.py` — health endpoint reflects
     `app_name` and `app_env` from settings.

4. **End-to-end live verification** (`scripts/verify_backend.sh`):
   - Boots real backend with `WORKSPACE_PATH=...`,
     `DATABASE_URL=...`, `LOG_LEVEL=INFO`, `APP_ENV=verify`,
     `--host`/`--port` via uvicorn CLI args.
   - 49/49 PASS unchanged.
   - Startup log captured to `data/verify_evidence/01_startup.log`;
     zero tracebacks.
   - Restart persistence: post-restart reads succeed against the
     same workspace + index.
   - The verify harness no longer relies on a CWD-relative symlink
     trick — `WORKSPACE_PATH` flows as a real env var.

### Migration notes (Phase 3.3)

| Removed | Added |
| --- | --- |
| `Settings.host` | (process supervisor owns host via `--host`) |
| `Settings.port` | (process supervisor owns port via `--port`) |
| `Settings.database_url` (flat field) | `Settings.database: DatabaseSettings` (sub-model + precedence rule); `Settings.database_url` is now a `@property` reading through |
| `"./data/workspace"` hardcoded in `backend/api/dependencies.py:36` | `Settings.workspace_path` (env: `WORKSPACE_PATH`) |
| `LOG_LEVEL=DEBG` silently degrading to INFO in `backend/core/logging.py:21` | Literal validator at `Settings` level; construction-time rejection |
| CWD-relative symlink trick in `scripts/verify_backend.sh` | `WORKSPACE_PATH=...` env var passed to the uvicorn subprocess |
| `BACKEND_URL` constants hardcoded in `verify_backend.sh` | Same constants — these are process-supervisor (CLI) inputs, not application config |

Backwards-compat guarantees:

    - `Settings()` with no env vars behaves identically to the
      pre-Phase-3.3 default (app_env="development", log_level="INFO",
      workspace_path="./data/workspace", database_url=dev DSN).
    - `Settings.database_url` is a property — every existing
      `settings.database_url` consumer keeps working unchanged.
    - `configure_logging(level)` now requires a `LogLevel` Literal;
      the only production caller is `lifespan.py:44` with
      `settings.log_level`, which is always typed `LogLevel`.

---

#### ADR-0021 — Configuration as a typed boundary (Phase 3.3)

- **Date:** 2026-08-05
- **Status:** Accepted
- **Decision:** Configuration is a single, typed boundary. The
  `backend.config` package owns ALL environment-variable reads; all
  other production code consumes configuration via `app.state` or
  injected dependencies. Settings is `frozen=True` after startup;
  every field is validated at construction time. Process-supervisor
  config (HOST, PORT) is OUT of scope of `Settings` — owned by the
  process launcher.
- **Rationale:** Five configuration defects existed in Phase 3.2-era
  code, each fixable only by understanding the wiring:

      1. `HOST` / `PORT` defined in Settings but never read — dead
         fields, hidden coupling to whatever they were once wired
         to.
      2. `LOG_LEVEL=DEBG` silently degraded to INFO via
         `getattr(logging, level.upper(), logging.INFO)`. A typo
         had no failure mode.
      3. `Settings` was mutable — nothing prevented runtime
         mutation. The cache, synchroniser, and engine all read
         from this singleton.
      4. `"./data/workspace"` hardcoded in `api/dependencies.py:36`
         with an explicit TODO. The verify harness worked around it
         via a CWD-symlink trick. A future rename of the workspace
         directory would silently misbehave.
      5. `POSTGRES_*` declared in `.env.example` but unread by
         Settings. New contributors would follow the documentation
         and wonder why their config was ignored.

  Configuration is a single boundary between operator intent and
  application behaviour. That boundary deserves a typed contract,
  immutable construction, and structural enforcement of the rule
  "no module outside `backend.config/` reads the environment
  directly."

  Process supervisor (HOST/PORT) separation: the application must
  not own its launching. The process supervisor owns the host and
  port; the application owns its application configuration. Wiring
  HOST/PORT back into Settings would re-create the earlier coupling
  and complicate future embedded-launcher scenarios (programmatic
  uvicorn, ASGI testing harnesses, k8s sidecars).

- **Alternatives Considered:**
  - **Read env vars at request time via a facade.** Rejected — it
    couples request handlers to operator intent (changing a
    deployment knob would change behaviour mid-request). Frozen
    startup-time reads are the simpler invariant.
  - **Wire HOST/PORT into Settings and read them in lifespan.**
    Rejected — the process supervisor owns host/port. Settings
    consuming supervisor-side config creates the coupling ADR-0021
    exists to remove.
  - **Use a third-party config library (Dynaconf, OmegaConf).**
    Rejected — adds a dependency for what Pydantic Settings already
    does well. The boundary is a one-package concern; an extra
    library would obscure it.
  - **Advisory comment in lieu of a structural test.** Rejected —
    future contributors will not see the comment. The AST test
    fails the build the moment a new module slips in an
    `os.environ` read. The same pattern is already used by the four
    existing isolation tests in this project.
  - **Hot-reload every setting.** Rejected for V1 — none of the
    V1 settings have a documented reload path. Adding the plumbing
    speculatively would be premature. The hot-reload policy is
    documented as "RESTART ONLY" everywhere, with a future-phase
    escape hatch called out in §13g.
- **Consequences:**
  - Every config item is validated at construction. Typos fail
    loudly at startup, not silently at runtime.
  - Settings is immutable after construction (`frozen=True`).
    Tests vary config by building fresh instances, not by
    mutating shared state.
  - The configuration boundary is enforced by
    `tests/test_config_isolation.py`. Any new `os.environ` / `os.getenv`
    reference outside `backend/config/` fails CI.
  - `backend/database/session.py` and `backend/alembic/env.py`
    are explicit carve-outs (allowlisted in the structural test).
    Both run before `app.state` exists; documented in §13g.
  - Process supervisor keeps its own contract. `verify_backend.sh`
    passes `--host`/`--port` to the uvicorn subprocess.
  - Future contributors adding new settings: they MUST update
    `docs/CONFIGURATION_INVENTORY.md` (v2) and add a unit test
    for any validator. The inventory is the single point of
    cross-reference.
- **Verification Strategy:**
  - Unit tests (`backend/tests/config/`): `test_settings_freeze.py`,
    `test_log_level_validator.py`, `test_database_settings.py`,
    `test_workspace_path_from_settings.py` — 36 cases total,
    asserting immutability, validation, precedence, and env-var
    resolution.
  - Structural test (`backend/tests/test_config_isolation.py`):
    AST-walks `backend/`, fails on `os.environ` / `os.getenv`
    outside the allowlist. Detector self-tests inject synthesized
    violations to guard the walker against silent regressions.
  - End-to-end live verification (`scripts/verify_backend.sh`):
    49/49 PASS, restart persistence OK, zero tracebacks in startup
    logs. Workspace path now flows as a real env var
    (`WORKSPACE_PATH`); the CWD-symlink trick is retired.
  - The Inventory (`docs/CONFIGURATION_INVENTORY.md` v2) is the
    cross-reference for every setting, its reload model, its
    migration risk, and its consumers. New settings MUST add a
    row + migration note.
- **Supersedes:** None. Builds on ADR-0006 (repositories own
  persistence orchestration) and ADR-0020 (end-to-end verification
  is architectural).

---