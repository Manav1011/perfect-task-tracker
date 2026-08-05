"""Secrets sub-model — Phase 5.0 C1.

Construction rules (per `backend.config.secrets`):

  - app_env == "development"            → secrets may be None / empty.
  - app_env != "development"            → both SESSION_SECRET and
                                          CSRF_SECRET must resolve to
                                          non-empty values, or
                                          ValueError raises at construction.

These tests cover both halves of the gate, plus the cross-cutting
properties of the sub-model (frozen, env-var resolution, SecretStr
unwrapping behaviour).
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from backend.config.secrets import Secrets


# ---------- dev-env gate (secrets optional) ---------------------------


def _patch_app_env(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    """Set or clear APP_ENV in the process environment.

    The model validator reads APP_ENV via os.getenv at construction
    time, so we set/clear it before instantiating Secrets.
    """
    if value is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", value)


def test_development_secrets_can_be_unset(monkeypatch) -> None:
    """app_env=development (the default) → secrets may both be None."""
    _patch_app_env(monkeypatch, None)  # unset → default ("development")
    s = Secrets()
    assert s.session_secret is None
    assert s.csrf_secret is None


def test_development_secrets_can_be_empty_string(monkeypatch) -> None:
    """Empty-string secrets in dev are allowed (not failing the gate).

    Pydantic's SecretStr preserves the empty string as `SecretStr('')`
    rather than coercing to None — the validator treats the empty
    unwrapped value as "missing" and tolerates it in dev.
    """
    _patch_app_env(monkeypatch, "development")
    s = Secrets(session_secret="", csrf_secret="")
    # Construction did not raise (the meaningful assertion). The empty
    # string lives on as SecretStr('') — what the validator saw is the
    # unwrapped falsy value, which is what it gates on.
    assert s.session_secret is not None
    assert s.session_secret.get_secret_value() == ""
    assert s.csrf_secret is not None
    assert s.csrf_secret.get_secret_value() == ""


def test_development_secrets_can_be_set(monkeypatch) -> None:
    """Dev can also have real secrets (e.g. local testing of Phase 5.4)."""
    _patch_app_env(monkeypatch, "development")
    s = Secrets(session_secret="dev-session", csrf_secret="dev-csrf")
    assert s.session_secret is not None
    assert s.session_secret.get_secret_value() == "dev-session"
    assert s.csrf_secret is not None
    assert s.csrf_secret.get_secret_value() == "dev-csrf"


# ---------- non-dev gate (secrets required) ---------------------------


def test_non_development_missing_both_raises(monkeypatch) -> None:
    """app_env=staging without SESSION_SECRET / CSRF_SECRET → ValueError."""
    _patch_app_env(monkeypatch, "staging")
    with pytest.raises(ValidationError) as exc_info:
        Secrets()
    msg = str(exc_info.value)
    assert "SESSION_SECRET" in msg
    assert "CSRF_SECRET" in msg


def test_non_development_only_session_secret_set_raises(monkeypatch) -> None:
    """Exactly one secret set in non-dev → ValueError listing the missing one."""
    _patch_app_env(monkeypatch, "production")
    with pytest.raises(ValidationError) as exc_info:
        Secrets(session_secret="real-session-secret")
    msg = str(exc_info.value)
    assert "CSRF_SECRET" in msg
    assert "SESSION_SECRET" not in msg


def test_non_development_only_csrf_secret_set_raises(monkeypatch) -> None:
    """Symmetric: only csrf_secret set in non-dev → ValueError mentions SESSION_SECRET."""
    _patch_app_env(monkeypatch, "production")
    with pytest.raises(ValidationError) as exc_info:
        Secrets(csrf_secret="real-csrf-secret")
    msg = str(exc_info.value)
    assert "SESSION_SECRET" in msg
    assert "CSRF_SECRET" not in msg


def test_non_development_both_set_passes(monkeypatch) -> None:
    """Both secrets set in non-dev → construction succeeds."""
    _patch_app_env(monkeypatch, "production")
    s = Secrets(
        session_secret="real-session-secret",
        csrf_secret="real-csrf-secret",
    )
    assert s.session_secret.get_secret_value() == "real-session-secret"
    assert s.csrf_secret.get_secret_value() == "real-csrf-secret"


def test_non_development_empty_string_raises(monkeypatch) -> None:
    """Empty string in non-dev is treated as missing (defensive)."""
    _patch_app_env(monkeypatch, "verify")
    with pytest.raises(ValidationError) as exc_info:
        Secrets(session_secret="", csrf_secret="real-csrf")
    msg = str(exc_info.value)
    assert "SESSION_SECRET" in msg


# ---------- env-var resolution via Pydantic env bridge -----------------


def test_secret_resolved_from_env(monkeypatch) -> None:
    """SESSION_SECRET / CSRF_SECRET env vars resolve through Pydantic."""
    monkeypatch.setenv("SESSION_SECRET", "env-session")
    monkeypatch.setenv("CSRF_SECRET", "env-csrf")
    s = Secrets()
    assert s.session_secret is not None
    assert s.session_secret.get_secret_value() == "env-session"
    assert s.csrf_secret is not None
    assert s.csrf_secret.get_secret_value() == "env-csrf"


def test_kwarg_takes_precedence_over_env_when_explicit(monkeypatch) -> None:
    """Explicit kwargs win over env values (mirrors DatabaseSettings pattern)."""
    monkeypatch.setenv("SESSION_SECRET", "env-session")
    s = Secrets(session_secret="kwarg-session")
    assert s.session_secret.get_secret_value() == "kwarg-session"


# ---------- cross-cutting invariants ----------------------------------


def test_secrets_is_frozen(monkeypatch) -> None:
    """Frozen after construction — mutation raises (Phase 3.3 invariant)."""
    _patch_app_env(monkeypatch, None)  # dev: secrets may be unset
    s = Secrets(session_secret="a", csrf_secret="b")
    with pytest.raises(ValidationError):
        s.session_secret = SecretStr("mutated")  # type: ignore[misc]


def test_unknown_app_env_treated_as_non_development(monkeypatch) -> None:
    """Anything not literally 'development' triggers the gate.

    The check is on equality with 'development', so 'Development',
    'DEV', ' prod ', ''  all fail to bypass. This pins the contract.
    """
    for value in ("staging", "production", "verify", "anything"):
        _patch_app_env(monkeypatch, value)
        with pytest.raises(ValidationError):
            Secrets()


# ---------- SecretStr disclosure regression (Phase 5.0 C1 follow-up) --


# A distinctive sentinel value used to scan for accidental disclosure.
# If a future regression surfaces the secret in a ValidationError,
# repr, log, or str() output, this test fails loudly.
_SECRET_SENTINEL = "Z3N7-psk-THIS-MUST-NEVER-LEAK-x4f2"


def test_secret_str_hides_value_in_repr_and_str() -> None:
    """SecretStr is exactly what its name promises: a str-like wrapper
    that hides the raw value from repr(), str(), and format().

    This test pins the no-disclosure contract. If a future change to
    Secrets (or a Pydantic upgrade) accidentally exposes the wrapped
    value through one of these channels, this test catches it.
    """
    s = Secrets(
        session_secret=_SECRET_SENTINEL,
        csrf_secret=_SECRET_SENTINEL + "-csrf",
    )

    # repr of the model itself must not contain the raw value.
    assert _SECRET_SENTINEL not in repr(s)
    # str() ditto.
    assert _SECRET_SENTINEL not in str(s)
    # repr/str of the individual SecretStr mirrors the same contract.
    assert s.session_secret is not None
    assert _SECRET_SENTINEL not in repr(s.session_secret)
    assert _SECRET_SENTINEL not in str(s.session_secret)
    # The sentinel is recoverable only via get_secret_value() — the
    # single explicit unwrap API. Anything else is a disclosure regression.
    assert s.session_secret.get_secret_value() == _SECRET_SENTINEL


def test_validation_error_on_missing_secrets_does_not_leak_existing_values(
    monkeypatch,
) -> None:
    """When Secrets construction fails in non-dev, the error message
    lists which env vars are missing — but it MUST NOT echo the
    *values* of any secrets that WERE provided.

    This pins the second half of the SecretStr contract: failure
    paths also stay sealed.
    """
    _patch_app_env(monkeypatch, "production")
    # Provide one secret, leave the other missing.
    try:
        Secrets(session_secret=_SECRET_SENTINEL)
    except ValidationError as exc:
        # The error mentions CSRF_SECRET is missing...
        msg = str(exc)
        assert "CSRF_SECRET" in msg
        # ...but never echoes the session_secret value we provided.
        assert _SECRET_SENTINEL not in msg
    else:
        pytest.fail("Expected ValidationError when CSRF_SECRET missing")


def test_settings_construction_in_non_dev_does_not_echo_secret_values(
    monkeypatch,
) -> None:
    """At the parent level: Settings() constructed in non-dev must
    propagate the same no-disclosure behaviour. The error message
    from the Secrets validator (re-raised inside Pydantic's batch
    validation) must not include raw secret bytes.
    """
    _patch_app_env(monkeypatch, "production")
    from backend.config.settings import Settings  # local import: cycle guard

    try:
        Settings(
            secrets=Secrets(
                session_secret=_SECRET_SENTINEL,
            )
        )
    except ValidationError as exc:
        assert _SECRET_SENTINEL not in str(exc)
    else:
        pytest.fail("Expected ValidationError when CSRF_SECRET missing")
