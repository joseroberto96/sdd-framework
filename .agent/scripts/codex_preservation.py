#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inventario de preservación posterior para el smoke de Codex.

El inventario observa el checkout por contenido y conserva, por separado, el
estado lógico de Git.  ``git status`` se mantiene como evidencia auxiliar,
pero no es suficiente para detectar cambios en un archivo que ya estaba dirty
o en un archivo ignorado.

Este módulo no restaura ni escribe nada.  Las capturas son deliberadamente
acotadas: si no se puede inspeccionar todo el ámbito, el resultado es
``unavailable`` y el consumidor no puede convertirlo en ``pass``.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import time
from pathlib import Path
from typing import Any


EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
DEFAULT_MAX_ENTRIES = 100_000
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_SECONDS = 30.0
_HASH_CHUNK_SIZE = 1024 * 1024


def _check(status: str, detail: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "detail": detail}
    result.update(extra)
    return result


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _path_is_transient(path: Path, transient_paths: tuple[Path, ...]) -> bool:
    for transient in transient_paths:
        try:
            path.relative_to(transient)
            return True
        except ValueError:
            continue
    return False


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    # Reading the index must not refresh its stat cache or take a write lock.
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def _run_git(root: Path, args: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=_git_environment(),
    )


def _git_state(root: Path, deadline: float | None = None) -> tuple[dict[str, Any], str | None]:
    """Return HEAD/index logical state, or an error for an unreadable repo."""
    def run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
        if deadline is None:
            timeout = 15.0
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(["git", *args], 0)
            timeout = min(15.0, remaining)
        return _run_git(root, args, timeout=timeout)

    try:
        inside = run(["rev-parse", "--is-inside-work-tree"])
    except (OSError, subprocess.SubprocessError) as exc:
        # A directory without Git is a valid standalone inventory.  Other
        # failures are visible so a smoke checkout is never certified blindly.
        if isinstance(exc, FileNotFoundError):
            return {"available": False, "head": None, "index": {}}, None
        return {}, f"no se pudo consultar Git: {exc}"
    if inside.returncode != 0 or inside.stdout.strip().lower() != b"true":
        return {"available": False, "head": None, "index": {}}, None

    try:
        head_result = run(["rev-parse", "--verify", "HEAD"])
        # rc=128 with no HEAD is the normal unborn-branch state.  Any other
        # error means the logical Git evidence is not available.
        if head_result.returncode == 0:
            head = head_result.stdout.strip().decode("ascii")
        elif head_result.returncode == 128:
            head = None
        else:
            detail = (head_result.stderr or head_result.stdout).decode(
                "utf-8", "replace"
            ).strip()
            return {}, f"no se pudo resolver HEAD: {detail or 'error de Git'}"

        index_result = run(["ls-files", "--stage", "-z"])
        if index_result.returncode != 0:
            detail = (index_result.stderr or index_result.stdout).decode(
                "utf-8", "replace"
            ).strip()
            return {}, f"no se pudo leer el índice Git: {detail or 'error de Git'}"
        # A conflicted index may contain up to three records for one path
        # (stages 1/2/3). Keep all of them instead of silently overwriting
        # the earlier stage in a path-keyed dictionary.
        index_records: dict[str, list[dict[str, Any]]] = {}
        for raw_record in index_result.stdout.split(b"\0"):
            if not raw_record:
                continue
            try:
                metadata, raw_path = raw_record.split(b"\t", 1)
                mode, object_id, stage = metadata.split(b" ", 2)
                path = raw_path.decode("utf-8", "surrogateescape")
                index_records.setdefault(path, []).append({
                    "mode": mode.decode("ascii"),
                    "object": object_id.decode("ascii"),
                    "stage": int(stage),
                })
            except (ValueError, UnicodeError) as exc:
                return {}, f"índice Git ilegible: {exc}"

        # Preserve the established path -> object shape for the common case;
        # use a sorted list only when the index genuinely has multiple stages.
        index: dict[str, dict[str, Any] | list[dict[str, Any]]] = {
            path: records[0] if len(records) == 1 else sorted(records, key=lambda item: item["stage"])
            for path, records in index_records.items()
        }

        tracked_result = run(["ls-files", "-z"])
        if tracked_result.returncode != 0:
            detail = (tracked_result.stderr or tracked_result.stdout).decode(
                "utf-8", "replace"
            ).strip()
            return {}, f"no se pudo leer el inventario Git: {detail or 'error de Git'}"
        tracked = {
            raw.decode("utf-8", "surrogateescape")
            for raw in tracked_result.stdout.split(b"\0")
            if raw
        }
        return {"available": True, "head": head, "index": index, "tracked": tracked}, None
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return {}, f"no se pudo leer el estado lógico de Git: {exc}"


