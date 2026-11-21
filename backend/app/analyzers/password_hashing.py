"""Detect direct fast digests of password inputs in Kotlin code."""

import re
from pathlib import PurePosixPath

from tree_sitter import Node

from . import kotlin as kt
from .models import Finding, Scan, ScanResult, Snapshot

_ALGORITHMS = {"MD5": "MD5", "SHA1": "SHA-1", "SHA256": "SHA-256"}
_DIGEST_UTILS = {
    "md5": "MD5", "md5Hex": "MD5", "md5Digest": "MD5", "md5DigestAsHex": "MD5",
    "sha1": "SHA-1", "sha1Hex": "SHA-1", "sha256": "SHA-256", "sha256Hex": "SHA-256",
}
_PASSWORD_NAME = re.compile(r"(?:.*(?:password|passwd|passphrase)|pwd)(?:bytes|chars)?$", re.I)


def accepts_password_source(path: PurePosixPath) -> bool:
    return path.suffix == ".kt"


def _password_input(source: kt.KotlinFile, node: Node) -> bool:
    """Follow aliases and common conversions of the actual digest input."""
    seen: set[Node] = set()
    pending = [node]
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        if node.type == "identifier" and _PASSWORD_NAME.fullmatch(kt.text(node)):
            return True
        resolved = source.resolve(node)
        if resolved != node:
            pending.append(resolved)
            continue
        if node.type == "binary_expression" and kt.operator(node) == "+":
            pending.extend(kt.children(node))
            continue
        if node.type == "navigation_expression":
            if _PASSWORD_NAME.fullmatch(kt.text(kt.children(node)[-1])):
                return True
            continue
        call = kt.call(node)
        if call and call.receiver and call.name in {"toByteArray", "encodeToByteArray", "toCharArray", "toString"}:
            pending.append(call.receiver)
    return False


def _algorithm(source: kt.KotlinFile, call: kt.Call) -> str | None:
    if call.receiver is None:
        return None
    receiver = source.resolve(call.receiver)
    if kt.text(receiver) in {"DigestUtils", "org.apache.commons.codec.digest.DigestUtils", "org.springframework.util.DigestUtils"}:
        return _DIGEST_UTILS.get(call.name)
    factory = kt.call(receiver)
    if (call.name == "digest" and factory and factory.name == "getInstance"
            and factory.receiver and kt.text(factory.receiver) in {"MessageDigest", "java.security.MessageDigest"}
            and factory.args):
        name = kt.literal(source.resolve(factory.args[0]))
        if name:
            return _ALGORITHMS.get(name.upper().replace("-", ""))
    return None


def scan_password_hashing(snapshot: Snapshot) -> ScanResult:
    scan = Scan(snapshot, accepts_password_source)
    findings = []
    for path, data in scan.files.items():
        source = kt.KotlinFile(data)
        if source.root.has_error:
            scan.skip(path, "KOTLIN_PARSE_ERROR", "Файл Kotlin разобран частично.")
        for node in kt.walk(source.root, "call_expression"):
            call = kt.call(node)
            if not call or len(call.args) != 1:
                continue
            algorithm = _algorithm(source, call)
            if algorithm and _password_input(source, call.args[0]):
                findings.append(Finding(
                    rule_id="sast.weak-password-hash", category="sast", severity="high",
                    title="Быстрый хеш пароля",
                    description=f"{algorithm} позволяет быстро перебирать варианты пароля по сохранённому хешу.",
                    remediation="Используйте Argon2id или BCrypt. В Spring Security используйте PasswordEncoder для сохранения и проверки пароля.",
                    path=path, line=kt.line(node), end_line=kt.end_line(node), evidence=algorithm,
                    metadata={"class": "weak-password-hash", "algorithm": algorithm},
                ))
    return scan.result("sast", "Прямое хеширование паролей в Kotlin", findings)
