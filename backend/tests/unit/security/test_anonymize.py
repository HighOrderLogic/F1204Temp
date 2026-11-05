from __future__ import annotations

import re
import pytest

from app.utils.anonymize import anonymize_eid, anonymize_records, anonymize_value


@pytest.mark.unit
def test_anonymize_records_admin_passthrough():
    """US02: Verifies that admin sessions receive unmasked, raw EIDs."""
    records = [{"eid": "982 123536700001", "value": 520.0}]
    result = anonymize_records(records, is_admin=True)
    assert result[0]["eid"] == "982 123536700001"


@pytest.mark.unit
def test_anonymize_value_admin_passthrough():
    """US02: Verifies that scalar EID anonymization returns raw value for admins."""
    raw_eid = "982 123536700002"
    assert anonymize_value(raw_eid, is_admin=True, field="eid") == raw_eid


@pytest.mark.unit
def test_anonymize_records_hmac_32bit_format():
    """US02: Verifies that non-admin sessions receive 32-bit HMAC pseudo-IDs matching ID-XXXXXXXX."""
    records = [{"eid": "982 123536700001", "value": 520.0}]
    result = anonymize_records(records, is_admin=False)
    assert result[0]["eid"] != "*****"
    assert re.match(r"^ID-[0-9A-F]{8}$", result[0]["eid"]) is not None


@pytest.mark.unit
def test_anonymize_records_relational_uniqueness():
    """US02: Verifies that distinct animal EIDs remain distinct after pseudonymization."""
    records = [
        {"eid": "982 123536700001", "value": 520.0},
        {"eid": "982 123536700002", "value": 540.0},
        {"eid": "982 123536700001", "value": 530.0},
    ]
    result = anonymize_records(records, is_admin=False)
    assert result[0]["eid"] != result[1]["eid"], "Distinct EIDs must not collide"
    assert result[0]["eid"] == result[2]["eid"], "Identical EIDs must map to identical pseudonym"


@pytest.mark.unit
def test_anonymize_sentinel_preservation():
    """US02: Verifies that reserved sentinels 'Overall', 'All', and 'No Treatment' are not hashed."""
    records = [
        {"eid": "Overall", "value": 500.0},
        {"eid": "All", "value": 500.0},
        {"eid": "No Treatment", "value": 500.0},
    ]
    result = anonymize_records(records, is_admin=False)
    assert result[0]["eid"] == "Overall"
    assert result[1]["eid"] == "All"
    assert result[2]["eid"] == "No Treatment"


@pytest.mark.unit
def test_anonymize_null_and_empty_edge_cases():
    """US02: Verifies handling of None, empty strings, non-string, and missing eid fields."""
    records = [
        {"eid": None, "value": 10.0},
        {"eid": "", "value": 20.0},
        {"value": 30.0},
    ]
    result = anonymize_records(records, is_admin=False)
    assert result[0]["eid"] is None
    assert result[1]["eid"] == ""
    assert "eid" not in result[2]
    assert anonymize_value(None, is_admin=False, field="eid") is None
    assert anonymize_value("982001", is_admin=False, field="other") == "982001"


@pytest.mark.unit
def test_anonymize_determinism_and_key_isolation(monkeypatch):
    """US02: Verifies deterministic output under same key and isolation under rotated key."""
    eid = "982 123536700001"
    monkeypatch.setenv("EID_PSEUDONYM_KEY", "key-alpha")
    h1 = anonymize_eid(eid)
    h2 = anonymize_eid(eid)
    assert h1 == h2

    monkeypatch.setenv("EID_PSEUDONYM_KEY", "key-beta")
    h3 = anonymize_eid(eid)
    assert h1 != h3
    assert re.match(r"^ID-[0-9A-F]{8}$", h3) is not None



@pytest.fixture
def no_pseudonym_key(monkeypatch):
    """Neither the environment nor backend/.env (settings) provides a key."""
    from app.config import get_settings

    monkeypatch.delenv("EID_PSEUDONYM_KEY", raising=False)
    monkeypatch.setattr(get_settings(), "eid_pseudonym_key", "")


@pytest.mark.unit
def test_anonymize_missing_key_raises_with_fix_instructions(no_pseudonym_key):
    """Masking refuses to fall back to a public key and says how to set one."""
    from app.utils.anonymize import PseudonymKeyMissingError, pseudonym_key_missing

    assert pseudonym_key_missing()
    with pytest.raises(PseudonymKeyMissingError) as exc:
        anonymize_eid("982 123536700001")
    assert "EID_PSEUDONYM_KEY" in str(exc.value)
    assert "backend/.env" in str(exc.value)
    assert anonymize_records([{"eid": "x"}], is_admin=True) == [{"eid": "x"}], "admins never need the key"


@pytest.mark.unit
def test_anonymize_legacy_public_key_treated_as_missing(no_pseudonym_key, monkeypatch):
    """The old built-in default is public in git history, so it must not be accepted."""
    from app.utils.anonymize import PseudonymKeyMissingError

    monkeypatch.setenv("EID_PSEUDONYM_KEY", "comp3988-default-secret-key")
    with pytest.raises(PseudonymKeyMissingError):
        anonymize_eid("982 123536700001")


@pytest.mark.unit
def test_anonymize_key_from_settings_when_env_absent(no_pseudonym_key, monkeypatch):
    """A key in backend/.env (loaded into settings) works without an environment variable."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "eid_pseudonym_key", "from-dotenv")
    from_settings = anonymize_eid("982 123536700001")
    monkeypatch.setenv("EID_PSEUDONYM_KEY", "from-dotenv")
    assert anonymize_eid("982 123536700001") == from_settings


@pytest.mark.unit
def test_resolve_animal_id_by_role():
    """Admins resolve real EIDs, non-admins masked IDs; anything else is not on the farm."""
    from app.utils.anonymize import resolve_animal_id

    farm = ["982 123536700233", "982 123536700478"]
    mask = anonymize_eid(farm[1])
    assert resolve_animal_id(farm[1], farm, is_admin=True) == farm[1]
    assert resolve_animal_id(mask, farm, is_admin=True) is None
    assert resolve_animal_id(mask, farm, is_admin=False) == farm[1]
    assert resolve_animal_id(farm[1], farm, is_admin=False) is None
    assert resolve_animal_id("ID-00000000", farm, is_admin=False) is None


@pytest.mark.unit
def test_pseudonym_lookup_collision_does_not_reveal_eids(monkeypatch):
    """A clash between two animals' masks is an error, and the error names only the mask."""
    from app.utils import anonymize
    from app.utils.anonymize import PseudonymCollisionError, pseudonym_lookup

    monkeypatch.setattr(anonymize, "anonymize_eid", lambda eid: "ID-SAMEMASK")
    with pytest.raises(PseudonymCollisionError) as exc:
        pseudonym_lookup(["982 123536700233", "982 123536700478"])
    assert "ID-SAMEMASK" in str(exc.value)
    assert "982" not in str(exc.value)