def _signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
        int(info.st_mode),
    )


class _Capture:
    def __init__(
        self,
        root: Path,
        transient_paths: tuple[Path, ...],
        max_entries: int,
        max_bytes: int,
        max_seconds: float,
        started: float | None = None,
    ) -> None:
        self.root = root
        self.transient_paths = transient_paths
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds
        self.started = started if started is not None else time.monotonic()
        self.deadline = self.started + max_seconds
        self.entries: dict[str, dict[str, Any]] = {}
        self.excluded: list[str] = []
        self.bytes_read = 0
        self.error: str | None = None
        self.tracked: set[str] = set()

    def _limit(self, message: str) -> None:
        if self.error is None:
            self.error = message

    def _check_budget(self) -> bool:
        if self.error is not None:
            return False
        if time.monotonic() > self.deadline:
            self._limit("captura interrumpida por límite de tiempo")
            return False
        if len(self.entries) > self.max_entries:
            self._limit("captura interrumpida por límite de entradas")
            return False
        if self.bytes_read > self.max_bytes:
            self._limit("captura interrumpida por límite de bytes leídos")
            return False
        return True

    def _add(self, relative: str, entry: dict[str, Any]) -> bool:
        if not self._check_budget():
            return False
        if len(self.entries) >= self.max_entries:
            self._limit("captura interrumpida por límite de entradas")
            return False
        self.entries[relative] = entry
        return True

    def _hash_file(self, path: Path, info_before: os.stat_result) -> str | None:
        digest = hashlib.sha256()
        try:
            with path.open("rb", buffering=0) as stream:
                while True:
                    if not self._check_budget():
                        return None
                    chunk = stream.read(_HASH_CHUNK_SIZE)
                    if not chunk:
                        break
                    self.bytes_read += len(chunk)
                    if self.bytes_read > self.max_bytes:
                        self._limit("captura interrumpida por límite de bytes leídos")
                        return None
                    digest.update(chunk)
            info_after = path.stat(follow_symlinks=False)
        except (OSError, ValueError) as exc:
            self._limit(f"no se pudo leer {self.root / _relative(path, self.root)}: {exc}")
            return None
        if not stat.S_ISREG(info_after.st_mode):
            self._limit(
                f"el tipo de archivo cambió durante la captura: {_relative(path, self.root)}"
            )
            return None
        if _signature(info_before) != _signature(info_after):
            self._limit(
                f"el archivo cambió durante la captura: {_relative(path, self.root)}"
            )
            return None
        return digest.hexdigest()

    def _directory_has_tracked_descendant(self, relative: str) -> bool:
        prefix = f"{relative}/" if relative else ""
        return any(path.startswith(prefix) for path in self.tracked)

    def _walk(self, directory: Path) -> None:
        if self.error is not None:
            return
        try:
            directory_before = directory.stat()
            with os.scandir(directory) as scanner:
                children = sorted(scanner, key=lambda item: item.name)
                for child in children:
                    if not self._check_budget():
                        return
                    child_path = Path(child.path)
                    relative = _relative(child_path, self.root)
                    if _path_is_transient(child_path, self.transient_paths):
                        continue
                    # .git may be a directory or a gitfile; both are
                    # administrative state outside the content inventory.
                    if child.name == ".git":
                        self.excluded.append(relative)
                        continue
                    try:
                        info = child.stat(follow_symlinks=False)
                    except OSError as exc:
                        self._limit(f"no se pudo inspeccionar {relative}: {exc}")
                        return
                    mode = stat.S_IMODE(info.st_mode)
                    if child.is_dir(follow_symlinks=False):
                        if child.name in EXCLUDED_DIR_NAMES and not self._directory_has_tracked_descendant(relative):
                            self.excluded.append(relative)
                            continue
                        if not self._add(relative, {"kind": "dir", "mode": mode}):
                            return
                        self._walk(child_path)
                        if self.error is not None:
                            return
                        try:
                            directory_after = child_path.stat()
                        except OSError as exc:
                            self._limit(f"no se pudo verificar {relative}: {exc}")
                            return
                        if _signature(info) != _signature(directory_after):
                            self._limit(f"el directorio cambió durante la captura: {relative}")
                            return
                        continue
                    if child.is_symlink():
                        try:
                            target = os.readlink(child_path)
                        except OSError as exc:
                            self._limit(f"no se pudo leer el enlace {relative}: {exc}")
                            return
                        if not self._add(
                            relative,
                            {"kind": "symlink", "mode": mode, "target": target},
                        ):
                            return
                        try:
                            info_after = child_path.stat(follow_symlinks=False)
                        except OSError as exc:
                            self._limit(f"no se pudo verificar el enlace {relative}: {exc}")
                            return
                        if _signature(info) != _signature(info_after):
                            self._limit(f"el enlace cambió durante la captura: {relative}")
                            return
                        continue
                    if stat.S_ISREG(info.st_mode):
                        digest = self._hash_file(child_path, info)
                        if digest is None:
                            return
                        if not self._add(
                            relative,
                            {
                                "kind": "file",
                                "mode": mode,
                                "size": int(info.st_size),
                                "sha256": digest,
                            },
                        ):
                            return
                        continue
                    self._limit(f"tipo de archivo no soportado en {relative}")
                    return
            directory_after = directory.stat()
        except (OSError, ValueError) as exc:
            self._limit(f"no se pudo recorrer {directory}: {exc}")
            return
        if _signature(directory_before) != _signature(directory_after):
            self._limit(
                f"el directorio cambió durante la captura: {_relative(directory, self.root) or '.'}"
            )


