#!/usr/bin/env python3
"""Métricas reproducibles del contexto y de sus lecturas reales.

El modo normal solo resume páginas ya emitidas. El modo diagnóstico añade una
traza efímera de las lecturas realizadas por el proceso real; no la incorpora
al payload, al fingerprint ni al estado canónico. La traza se activa mediante
``FRAMEWORK_CONTEXT_TRACE_DIR`` y cada consulta escribe allí un JSON fuera del
workspace.
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import io
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import state_queries


TRACE_ENV = "FRAMEWORK_CONTEXT_TRACE_DIR"
_THIS_FILE = Path(__file__).resolve()
_ACTIVE_TRACE: contextvars.ContextVar["ReadTrace | None"] = contextvars.ContextVar(
    "framework_context_read_trace", default=None
)
_TRACE_SEQUENCE = 0


class SnapshotChanged(RuntimeError):
    """Una dependencia cambió mientras la consulta reutilizaba el snapshot."""


def _caller() -> str:
    """Identifica el consumidor sin guardar rutas de stack en el reporte."""
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        while frame is not None:
            filename = Path(frame.f_code.co_filename).resolve()
            if filename != _THIS_FILE and filename.name != "pathlib.py":
                return f"{filename.name}:{frame.f_code.co_name}"
            frame = frame.f_back
    finally:
        del frame
    return "unknown"


def _stat_signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


@dataclass
class ReadTrace:
    """Snapshot de lecturas de una sola consulta o fase de transporte."""

    root: Path
    phase: str = "query"
    entries: list[dict[str, Any]] = field(default_factory=list)
    unobserved_reads: list[dict[str, str]] = field(default_factory=list)
    _cache: dict[tuple[str, str, str, str, str], tuple[tuple[int, int, int, int], Any, int]] = field(
        default_factory=dict, repr=False
    )
    _raw_cache: dict[str, tuple[tuple[int, int, int, int], bytes]] = field(
        default_factory=dict, repr=False
    )
    _signatures: dict[str, tuple[int, int, int, int]] = field(default_factory=dict, repr=False)
    _paths: dict[str, Path] = field(default_factory=dict, repr=False)

    def relative_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return resolved.as_posix()

    def _check_signature(self, path: Path) -> tuple[int, int, int, int]:
        signature = _stat_signature(path)
        if signature is None:
            raise SnapshotChanged(f"la dependencia desapareció durante la consulta: {path}")
        key = self.relative_path(path)
        self._paths[key] = path
        previous = self._signatures.get(key)
        if previous is not None and previous != signature:
            raise SnapshotChanged(f"la dependencia cambió durante la consulta: {path}")
        self._signatures[key] = signature
        return signature

    def _record(
        self, path: Path, operation: str, *, content_bytes: int,
        physical_bytes: int, cached: bool, consumer: str | None = None,
        category: str | None = None,
    ) -> None:
        relative = self.relative_path(path)
        if category is None:
            if relative.endswith("_project/03_project_state.json"):
                category = "canonical_state"
            elif relative.endswith("_project/state_log.jsonl"):
                category = "history_log"
            elif relative.endswith(("_project/06_bug_fixes.md", "_project/07_pending_clarifications.md")):
                category = "historical_projection"
            else:
                category = "document"
        self.entries.append({
            "path": relative,
            "operation": operation,
            "content_bytes": content_bytes,
            "bytes": physical_bytes,
            "cached": cached,
            "consumer": consumer or _caller(),
            "phase": self.phase,
            "category": category,
        })

    def read_text(
        self, path: Path, reader: Callable[[], str], *, encoding: str | None,
        errors: str | None, newline: str | None,
        raw_reader: Callable[[], bytes] | None = None,
    ) -> str:
        signature = self._check_signature(path)
        key = ("text", self.relative_path(path), encoding or "", errors or "", newline or "")
        cached = key in self._cache
        raw_cached = False
        if cached:
            cached_signature, value, content_bytes = self._cache[key]
            if cached_signature != signature:
                raise SnapshotChanged(f"la dependencia cambió durante la consulta: {path}")
            physical_bytes = 0
        else:
            if raw_reader is not None:
                raw, raw_cached = self._raw_read(path, raw_reader, signature)
                with io.BytesIO(raw) as stream:
                    with io.TextIOWrapper(
                        stream, encoding=encoding, errors=errors or "strict", newline=newline,
                    ) as decoded:
                        value = decoded.read()
                content_bytes = len(raw)
                physical_bytes = 0 if raw_cached else content_bytes
            else:
                value = reader()
                content_bytes = signature[2]
                physical_bytes = content_bytes
            if not isinstance(value, str):
                raise TypeError("Path.read_text no devolvió texto")
            self._cache[key] = (signature, value, content_bytes)
            # A read_text may reuse the shared raw cache populated by a prior
            # read_bytes (e.g. state_log hash + provenance scan). It is still
            # a cache hit from the filesystem's perspective even though the
            # decoded text representation was built for the first time.
            cached = raw_cached if raw_reader is not None else cached
        self._record(
            path, "read_text", content_bytes=content_bytes,
            physical_bytes=physical_bytes, cached=cached,
        )
        return value

    def read_bytes(self, path: Path, reader: Callable[[], bytes]) -> bytes:
        signature = self._check_signature(path)
        value, cached = self._raw_read(path, reader, signature)
        content_bytes = len(value)
        physical_bytes = 0 if cached else content_bytes
        self._record(
            path, "read_bytes", content_bytes=content_bytes,
            physical_bytes=physical_bytes, cached=cached,
        )
        return value

    def _raw_read(
        self, path: Path, reader: Callable[[], bytes],
        signature: tuple[int, int, int, int],
    ) -> tuple[bytes, bool]:
        key = self.relative_path(path)
        cached = key in self._raw_cache
        if cached:
            cached_signature, value = self._raw_cache[key]
            if cached_signature != signature:
                raise SnapshotChanged(f"la dependencia cambió durante la consulta: {path}")
            return value, True
        value = reader()
        if not isinstance(value, bytes):
            raise TypeError("Path.read_bytes no devolvió bytes")
        self._raw_cache[key] = (signature, value)
        return value, False

    def record_manual(
        self, path: Path, *, operation: str, content_bytes: int,
        cached: bool = False, physical_bytes: int | None = None,
        consumer: str | None = None, category: str | None = None,
    ) -> None:
        """Registra un lector que no pasa por ``Path.read_*`` (p.ej. ``open``)."""
        if not isinstance(content_bytes, int) or content_bytes < 0:
            raise ValueError("content_bytes debe ser entero no negativo")
        self._check_signature(path)
        self._record(
            path, operation, content_bytes=content_bytes,
            physical_bytes=content_bytes if physical_bytes is None else physical_bytes,
            cached=cached, consumer=consumer, category=category,
        )

    def verify_snapshot(self) -> None:
        """Comprueba que ninguna dependencia observada cambió antes de emitir."""
        for key, path in self._paths.items():
            current = _stat_signature(path)
            if current is None or current != self._signatures.get(key):
                raise SnapshotChanged(f"la dependencia cambió durante la consulta: {path}")

    def note_unobserved(self, consumer: str, reason: str) -> None:
        entry = {"consumer": str(consumer), "reason": str(reason)}
        if entry not in self.unobserved_reads:
            self.unobserved_reads.append(entry)

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "entries": list(self.entries),
            "unobserved_reads": list(self.unobserved_reads),
        }


@contextlib.contextmanager
def capture_reads(root: Path, *, phase: str = "query") -> Iterator[ReadTrace]:
    """Captura lecturas de pathlib y comparte un snapshot dentro de la fase."""
    trace = ReadTrace(root=root, phase=phase)
    old_text = Path.read_text
    old_bytes = Path.read_bytes

    def traced_text(path: Path, *args: Any, **kwargs: Any) -> str:
        encoding = kwargs.get("encoding", args[0] if len(args) > 0 else None)
        errors = kwargs.get("errors", args[1] if len(args) > 1 else None)
        newline = kwargs.get("newline", args[2] if len(args) > 2 else None)
        return trace.read_text(
            Path(path), lambda: old_text(path, *args, **kwargs),
            raw_reader=lambda: old_bytes(path),
            encoding=encoding, errors=errors, newline=newline,
        )

    def traced_bytes(path: Path, *args: Any, **kwargs: Any) -> bytes:
        return trace.read_bytes(Path(path), lambda: old_bytes(path, *args, **kwargs))

    token = _ACTIVE_TRACE.set(trace)
    Path.read_text = traced_text  # type: ignore[assignment]
    Path.read_bytes = traced_bytes  # type: ignore[assignment]
    try:
        yield trace
    finally:
        Path.read_text = old_text  # type: ignore[assignment]
        Path.read_bytes = old_bytes  # type: ignore[assignment]
        _ACTIVE_TRACE.reset(token)


def active_trace() -> ReadTrace | None:
    return _ACTIVE_TRACE.get()


def record_read(
    path: Path, *, operation: str, content_bytes: int,
    cached: bool = False, physical_bytes: int | None = None,
    consumer: str | None = None, category: str | None = None,
) -> None:
    trace = active_trace()
    if trace is not None:
        trace.record_manual(
            path, operation=operation, content_bytes=content_bytes,
            cached=cached, physical_bytes=physical_bytes, consumer=consumer,
            category=category,
        )


def note_unobserved(consumer: str, reason: str) -> None:
    trace = active_trace()
    if trace is not None:
        trace.note_unobserved(consumer, reason)


def write_trace(trace_dir: Path, trace: ReadTrace) -> Path:
    """Persiste una traza fuera del repo, sin volver a entrar en el capture."""
    global _TRACE_SEQUENCE
    trace_dir = Path(trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    _TRACE_SEQUENCE += 1
    path = trace_dir / f"context-read-{os.getpid()}-{time.time_ns()}-{_TRACE_SEQUENCE}.json"
    path.write_text(json.dumps(trace.as_dict(), ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def read_trace_files(trace_dir: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for path in sorted(Path(trace_dir).glob("context-read-*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict):
            documents.append(document)
    return documents


def _trace_entries(read_trace: object) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    if read_trace is None:
        return [], [], []
    if isinstance(read_trace, dict):
        documents = [read_trace]
    elif isinstance(read_trace, list):
        documents = [item for item in read_trace if isinstance(item, dict)]
    else:
        raise ValueError("read_trace debe ser documento o lista de documentos")
    entries: list[dict[str, Any]] = []
    unobserved: list[dict[str, str]] = []
    for document in documents:
        raw_entries = document.get("entries", [])
        if isinstance(raw_entries, list):
            entries.extend(item for item in raw_entries if isinstance(item, dict))
        raw_unobserved = document.get("unobserved_reads", [])
        if isinstance(raw_unobserved, list):
            unobserved.extend(item for item in raw_unobserved if isinstance(item, dict))
    return entries, unobserved, documents


def measure_pages(
    pages: list[dict], *, wire_bytes: list[int] | None = None,
    read_paths: list[str] | None = None, read_trace: object = None,
) -> dict[str, Any]:
    """Resume páginas, transporte y lecturas observadas de un recorrido.

    ``read_paths`` queda soportado para compatibilidad, pero cuando se entrega
    ``read_trace`` la fuente de verdad de archivos/bytes es la traza del proceso.
    """
    if not isinstance(pages, list) or not all(isinstance(page, dict) for page in pages):
        raise ValueError("pages debe ser una lista de respuestas JSON")
    if wire_bytes is not None and (
        len(wire_bytes) != len(pages)
        or not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in wire_bytes)
    ):
        raise ValueError("wire_bytes debe tener un entero no negativo por página")
    if read_paths is not None and not all(isinstance(path, str) and path for path in read_paths):
        raise ValueError("read_paths debe contener rutas relativas no vacías")

    page_bytes = [state_queries.serialized_context_size(page) for page in pages]
    items = [
        item for page in pages
        for item in page.get("data", {}).get("items", [])
        if isinstance(item, dict)
    ]
    source_items = [item for item in items if isinstance(item.get("path"), str)]
    fragment_keys = [
        (
            item.get("path"), item.get("selector"), item.get("fragment_index"),
            item.get("fragment_count"), item.get("content"),
        )
        for item in source_items
    ]
    document_keys = [
        (item.get("path"), item.get("selector"), item.get("content"))
        for item in source_items
    ]
    fragment_counts = Counter(fragment_keys)
    document_counts = Counter(document_keys)
    emitted_files = sorted({item["path"] for item in source_items})

    entries, unobserved, documents = _trace_entries(read_trace)
    observed = read_trace is not None
    if observed:
        read_files = sorted({str(entry.get("path")) for entry in entries if entry.get("path")})
        read_counts = Counter(path for path in (entry.get("path") for entry in entries) if path)
        physical_entries = [
            entry for entry in entries
            if isinstance(entry.get("bytes"), int) and entry.get("bytes", 0) > 0
        ]
        logical_read_bytes = sum(
            int(entry.get("content_bytes", 0))
            for entry in entries
            if isinstance(entry.get("content_bytes"), int) and entry.get("content_bytes", 0) >= 0
        )
        physical_read_bytes = sum(int(entry.get("bytes", 0)) for entry in physical_entries)
        cache_hits = sum(1 for entry in entries if entry.get("cached") is True)
        consumers = Counter(str(entry.get("consumer", "unknown")) for entry in entries)
        phases = Counter(str(entry.get("phase", "query")) for entry in entries)
        categories = Counter(str(entry.get("category", "uncategorized")) for entry in entries)
        bytes_by_category = Counter({
            category: sum(
                int(entry.get("bytes", 0)) for entry in entries
                if entry.get("category", "uncategorized") == category
            )
            for category in categories
        })
        repeated_read_count = sum(count - 1 for count in read_counts.values() if count > 1)
        repeated_physical_count = sum(
            count - 1 for count in Counter(
                str(entry.get("path")) for entry in physical_entries if entry.get("path")
            ).values() if count > 1
        )
    else:
        read_files = sorted(set(read_paths)) if read_paths is not None else emitted_files
        logical_read_bytes = None
        physical_read_bytes = None
        cache_hits = None
        consumers = Counter()
        phases = Counter()
        categories = Counter()
        bytes_by_category = Counter()
        repeated_read_count = 0
        repeated_physical_count = 0

    return {
        "pages": len(pages),
        "page_data_bytes": page_bytes,
        "total_data_bytes": sum(page_bytes),
        "wire_bytes": sum(wire_bytes) if wire_bytes is not None else None,
        "unique_files": read_files,
        "unique_file_count": len(read_files),
        "emitted_unique_files": emitted_files,
        "fragment_count": len(fragment_keys),
        "repeated_fragment_count": sum(count - 1 for count in fragment_counts.values() if count > 1),
        "repeated_document_count": sum(count - 1 for count in document_counts.values() if count > 1),
        "read_source": "observed_trace" if observed else "declared_or_emitted",
        "read_operations": len(entries) if observed else None,
        "read_unique_file_count": len(read_files) if observed else None,
        "read_bytes": physical_read_bytes,
        "logical_read_bytes": logical_read_bytes,
        "cache_hit_count": cache_hits,
        "repeated_read_count": repeated_read_count if observed else None,
        "repeated_physical_read_count": repeated_physical_count if observed else None,
        "reads_by_consumer": dict(sorted(consumers.items())),
        "reads_by_phase": dict(sorted(phases.items())),
        "read_operations_by_category": dict(sorted(categories.items())),
        "read_bytes_by_category": dict(sorted(bytes_by_category.items())),
        "read_events": entries,
        "unobserved_reads": unobserved,
        "trace_documents": len(documents) if observed else 0,
    }


def _query_cli(root: Path, view: str, module_id: str | None, cursor: str | None, env: dict[str, str]) -> tuple[dict, int]:
    command = [sys.executable, "-B", ".agent/scripts/state_manager.py", "query", "--view", view]
    if module_id is not None:
        command.extend(("--module-id", module_id))
    if cursor is not None:
        command.extend(("--cursor", cursor))
    process = subprocess.run(command, cwd=root, env=env, capture_output=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(
            f"CLI query falló ({process.returncode}): "
            f"{process.stderr.decode('utf-8', 'replace')}"
        )
    try:
        page = json.loads(process.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("CLI no devolvió JSON de contexto") from exc
    return page, len(process.stdout)


def _mcp_roundtrip(process: subprocess.Popen[bytes], request: dict[str, Any]) -> tuple[dict, int]:
    payload = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(payload)
    process.stdin.flush()
    response_line = process.stdout.readline()
    if not response_line:
        raise RuntimeError("MCP terminó antes de responder")
    try:
        return json.loads(response_line.decode("utf-8")), len(response_line) + len(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MCP no devolvió JSON-RPC válido") from exc


def measure_transport(
    root: Path, *, view: str, module_id: str | None = None,
    transport: str = "cli",
) -> dict[str, Any]:
    """Ejecuta un recorrido real y devuelve sus métricas observadas."""
    if transport not in {"cli", "mcp"}:
        raise ValueError("transport debe ser cli o mcp")
    root = Path(root).resolve()
    with tempfile.TemporaryDirectory(prefix="framework-context-trace-") as trace_name:
        trace_dir = Path(trace_name)
        env = os.environ.copy()
        env[TRACE_ENV] = str(trace_dir)
        pages: list[dict] = []
        wire: list[int] = []
        initialization_wire = 0
        discovery_wire = 0
        process_count = 0

        if transport == "cli":
            cursor: str | None = None
            while True:
                page, size = _query_cli(root, view, module_id, cursor, env)
                pages.append(page)
                wire.append(size)
                process_count += 1
                if page.get("complete"):
                    break
                cursor = page.get("next_cursor")
                if not isinstance(cursor, str) or not cursor:
                    raise RuntimeError("CLI entregó una página incompleta sin cursor")
        else:
            process = subprocess.Popen(
                [sys.executable, "-B", ".agent/scripts/state_manager.py"],
                cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                _, init_wire = _mcp_roundtrip(process, {
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26"},
                })
                initialization_wire += init_wire
                assert process.stdin is not None
                notification = (json.dumps({
                    "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
                }, ensure_ascii=False) + "\n").encode("utf-8")
                process.stdin.write(notification)
                process.stdin.flush()
                _, list_wire = _mcp_roundtrip(process, {
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
                })
                discovery_wire += list_wire + len(notification)
                cursor = None
                request_id = 3
                while True:
                    arguments: dict[str, Any] = {"view": view}
                    if module_id is not None:
                        arguments["module_id"] = module_id
                    if cursor is not None:
                        arguments["cursor"] = cursor
                    response, size = _mcp_roundtrip(process, {
                        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                        "params": {"name": "get_work_context", "arguments": arguments},
                    })
                    content = response.get("result", {}).get("content", [])
                    text = content[0].get("text") if content and isinstance(content[0], dict) else None
                    if not isinstance(text, str):
                        raise RuntimeError("MCP tools/call no contiene contexto serializado")
                    page = json.loads(text)
                    pages.append(page)
                    wire.append(size)
                    request_id += 1
                    if page.get("complete"):
                        break
                    cursor = page.get("next_cursor")
                    if not isinstance(cursor, str) or not cursor:
                        raise RuntimeError("MCP entregó una página incompleta sin cursor")
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=30)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
                process_count = 1

        traces = read_trace_files(trace_dir)
        report = measure_pages(pages, wire_bytes=wire, read_trace=traces)
        report.update({
            "transport": transport,
            "view": view,
            "module_id": module_id,
            "initialization_wire_bytes": initialization_wire,
            "discovery_wire_bytes": discovery_wire,
            "call_wire_bytes": sum(wire),
            "process_count": process_count,
        })
        return report


def readable_summary(report: dict[str, Any]) -> str:
    """Resumen corto para humanos; el JSON conserva el detalle auditable."""
    return "\n".join([
        f"Contexto {report.get('transport')} / {report.get('view')} / {report.get('module_id') or '-'}",
        f"páginas={report.get('pages')} datos={report.get('total_data_bytes')}B wire={report.get('wire_bytes')}B",
        f"archivos únicos observados={report.get('read_unique_file_count')} "
        f"lecturas={report.get('read_operations')} bytes_leídos={report.get('read_bytes')}B "
        f"cache_hits={report.get('cache_hit_count')} relecturas={report.get('repeated_read_count')}",
        f"fragmentos_repetidos={report.get('repeated_fragment_count')} "
        f"documentos_repetidos={report.get('repeated_document_count')} "
        f"subprocesos_no_observados={len(report.get('unobserved_reads') or [])}",
    ])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Mide un recorrido real CLI/MCP del contexto progresivo")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--view", required=True, choices=sorted(state_queries.VIEWS))
    parser.add_argument("--module-id")
    parser.add_argument("--transport", choices=("cli", "mcp"), default="cli")
    parser.add_argument("--output", type=Path, help="reporte JSON fuera del workspace")
    args = parser.parse_args(argv)
    try:
        report = measure_transport(
            args.root, view=args.view, module_id=args.module_id, transport=args.transport,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"measurement failed: {exc}", file=sys.stderr)
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(readable_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
