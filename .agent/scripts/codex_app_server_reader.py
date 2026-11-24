#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lector acotado y persistente para el wire JSONL de app-server.

El proceso mantiene stdout abierto entre mensajes.  Leer con ``readline``
sobre un ``TextIOWrapper`` no basta para este contrato: puede esperar EOF y no
permite controlar de forma precisa los bytes recibidos.  Este lector trabaja
con los pipes binarios, reensambla fragmentos y drena stderr a una cola de
últimos bytes para no bloquear al hijo.
"""
from __future__ import annotations

import json
import os
import selectors
import time
from typing import Any


DEFAULT_MAX_LINE_BYTES = 1024 * 1024
DEFAULT_MAX_STDOUT_BYTES = 8 * 1024 * 1024
DEFAULT_STDERR_TAIL_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_NO_MESSAGE = object()


class ReaderError(RuntimeError):
    """Error observable del proceso/forma del protocolo."""


class ReaderTimeout(ReaderError):
    """No llegó un mensaje antes del deadline monotónico."""


class ReaderLimitError(ReaderError):
    """Se superó un límite de línea o stdout acumulado."""


class ReaderProtocolError(ReaderError):
    """UTF-8, JSONL o EOF parcial inválido."""


class ReaderEOFError(ReaderProtocolError):
    """El proceso cerró stdout sin otro mensaje completo."""


class AppServerReader:
    """Consume mensajes JSON objeto de pipes persistentes sin esperar EOF."""

    def __init__(
        self,
        stdout: Any,
        stderr: Any | None = None,
        *,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES,
        stderr_tail_bytes: int = DEFAULT_STDERR_TAIL_BYTES,
    ) -> None:
        if stdout is None:
            raise ValueError("app-server stdout no disponible")
        if max_line_bytes <= 0 or max_stdout_bytes <= 0 or stderr_tail_bytes < 0:
            raise ValueError("los límites del lector deben ser positivos")
        self.stdout = stdout
        self.stderr = stderr
        self.max_line_bytes = max_line_bytes
        self.max_stdout_bytes = max_stdout_bytes
        self._stderr_tail_limit = stderr_tail_bytes
        self._buffer = bytearray()
        self._stdout_bytes = 0
        self._stderr_tail = bytearray()
        self._stdout_eof = False
        self._stderr_eof = stderr is None
        self._selector = selectors.DefaultSelector()
        self._fds: dict[int, str] = {}
        self._register(stdout, "stdout")
        if stderr is not None:
            self._register(stderr, "stderr")

    def _register(self, stream: Any, kind: str) -> None:
        fd = int(stream.fileno())
        # Nonblocking reads plus selectors prevent a short read from blocking
        # after a child writes fragments in several syscalls.
        try:
            os.set_blocking(fd, False)
        except (AttributeError, OSError):
            pass
        self._selector.register(fd, selectors.EVENT_READ, kind)
        self._fds[fd] = kind

    @property
    def stdout_bytes(self) -> int:
        return self._stdout_bytes

    @property
    def stderr_tail_bytes(self) -> bytes:
        return bytes(self._stderr_tail)

    def stderr_tail(self) -> str:
        """Return the bounded stderr tail as sanitized text."""
        return self.stderr_tail_bytes.decode("utf-8", "replace")

    def _unregister(self, fd: int) -> None:
        try:
            self._selector.unregister(fd)
        except (KeyError, ValueError):
            pass
        self._fds.pop(fd, None)

    def _append_stderr(self, chunk: bytes) -> None:
        if self._stderr_tail_limit <= 0:
            return
        self._stderr_tail.extend(chunk)
        if len(self._stderr_tail) > self._stderr_tail_limit:
            del self._stderr_tail[: len(self._stderr_tail) - self._stderr_tail_limit]

    def _read_fd(self, fd: int, kind: str) -> None:
        try:
            chunk = os.read(fd, _READ_CHUNK_BYTES)
        except BlockingIOError:
            return
        except OSError as exc:
            raise ReaderProtocolError(f"no se pudo leer stdout/stderr de app-server: {exc}") from exc
        if not chunk:
            if kind == "stdout":
                self._stdout_eof = True
            else:
                self._stderr_eof = True
            self._unregister(fd)
            return
        if kind == "stdout":
            self._stdout_bytes += len(chunk)
            if self._stdout_bytes > self.max_stdout_bytes:
                raise ReaderLimitError("app-server superó el límite acumulado de stdout")
            self._buffer.extend(chunk)
        else:
            self._append_stderr(chunk)

    def _consume_line(self) -> dict[str, Any] | object:
        newline = self._buffer.find(b"\n")
        if newline < 0:
            if len(self._buffer) > self.max_line_bytes:
                raise ReaderLimitError("app-server superó el límite de bytes por línea")
            return _NO_MESSAGE
        if newline > self.max_line_bytes:
            raise ReaderLimitError("app-server superó el límite de bytes por línea")
        raw = bytes(self._buffer[:newline])
        del self._buffer[: newline + 1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReaderProtocolError("app-server emitió UTF-8 inválido") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReaderProtocolError("app-server emitió JSON inválido") from exc
        if not isinstance(payload, dict):
            raise ReaderProtocolError("app-server emitió un valor JSON que no es objeto")
        return payload

    def _select(self, deadline: float) -> list[tuple[selectors.SelectorKey, int]]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReaderTimeout("timeout leyendo mensajes de app-server")
        events = self._selector.select(remaining)
        if not events and time.monotonic() >= deadline:
            raise ReaderTimeout("timeout leyendo mensajes de app-server")
        return events

    def read_message(self, deadline: float) -> dict[str, Any] | None:
        """Read one complete JSON object before ``deadline``.

        Complete lines already buffered are returned before EOF is reported;
        this matters when a child writes multiple lines and then closes.
        """
        while True:
            payload = self._consume_line()
            if payload is not _NO_MESSAGE:
                return payload  # type: ignore[return-value]
            if self._stdout_eof:
                if self._buffer:
                    raise ReaderEOFError("app-server terminó con una línea JSONL parcial")
                # EOF limpio no es un protocolo inválido: el consumidor decide
                # si la fase que esperaba otro mensaje queda unavailable o si
                # el cierre era el final normal del proceso.  Una línea parcial
                # sí se rechaza arriba para no perder un evento.
                return None
            for key, _ in self._select(deadline):
                self._read_fd(int(key.fd), str(key.data))

    def drain_stderr(self, deadline: float) -> None:
        """Drain stderr until EOF, retaining only its bounded tail."""
        while not self._stderr_eof:
            events = self._select(deadline)
            for key, _ in events:
                if str(key.data) == "stderr":
                    self._read_fd(int(key.fd), "stderr")
                else:
                    # Preserve stdout bytes if the caller drains while the
                    # child is still alive; a later read_message can consume
                    # the complete line from the buffer.
                    self._read_fd(int(key.fd), "stdout")

    def close(self) -> None:
        try:
            self._selector.close()
        finally:
            self._fds.clear()


__all__ = [
    "AppServerReader",
    "DEFAULT_MAX_LINE_BYTES",
    "DEFAULT_MAX_STDOUT_BYTES",
    "DEFAULT_STDERR_TAIL_BYTES",
    "ReaderEOFError",
    "ReaderError",
    "ReaderLimitError",
    "ReaderProtocolError",
    "ReaderTimeout",
]