def _unavailable_snapshot(
    root: Path,
    detail: str,
    transient_paths: tuple[Path, ...] = (),
    **extra: Any,
) -> dict[str, Any]:
    transient_relative = [
        path.relative_to(root).as_posix()
        for path in transient_paths
        if path == root or path.is_relative_to(root)
    ]
    return {
        "status": "unavailable",
        "detail": detail,
        "root": str(root),
        "entries": {},
        "excluded": [],
        "counters": {"entries": 0, "bytes_read": 0, "excluded": 0},
        "git_head": None,
        "git_index": {},
        "transient_paths": transient_relative,
        **extra,
    }


def capture_snapshot(
    root: Path,
    transient_paths: tuple[Path, ...] = (),
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> dict[str, Any]:
    """Capture a bounded, content-addressed view of ``root``.

    ``transient_paths`` is an exact allowlist of paths created by the smoke
    itself.  It never acts as a directory-name exclusion and does not permit
    excluding a pre-existing fixture or the whole ``.codex`` tree.
    """
    root = Path(root).resolve()
    started = time.monotonic()
    if not root.is_dir():
        return _unavailable_snapshot(root, "la raíz del checkout no es un directorio")
    if max_entries <= 0 or max_bytes < 0 or max_seconds <= 0:
        return _unavailable_snapshot(root, "los límites de captura deben ser positivos")
    try:
        transient = tuple(Path(path).resolve() for path in transient_paths)
    except (OSError, RuntimeError) as exc:
        return _unavailable_snapshot(root, f"no se pudieron resolver rutas transitorias: {exc}")

    deadline = started + max_seconds
    git, git_error = _git_state(root, deadline)
    if git_error:
        return _unavailable_snapshot(root, git_error, transient)
    if time.monotonic() - started > max_seconds:
        return _unavailable_snapshot(root, "captura interrumpida por límite de tiempo", transient)
    capture = _Capture(root, transient, max_entries, max_bytes, max_seconds, started)
    capture.tracked = set(git.get("tracked", set()))
    capture._walk(root)
    duration_ms = int((time.monotonic() - capture.started) * 1000)
    if capture.error:
        return {
            "status": "unavailable",
            "detail": capture.error,
            "root": str(root),
            "entries": capture.entries,
            "excluded": sorted(set(capture.excluded)),
            "counters": {
                "entries": len(capture.entries),
                "bytes_read": capture.bytes_read,
                "excluded": len(set(capture.excluded)),
                "duration_ms": duration_ms,
            },
            "git_head": git.get("head"),
            "git_index": git.get("index", {}),
            "transient_paths": [
                _relative(path, root)
                for path in transient
                if path == root or path.is_relative_to(root)
            ],
        }
    return {
        "status": "pass",
        "detail": "inventario de preservación capturado",
        "root": str(root),
        "entries": capture.entries,
        "excluded": sorted(set(capture.excluded)),
        "counters": {
            "entries": len(capture.entries),
            "bytes_read": capture.bytes_read,
            "excluded": len(set(capture.excluded)),
            "duration_ms": duration_ms,
        },
        "git_head": git.get("head"),
        "git_index": git.get("index", {}),
        "transient_paths": [
            _relative(path, root)
            for path in transient
            if path == root or path.is_relative_to(root)
        ],
    }


def _index_changed_paths(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    before_index = before.get("git_index") or {}
    after_index = after.get("git_index") or {}
    changed = {
        path
        for path in set(before_index) | set(after_index)
        if before_index.get(path) != after_index.get(path)
    }
    return sorted(changed)


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare two captures without exposing individual file hashes."""
    before_status = str(before.get("status", "unavailable"))
    after_status = str(after.get("status", "unavailable"))
    if before_status != "pass" or after_status != "pass":
        if "fail" in {before_status, after_status}:
            status = "fail"
        else:
            status = "unavailable"
        details = [
            str(snapshot.get("detail"))
            for snapshot in (before, after)
            if snapshot.get("status") != "pass" and snapshot.get("detail")
        ]
        return {
            "status": status,
            "detail": "; ".join(dict.fromkeys(details)) or "no se pudo comparar el inventario",
            "changed_paths": [],
            "counters": {
                "before_entries": len(before.get("entries", {})),
                "after_entries": len(after.get("entries", {})),
            },
        }

    before_entries = before.get("entries") if isinstance(before.get("entries"), dict) else {}
    after_entries = after.get("entries") if isinstance(after.get("entries"), dict) else {}
    changed_paths = sorted(
        path
        for path in set(before_entries) | set(after_entries)
        if before_entries.get(path) != after_entries.get(path)
    )
    if before.get("git_head") != after.get("git_head"):
        changed_paths.append("__git__/HEAD")
    index_paths = _index_changed_paths(before, after)
    if index_paths:
        changed_paths.extend(f"__git__/index/{path}" for path in index_paths)
    changed_paths = sorted(set(changed_paths))
    if changed_paths:
        return {
            "status": "fail",
            "detail": "el inventario de preservación detectó cambios en el checkout",
            "changed_paths": changed_paths,
            "counters": {
                "before_entries": len(before_entries),
                "after_entries": len(after_entries),
            },
        }
    return {
        "status": "pass",
        "detail": "el inventario de preservación no detectó cambios",
        "changed_paths": [],
        "counters": {
            "before_entries": len(before_entries),
            "after_entries": len(after_entries),
        },
    }


def snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the safe, public portion used in smoke JSON."""
    counters = snapshot.get("counters") if isinstance(snapshot.get("counters"), dict) else {}
    return {
        "status": str(snapshot.get("status", "unavailable")),
        "detail": str(snapshot.get("detail", "")),
        "entries": int(counters.get("entries", len(snapshot.get("entries", {})))),
        "bytes_read": int(counters.get("bytes_read", 0)),
        "excluded": int(counters.get("excluded", len(snapshot.get("excluded", [])))),
        "scope": {
            "excluded_dir_names": sorted(EXCLUDED_DIR_NAMES),
            "transient_paths": list(snapshot.get("transient_paths", [])),
        },
    }


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_SECONDS",
    "EXCLUDED_DIR_NAMES",
    "capture_snapshot",
    "compare_snapshots",
    "snapshot_summary",
]
