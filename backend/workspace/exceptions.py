"""Workspace cache exception hierarchy.

The cache is a runtime optimisation, never a source of truth
(per ADR-0016 / TECH_SPEC §13d). These exceptions are *expected*
failure modes — they tell the caller whether to fall back to
disk or to fail startup.

Two distinct reasons to raise:

    1. **Not yet populated.** A caller asked the cache for a
       node before the StartupSubsystem's populate step ran.
       At *startup* this is a programming error (fail fast —
       the subsystem should never have entered the runtime
       path). At *runtime* it should be impossible if startup
       succeeded, but we treat it as a warning — the cache
       self-heals via a disk fallback.

    2. **Internally inconsistent.** The cache holds some state
       but the asked-for id isn't in it (after an `invalidate`,
       for example). The repository's self-healing miss path
       catches this and re-reads from disk; the warning log
       makes the inconsistency observable.

We keep the hierarchy narrow so future failure modes (eviction,
serialisation, distributed-cache errors) can be added without
breaking callers that switch on `CacheError`.
"""

from __future__ import annotations


class CacheError(Exception):
    """Base class for cache-layer errors.

    Catch this at any boundary where the cache is a *side
    collaborator* and the caller wants to fall back to the
    filesystem. The narrow subclasses below are for callers
    that need to distinguish "never populated" from "lost a
    node" — the startup subsystem falls into the former, the
    repository's miss handler into the latter.
    """


class CacheNotInitializedError(CacheError):
    """The cache has not been populated yet.

    Raised by the read API (`load_node`, `load_children`,
    `load_tree`) when called before `populate()` has run.

    Per Phase 3.0 brief + ChatGPT's runtime-vs-startup
    refinement:

        - At startup, treat this as a programming error:
          re-raise; do not catch.
        - At runtime, treat this as a warning: log structured
          diagnostics and fall back to the filesystem.

    The runtime fallback path lives in the repository, not
    here — the cache itself doesn't know how to read from
    disk.
    """


class CacheConsistencyError(CacheError):
    """The cache holds state, but the requested id is missing.

    Raised when a node id was previously invalidated (or never
    existed in the populated snapshot) and a reader asked for
    it. The repository catches this and self-heals by reading
    from disk and writing the result back into the cache on
    the next populate cycle.

    We deliberately do NOT make this a subclass of
    `CacheNotInitializedError`: the two conditions have
    different recovery semantics.
    """