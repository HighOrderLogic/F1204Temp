from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Iterable

_SENTINELS = frozenset({"Overall", "All", "No Treatment"})
# Old built-in fallback. It is public in git history, so it is treated as missing.
_LEGACY_PUBLIC_KEY = "comp3988-default-secret-key"
_HEX_LEN = 8

KEY_MISSING_CODE = "EID_PSEUDONYM_KEY_MISSING"
KEY_MISSING_MESSAGE = (
    "EID_PSEUDONYM_KEY is missing (or still set to the old public default). It is the secret used to "
    "mask animal EIDs for non-admin users, and the API refuses requests without it. "
    "Local development: add a line EID_PSEUDONYM_KEY=your-random-secret to backend/.env and restart the server. "
    "Production: add it in Vercel > Project Settings > Environment Variables and redeploy. "
    "Generate a value with: python -c \"import secrets; print(secrets.token_hex(32))\". "
    "Changing the key later changes every masked EID users have seen."
)


class PseudonymKeyMissingError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(KEY_MISSING_MESSAGE)


def _configured_key() -> str:
    # Environment first (Vercel, tests), then backend/.env via settings: same precedence as pydantic.
    from app.config import get_settings

    key = os.environ.get("EID_PSEUDONYM_KEY") or get_settings().eid_pseudonym_key
    return "" if key.strip() == _LEGACY_PUBLIC_KEY else key.strip()


def pseudonym_key_missing() -> bool:
    return not _configured_key()


def get_pseudonym_key() -> bytes:
    key = _configured_key()
    if not key:
        raise PseudonymKeyMissingError()
    return key.encode("utf-8")


def anonymize_eid(eid: Any) -> Any:
    if eid is None:
        return None
    s = str(eid).strip()
    if not s or s in _SENTINELS:
        return eid
    digest = hmac.new(get_pseudonym_key(), s.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"ID-{digest[:_HEX_LEN].upper()}"


def anonymize_records(records: list[dict], is_admin: bool) -> list[dict]:
    if is_admin:
        return records
    return [{**r, "eid": anonymize_eid(r["eid"])} if "eid" in r else r for r in records]


def anonymize_value(value: Any, is_admin: bool, field: str = "eid") -> Any:
    if field == "eid" and not is_admin:
        return anonymize_eid(value)
    return value



class PseudonymCollisionError(RuntimeError):
    """Two animals on one farm share a masked ID. The message names only the mask, never real EIDs."""


def pseudonym_lookup(eids: Iterable[Any]) -> dict[str, str]:
    """{masked ID: real EID} for one farm's animals, so a non-admin's choice can be resolved server-side.

    The mask cannot be reversed; this matches it against the farm's known EIDs instead.
    """
    lookup: dict[str, str] = {}
    for eid in eids:
        real = str(eid).strip()
        mask = anonymize_eid(real)
        if lookup.get(mask, real) != real:
            raise PseudonymCollisionError(
                f"Two animals on this farm share the masked ID {mask}. Lengthen _HEX_LEN in "
                "app/utils/anonymize.py (this changes every masked ID)."
            )
        lookup[mask] = real
    return lookup


def resolve_animal_id(animal: str, farm_eids: Iterable[Any], is_admin: bool) -> str | None:
    """Real EID for an animal chosen in the UI: admins send real EIDs, everyone else masked IDs.

    Returns None if the animal is not on the farm (or a non-admin sends a real EID).
    """
    animal = str(animal).strip()
    if is_admin:
        return animal if animal in {str(e).strip() for e in farm_eids} else None
    return pseudonym_lookup(farm_eids).get(animal)
