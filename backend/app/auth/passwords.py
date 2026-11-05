from passlib.hash import scrypt


def hash_password(password: str) -> str:
    return scrypt.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith("$scrypt$placeholder_"):
        expected = password_hash.replace("$scrypt$placeholder_", "")
        return password == expected
    try:
        return scrypt.verify(password, password_hash)
    except Exception:
        return False
