"""Signed rule files: know who a shared rules file came from and that it is unchanged.

Colleagues on the same Epic build share learned rules (:mod:`rule_sharing`).
A file can carry an Ed25519 signature over its canonical JSON:

    "signature": {"alg": "ed25519", "signer": "Nash", "key": "<public key>",
                  "sha256": "<digest of the rules>", "sig": "<signature>"}

* **Your key** is made on first use; the private half is encrypted with
  :mod:`secure_store` (``data/signing_key.enc``), the public half is shown in
  Settings → Share rules with a short fingerprint to read out to a colleague.
* **Trusted keys** live in ``data/trusted_keys.json`` (names and public keys —
  no chart text). Import a colleague's key once; their files then show as
  *signed by a trusted colleague*.
* :func:`verify` answers ``unsigned`` / ``trusted`` / ``untrusted`` / ``invalid``.
  An *invalid* file (edited after signing, or a forged signature) is refused.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from . import store

__all__ = ["Verification", "canonical", "public_key", "fingerprint", "sign", "verify",
           "trusted_keys", "trust_key", "untrust_key"]

ALG = "ed25519"


def _key_path():
    return store.SIGNING_KEY_FILE


def _trusted_path():
    return store.TRUSTED_KEYS_FILE


def canonical(payload: dict) -> bytes:
    """The bytes that are signed: the payload without its signature, keys sorted."""
    body = {k: v for k, v in payload.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _private_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from . import secure_store
    path = _key_path()
    if path.exists():
        raw = base64.b64decode(secure_store.read_text(path))
        return Ed25519PrivateKey.from_private_bytes(raw)
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    secure_store.write_text(path, base64.b64encode(raw).decode("ascii"))
    return key


def _public_b64(key) -> str:
    from cryptography.hazmat.primitives import serialization
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def public_key() -> str:
    """This computer's public signing key (base64), creating the key pair if needed."""
    return _public_b64(_private_key())


def fingerprint(key_b64: str) -> str:
    """Short, read-aloud form of a public key: ``AB12-CD34-EF56-7890``."""
    digest = hashlib.sha256(key_b64.strip().encode("ascii")).hexdigest()[:16].upper()
    return "-".join(digest[i:i + 4] for i in range(0, 16, 4))


def sign(payload: dict, signer: str = "") -> dict:
    """``payload`` with a ``signature`` block added (any old one replaced)."""
    key = _private_key()
    body = canonical(payload)
    sig = key.sign(body)
    return {**{k: v for k, v in payload.items() if k != "signature"},
            "signature": {"alg": ALG, "signer": signer.strip()[:60], "key": _public_b64(key),
                          "sha256": hashlib.sha256(body).hexdigest(),
                          "sig": base64.b64encode(sig).decode("ascii")}}


@dataclass
class Verification:
    status: str            # "unsigned" | "trusted" | "untrusted" | "invalid"
    signer: str = ""
    fingerprint: str = ""
    trusted_name: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "invalid"

    @property
    def message(self) -> str:
        if self.status == "trusted":
            return f"Signed by {self.trusted_name} (trusted key {self.fingerprint})"
        if self.status == "untrusted":
            who = f" “{self.signer}”" if self.signer else ""
            return (f"Signed{who} with a key you haven't trusted yet ({self.fingerprint}) — "
                    "check the fingerprint with the sender")
        if self.status == "invalid":
            return f"Signature does not match — the file was changed after signing ({self.detail})"
        return "Not signed — only import rules from someone you trust"

    def to_dict(self) -> dict:
        return {"status": self.status, "signer": self.signer, "fingerprint": self.fingerprint,
                "trusted_name": self.trusted_name, "message": self.message}


def verify(payload: dict) -> Verification:
    """Check ``payload``'s signature against its content and your trusted keys."""
    block = payload.get("signature") if isinstance(payload, dict) else None
    if block is None:
        return Verification("unsigned")
    if not isinstance(block, dict) or block.get("alg") != ALG:
        return Verification("invalid", detail="unknown signature format")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key_b64 = str(block.get("key") or "")
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64))
        body = canonical(payload)
        if block.get("sha256") and block["sha256"] != hashlib.sha256(body).hexdigest():
            return Verification("invalid", str(block.get("signer") or ""), fingerprint(key_b64),
                                detail="content digest differs")
        pub.verify(base64.b64decode(str(block.get("sig") or "")), body)
    except InvalidSignature:
        return Verification("invalid", str(block.get("signer") or ""), detail="bad signature")
    except Exception as exc:
        return Verification("invalid", detail=f"{type(exc).__name__}")
    fp = fingerprint(key_b64)
    for entry in trusted_keys():
        if entry["key"] == key_b64:
            return Verification("trusted", str(block.get("signer") or ""), fp, entry["name"])
    try:
        if key_b64 == public_key():
            return Verification("trusted", str(block.get("signer") or ""), fp, "you")
    except Exception:
        pass
    return Verification("untrusted", str(block.get("signer") or ""), fp)


def trusted_keys() -> list[dict]:
    try:
        data = json.loads(_trusted_path().read_text(encoding="utf-8"))
        return [e for e in data if isinstance(e, dict) and isinstance(e.get("key"), str)
                and isinstance(e.get("name"), str)] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_trusted(entries: list[dict]) -> None:
    path = _trusted_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def trust_key(name: str, key_b64: str) -> str:
    """Trust a colleague's public key; returns its fingerprint. Raises ValueError if malformed."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key_b64 = "".join(key_b64.split())
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64, validate=True))
    except Exception as exc:
        raise ValueError("That is not a Chart Cleaner public key") from exc
    name = " ".join(name.split())[:60] or fingerprint(key_b64)
    entries = [e for e in trusted_keys() if e["key"] != key_b64]
    entries.append({"name": name, "key": key_b64})
    _save_trusted(entries)
    return fingerprint(key_b64)


def untrust_key(key_b64: str) -> bool:
    entries = trusted_keys()
    kept = [e for e in entries if e["key"] != key_b64]
    if len(kept) == len(entries):
        return False
    _save_trusted(kept)
    return True
