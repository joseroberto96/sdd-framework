#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hook `Stop`: verifica el cierre de turno, corta bucles y avisa de sesión larga.

5.3.0 L4 (ADR-074; FW-314, FW-328, FW-335). Lo llaman el `Stop` de Claude Code
(`.claude/settings.json`) y el de Codex (`codex_hook_runner.py --hook-id stop`).

Qué hace, por este orden:

1. Recordatorio de `/gycd`, una vez por sesión (solo cuando el host lo pide).
2. Cierre de turno: pasa `last_assistant_message` por `validate_turn_close` en
   modo `live` (D1: aquí no hay reglas propias). Con errores bloquea UNA vez con el `fix`
   de cada defecto y pide solo las líneas de cierre corregidas (FW-402); los avisos no bloquean.
3. Bucles (solo Claude Code): el mismo test en rojo con la misma firma, o el
   mismo fichero editado y revertido, `LOOP_THRESHOLD` veces. Bloquea una vez.
4. Sesión larga (solo Claude Code): avisa al builder, sin bloquear, una vez por
   compactación nueva y una vez al pasar de `CONTEXT_THRESHOLD`.

Garantías que revisa `security`:

- No ejecuta nada del mensaje ni de la transcripción: solo los lee.
- Solo escribe en su directorio de telemetría, fuera del repo
  (`~/.cache/framework-v4/<proyecto>-<huella>/`): un JSONL con códigos y contadores
  y el estado mínimo de cada sesión. Nunca guarda texto del mensaje. Esa carpeta y
  `sessions/` deben ser directorios reales y propios: si son un enlace, no escribe.
- Si algo falla deja pasar y lo registra como `failed_open`. El fallo es por
  comprobación: una transcripción ilegible apaga los bucles y la sesión larga, no
  el cierre que sí se pudo validar, y al revés. El tiempo agotado y cualquier otra
  excepción dejan pasar el turno entero. Nunca bloquea dos veces seguidas, ni con
  `stop_hook_active` activo ni sin él: ese límite es suyo, no del arnés. Si no
  puede guardar el estado de sesión que lo sostiene, no bloquea y avisa al builder.
- No evalúa en modo `template` ni en subagentes. Con `turn_close_hook: off` en
  `_project/15_environment_profile.md` no bloquea ni avisa de sesión larga, solo
  registra; el recordatorio de `/gycd` es anterior al hook y sigue saliendo.

Versiones del arnés (D19): el contrato del evento (`stop_hook_active`,
`last_assistant_message`, continuación con `decision: "block"`) está verificado
en las de `VERIFIED_HARNESS`. Con una anterior el doctor avisa; si el campo no
llega, el hook deja pasar y lo registra.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # plataforma sin flock: el hook degrada sin cerrojo
    fcntl = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:  # `turn_close` y `session_metrics` se importan al usarlos
    sys.path.insert(0, _SCRIPTS)

HOSTS = ("claude", "codex")
VERIFIED_HARNESS = {"claude": "2.1.296", "codex": "0.162.1"}

HOOK_TIMEOUT_S = 8  # por debajo del `timeout` declarado en la configuración del host
TAIL_BYTES = 4 * 1024 * 1024  # de la transcripción solo se lee el final
LOOP_THRESHOLD = 3  # línea base de L1: 3 rojos seguidos con la misma firma
CONTEXT_THRESHOLD = 200_000  # tokens de contexto en el último turno (provisional, L11 lo revisa)
TELEMETRY_MAX_BYTES = 5 * 1024 * 1024
SESSION_STATE_MAX_BYTES = 64 * 1024
SESSION_STATE_MAX_AGE_S = 30 * 24 * 3600
LOCK_WAIT_S = 3.0  # espera máxima por el cerrojo de la sesión: por debajo de HOOK_TIMEOUT_S
LOCK_POLL_S = 0.02
PRUNE_DEFAULT_DAYS = 90
PRUNE_ENTRY_LIMIT = 2000  # tope de entradas que recorre la limpieza de la caché
CACHE_WARN_FOLDERS = 50  # el doctor avisa por encima de este número de carpetas
_MAX_KEYS = 64
_FIX_MAX_CHARS = 300  # el `fix` puede citar un trozo del mensaje: se acota

STATE_REL = Path("_project/03_project_state.json")
PROFILE_REL = Path("_project/15_environment_profile.md")
MANIFEST_REL = Path(".agent/workflows/manifest.json")

RESULT_PASS = "pass"
RESULT_BLOCKED = "blocked"
RESULT_PASSED_AFTER_BLOCK = "passed_after_block"
RESULT_FAILED_OPEN = "failed_open"

LOCK_BUSY_MESSAGE = ("Otra invocación del hook de cierre de turno de esta misma sesión tenía el cerrojo del estado: "
                     "el cierre de este turno no se verificó.")
GYCD_MESSAGE = "Si trabajaste en el proyecto, ejecuta /gycd para cerrar ordenadamente y guardar el estado."
STATE_UNSAVED_MESSAGE = ("El hook de cierre de turno no pudo guardar su estado en ~/.cache/framework-v4 y no "
                         "verifica el cierre: revisa los permisos de esa carpeta.")

_FINGERPRINT_RE = re.compile(r"[0-9a-f]{12}")  # huella del turno: un hash, nunca contenido
_CACHE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}-[0-9a-f]{12}\Z")  # el nombre que da telemetry_dir (sin salto final)

_SWITCH_RE = re.compile(
    r"(?i)^ {0,3}(?:[-*][ \t]*)?(?:\*\*|`)?turn_close_hook(?:\*\*|`)?[ \t]*:[ \t]*([^\n]*)$"
)
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


class _Timeout(Exception):
    """El hook agotó su tiempo máximo."""


# ------------------------------------------------------------- Telemetría


def _safe_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))[:80] or "x"


def _short_hash(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8", "replace")).hexdigest()[:12]


def telemetry_dir(root: Path) -> Path:
    """Único sitio donde escribe el hook: fuera del repo y propio de cada proyecto."""
    resolved = Path(root).resolve()
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", resolved.name)[:40] or "proyecto"
    return Path.home() / ".cache" / "framework-v4" / f"{name}-{_short_hash(str(resolved))}"


def telemetry_path(root: Path) -> Path:
    return telemetry_dir(root) / "turn_close.jsonl"


_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # un FIFO puesto en el nombre no puede colgar el open
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _O_NOFOLLOW | _O_CLOEXEC
_DIR_FD_OK = all(func in os.supports_dir_fd for func in (os.open, os.mkdir, os.stat, os.unlink, os.rmdir, os.rename))
LOG_NAME = "turn_close.jsonl"


def _is_mine(info: os.stat_result) -> bool:
    return not hasattr(os, "geteuid") or info.st_uid == os.geteuid()


def is_own_regular_file(fd: int) -> bool:
    """Fichero regular, propio y sin otros nombres: un enlace duro ajeno no es del hook."""
    info = os.fstat(fd)
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and _is_mine(info)


def open_own_dir(parent_fd: int, name: str, *, create: bool = True) -> int | None:
    """Abre `name` dentro de `parent_fd` como descriptor, sin seguir enlaces.

    Se valida sobre el descriptor (directorio, propio, 0700) y todo lo posterior va con
    `dir_fd` relativo a él: sustituir la carpeta por otra después no redirige nada.
    Devuelve None si no se puede (el llamador deja pasar)."""
    if not _DIR_FD_OK:
        return None
    try:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode) and _is_mine(info):
            if info.st_mode & 0o077:
                os.fchmod(fd, 0o700)
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


def is_own_directory_fd(fd: int) -> bool:
    try:
        info = os.fstat(fd)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and _is_mine(info)


def open_telemetry_dir(root: Path, *, create: bool = True) -> int | None:
    """Descriptor de la carpeta de telemetría del proyecto (la de `telemetry_dir`)."""
    directory = telemetry_dir(root)
    if not _DIR_FD_OK:
        return None
    try:
        if create:
            directory.parent.mkdir(parents=True, exist_ok=True)
        parent_fd = os.open(directory.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _O_CLOEXEC)
    except OSError:
        return None
    try:
        if not is_own_directory_fd(parent_fd):
            return None
        return open_own_dir(parent_fd, directory.name, create=create)
    finally:
        os.close(parent_fd)


def _open_log(dir_fd: int) -> int | None:
    """Abre `turn_close.jsonl` para añadir, rotándolo si pasó el tope. None si no es del hook."""
    for attempt in range(2):
        fd = os.open(LOG_NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC, 0o600,
                     dir_fd=dir_fd)
        keep = False
        try:
            if not is_own_regular_file(fd):
                return None
            if attempt or os.fstat(fd).st_size <= TELEMETRY_MAX_BYTES:
                keep = True
                return fd
            os.replace(LOG_NAME, LOG_NAME + ".1", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        finally:
            if not keep:
                os.close(fd)
    return None


def _record(root: Path, event: str, **fields: Any) -> None:
    """Añade una línea de telemetría. Nunca lanza: registrar no puede romper el turno."""
    try:
        dir_fd = open_telemetry_dir(root)
        if dir_fd is None:
            return
        try:
            line = json.dumps(
                {"v": 1, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **fields},
                ensure_ascii=False, separators=(",", ":"),
            )
            fd = _open_log(dir_fd)
            if fd is None:
                return
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        finally:
            os.close(dir_fd)
    except _Timeout:
        raise  # el tiempo máximo es de quien evalúa: aquí no se puede perder
    except Exception:  # noqa: BLE001 - incluye un HOME que no se puede resolver
        pass


def _open_sessions_dir(root: Path, *, create: bool) -> int | None:
    project_fd = open_telemetry_dir(root, create=create)
    if project_fd is None:
        return None
    try:
        return open_own_dir(project_fd, "sessions", create=create)
    finally:
        os.close(project_fd)


def _prune_lock(sessions_fd: int, name: str) -> None:
    """Borra un cerrojo viejo solo si se consigue su flock: uno tomado tiene titular y no se toca."""
    if fcntl is None:
        return
    fd = os.open(name, os.O_RDWR | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC, dir_fd=sessions_fd)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if is_own_regular_file(fd) and _same_inode(fd, sessions_fd, name):
            os.unlink(name, dir_fd=sessions_fd)
    except BlockingIOError:
        pass
    finally:
        os.close(fd)


def _prune_sessions(sessions_fd: int) -> None:
    """Borra estados, cerrojos y temporales huérfanos de más de 30 días, sin seguir enlaces."""
    limit = time.time() - SESSION_STATE_MAX_AGE_S
    try:
        with os.scandir(sessions_fd) as entries:
            for index, entry in enumerate(entries):
                if index >= 500:
                    break
                if not entry.name.endswith((".json", ".lock", ".tmp")):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if not (stat.S_ISREG(info.st_mode) and info.st_mtime < limit):
                        continue
                    if entry.name.endswith(".lock"):
                        _prune_lock(sessions_fd, entry.name)
                    else:
                        os.unlink(entry.name, dir_fd=sessions_fd)
                except OSError:
                    continue
    except OSError:
        pass


def _load_session(root: Path, session_id: str, sessions_fd: int | None = None) -> dict[str, Any]:
    own = sessions_fd is None
    try:
        if own:
            sessions_fd = _open_sessions_dir(root, create=False)
            if sessions_fd is None:
                return {}
        try:
            fd = os.open(f"{session_id}.json", os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC, dir_fd=sessions_fd)
            with os.fdopen(fd, "rb") as handle:
                if not is_own_regular_file(handle.fileno()):
                    return {}
                data = json.loads(handle.read(SESSION_STATE_MAX_BYTES).decode("utf-8"))
        finally:
            if own:
                os.close(sessions_fd)  # type: ignore[arg-type]
    except (OSError, ValueError):
        return {}
    return _clean_session(data)


def _clean_session(data: Any) -> dict[str, Any]:
    """Solo las claves propias y con su tipo: un estado dañado es una sesión nueva."""
    if not isinstance(data, dict):
        return {}
    clean: dict[str, Any] = {}
    for key in ("gycd", "context_warned"):
        if data.get(key) is True:
            clean[key] = True
    for key in ("streak", "compact_warned"):
        if type(data.get(key)) is int and data[key] >= 0:
            clean[key] = data[key]
    for key in ("loops", "compact_seen"):
        value = data.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            clean[key] = value[-_MAX_KEYS:]
    turn = data.get("turn")
    if isinstance(turn, str) and _FINGERPRINT_RE.fullmatch(turn):
        clean["turn"] = turn
    return clean


def _save_session(root: Path, session_id: str, session: dict[str, Any], sessions_fd: int | None = None) -> bool:
    """Guarda el estado de la sesión. False si no se pudo: quien llama no debe bloquear.

    Temporal de nombre único (O_EXCL) y renombrado sobre el destino: un enlace duro puesto en el
    nombre del estado se sustituye, no se escribe."""
    own = sessions_fd is None
    tmp: str | None = None
    try:
        if own:
            sessions_fd = _open_sessions_dir(root, create=True)
            if sessions_fd is None:
                return False
        name = f"{session_id}.json"
        try:
            os.stat(name, dir_fd=sessions_fd, follow_symlinks=False)
        except FileNotFoundError:
            _prune_sessions(sessions_fd)  # type: ignore[arg-type]
        tmp = f"{name}.{secrets.token_hex(6)}.tmp"
        payload = json.dumps(session, separators=(",", ":")).encode("utf-8")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC, 0o600, dir_fd=sessions_fd)
        try:
            written = os.write(fd, payload)
        finally:
            os.close(fd)
        if written != len(payload):  # disco lleno: un estado a medias no es un estado
            return False
        os.replace(tmp, name, src_dir_fd=sessions_fd, dst_dir_fd=sessions_fd)
        tmp = None
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if tmp is not None and sessions_fd is not None:
            try:
                os.unlink(tmp, dir_fd=sessions_fd)
            except OSError:
                pass
        if own and sessions_fd is not None:
            os.close(sessions_fd)


def _same_inode(fd: int, dir_fd: int, name: str) -> bool:
    try:
        mine, named = os.fstat(fd), os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    return (mine.st_dev, mine.st_ino) == (named.st_dev, named.st_ino)


def _acquire_lock(sessions_fd: int, name: str) -> int | None:
    """flock exclusivo sobre el fichero de cerrojo, con reintentos no bloqueantes acotados.

    Quien libera borra el fichero antes de soltar: por eso, ya con el cerrojo, se confirma que
    el nombre sigue siendo el mismo inode y, si no, se vuelve a empezar; lo mismo si el fichero
    ya no tiene nombre (nlink 0) al abrirlo. Uno con otro enlace, que no es regular o ajeno se
    rechaza. Al tomarlo se refresca su mtime para que la poda no lo vea viejo."""
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC, 0o600, dir_fd=sessions_fd)
        except FileNotFoundError:
            # Crear justo cuando quien libera borra el mismo nombre puede dar ENOENT (APFS): se reintenta.
            if time.monotonic() >= deadline:
                return None
            time.sleep(LOCK_POLL_S)
            continue
        keep = False
        try:
            if not is_own_regular_file(fd):
                info = os.fstat(fd)
                if not (stat.S_ISREG(info.st_mode) and info.st_nlink == 0 and _is_mine(info)):
                    return None
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if _same_inode(fd, sessions_fd, name):
                    try:
                        os.utime(fd)
                    except OSError:
                        pass
                    keep = True
                    return fd
        except BlockingIOError:
            pass
        finally:
            if not keep:
                os.close(fd)
        if time.monotonic() >= deadline:
            return None
        time.sleep(LOCK_POLL_S)


class _Guard:
    """Cerrojo y carpeta de sesiones de una evaluación.

    `status`: held (cerrojo tomado), unlocked (sin fcntl: degrada sin cerrojo), contended (no se
    consiguió a tiempo) o nostore (no hay carpeta fiable). Solo los dos primeros permiten guardar."""

    def __init__(self) -> None:
        self.sessions_fd: int | None = None
        self.status = "nostore"

    @property
    def usable(self) -> bool:
        return self.status in ("held", "unlocked")


@contextmanager
def _session_guard(root: Path, session_id: str) -> Iterator[_Guard]:
    guard = _Guard()
    lock_fd: int | None = None
    lock_name = f"{session_id}.lock"
    try:
        try:
            guard.sessions_fd = _open_sessions_dir(root, create=True)
            if guard.sessions_fd is not None:
                if fcntl is None:
                    guard.status = "unlocked"
                else:
                    lock_fd = _acquire_lock(guard.sessions_fd, lock_name)
                    guard.status = "held" if lock_fd is not None else "contended"
        except _Timeout:
            raise
        except Exception:  # noqa: BLE001 - sin carpeta o sin cerrojo fiable no se bloquea
            guard.status = "nostore" if guard.sessions_fd is None else "contended"
        yield guard
    finally:
        if lock_fd is not None:
            try:
                os.unlink(lock_name, dir_fd=guard.sessions_fd)  # type: ignore[arg-type]
            except OSError:
                pass
            os.close(lock_fd)
        if guard.sessions_fd is not None:
            os.close(guard.sessions_fd)


# ----------------------------------------------------- Limpieza de la caché


def _cache_root() -> Path:
    return Path.home() / ".cache" / "framework-v4"


def cache_folder_count() -> int:
    """Carpetas propias del hook en la caché. Solo lee; 0 si no hay caché o no se resuelve HOME."""
    count = 0
    try:
        with os.scandir(_cache_root()) as entries:
            for index, entry in enumerate(entries):
                if index >= 10 * PRUNE_ENTRY_LIMIT:
                    break
                if _CACHE_NAME_RE.match(entry.name) and entry.is_dir(follow_symlinks=False):
                    count += 1
    except (OSError, RuntimeError):
        return 0
    return count


class _Unknown(Exception):
    """La carpeta tiene algo que no es del hook (o no cabe en el recorrido): no se toca."""


def _list_known(dir_fd: int, name: str, accepts: Any, budget: list[int], newest: list[float]) -> list[str]:
    """Nombres de un subdirectorio del hook, todos ficheros regulares conocidos."""
    sub_fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    try:
        info = os.fstat(sub_fd)
        if not _is_mine(info):
            raise _Unknown
        newest[0] = max(newest[0], info.st_mtime)
        names: list[str] = []
        with os.scandir(sub_fd) as entries:
            for entry in entries:
                budget[0] -= 1
                if budget[0] < 0:
                    raise _Unknown
                item = entry.stat(follow_symlinks=False)
                if not (stat.S_ISREG(item.st_mode) and accepts(entry.name) and _is_mine(item)):
                    raise _Unknown
                newest[0] = max(newest[0], item.st_mtime)
                names.append(entry.name)
        return names
    finally:
        os.close(sub_fd)


def _plan_folder(cache_fd: int, name: str, budget: list[int]) -> tuple[dict[str, Any], float]:
    """(contenido conocido de la carpeta, mtime más reciente de todo). Lanza _Unknown si algo no encaja."""
    fd = os.open(name, _DIR_FLAGS, dir_fd=cache_fd)
    try:
        info = os.fstat(fd)
        if not _is_mine(info):
            raise _Unknown
        newest = [info.st_mtime]
        plan: dict[str, Any] = {"files": [], "dirs": {}}
        with os.scandir(fd) as entries:
            for entry in entries:
                budget[0] -= 1
                if budget[0] < 0:
                    raise _Unknown
                item = entry.stat(follow_symlinks=False)
                if entry.name in (LOG_NAME, LOG_NAME + ".1") and stat.S_ISREG(item.st_mode) and _is_mine(item):
                    plan["files"].append(entry.name)
                    newest[0] = max(newest[0], item.st_mtime)
                elif entry.name == "sessions" and stat.S_ISDIR(item.st_mode):
                    plan["dirs"]["sessions"] = _list_known(
                        fd, "sessions", lambda n: n.endswith((".json", ".lock", ".tmp")), budget, newest)
                elif entry.name == "smoke" and stat.S_ISDIR(item.st_mode):
                    plan["dirs"]["smoke"] = _list_known(fd, "smoke", lambda n: n.endswith(".jsonl"), budget, newest)
                else:
                    raise _Unknown
        return plan, newest[0]
    finally:
        os.close(fd)


def _delete_folder(cache_fd: int, name: str, plan: dict[str, Any]) -> None:
    """Borra lo planeado con dir_fd: unlink y rmdir no siguen enlaces, y no hay rmtree."""
    fd = os.open(name, _DIR_FLAGS, dir_fd=cache_fd)
    try:
        for sub, names in plan["dirs"].items():
            sub_fd = os.open(sub, _DIR_FLAGS, dir_fd=fd)
            try:
                for item in names:
                    os.unlink(item, dir_fd=sub_fd)
            finally:
                os.close(sub_fd)
            os.rmdir(sub, dir_fd=fd)
        for item in plan["files"]:
            os.unlink(item, dir_fd=fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=cache_fd)


def prune_cache(root: Path | None = None, *, days: int = PRUNE_DEFAULT_DAYS, apply: bool = False,
                limit: int = PRUNE_ENTRY_LIMIT, now: float | None = None) -> dict[str, Any]:
    """Limpieza a petición de la caché del hook (nunca la llama el hook).

    Candidata solo la carpeta propia, de nombre propio, que no es la del proyecto en curso, con
    solo entradas conocidas y todo más antiguo que `days`. Sin `apply` solo lista."""
    if days < 1:
        raise ValueError("days debe ser 1 como mínimo: con 0 cualquier carpeta de otro proyecto sería candidata")
    result: dict[str, Any] = {"candidates": [], "deleted": [], "skipped": 0, "kept": 0, "failed": 0, "truncated": False}
    current = telemetry_dir(Path(root) if root is not None else ROOT).name
    cutoff = (time.time() if now is None else now) - days * 24 * 3600
    try:
        cache_fd = os.open(_cache_root(), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _O_CLOEXEC)
    except OSError:
        return result
    if not is_own_directory_fd(cache_fd):
        os.close(cache_fd)
        return result
    budget = [limit]
    try:
        names: list[str] = []
        with os.scandir(cache_fd) as entries:
            for entry in entries:
                budget[0] -= 1
                if budget[0] < 0:
                    result["truncated"] = True
                    break
                names.append(entry.name)
        for name in sorted(names):
            if not _CACHE_NAME_RE.match(name):
                continue
            if name == current:
                result["kept"] += 1
                continue
            try:
                info = os.stat(name, dir_fd=cache_fd, follow_symlinks=False)
                if not (stat.S_ISDIR(info.st_mode) and _is_mine(info)):
                    raise _Unknown
                plan, newest = _plan_folder(cache_fd, name, budget)
            except (_Unknown, OSError):
                result["skipped"] += 1
                if budget[0] < 0:
                    result["truncated"] = True
                continue
            if newest >= cutoff:
                result["kept"] += 1
                continue
            result["candidates"].append(name)
            if apply:
                try:
                    _delete_folder(cache_fd, name, plan)
                    result["deleted"].append(name)
                except OSError:
                    result["failed"] += 1
    finally:
        os.close(cache_fd)
    return result


def _prune_command(days: int, apply: bool) -> int:
    try:
        result = prune_cache(days=days, apply=apply)
    except RuntimeError:
        print("No se pudo resolver HOME: no hay caché que limpiar.", file=sys.stderr)
        return 1
    mode = "Borrado" if apply else "Ensayo (no se borra nada; añade --apply para borrar)"
    print(f"{mode}: carpetas de ~/.cache/framework-v4 sin actividad en {days} días o más")
    for name in result["candidates"]:
        print(f"  {name}" + ("  [borrada]" if name in result["deleted"] else ""))
    print(f"Candidatas: {len(result['candidates'])} · borradas: {len(result['deleted'])} · "
          f"omitidas (contenido ajeno o no legible): {result['skipped']} · recientes o en curso: {result['kept']} · "
          f"fallos: {result['failed']}")
    if result["truncated"]:
        print(f"Recorrido acotado a {PRUNE_ENTRY_LIMIT} entradas: puede haber más carpetas sin mirar.")
    return 0


# ------------------------------------------------------- Lectura del proyecto


def _read_state(root: Path) -> dict[str, Any] | None:
    """Estado canónico, solo lectura. None si no existe (no hay proyecto que evaluar)."""
    path = Path(root) / STATE_REL
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("el estado no es un objeto JSON")
    return data


def _strip_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """(línea sin sus comentarios HTML, si queda uno abierto al acabar la línea)."""
    kept = ""
    while line:
        if in_comment:
            end = line.find("-->")
            if end < 0:
                break
            line, in_comment = line[end + 3:], False
        else:
            start = line.find("<!--")
            if start < 0:
                kept += line
                break
            kept += line[:start]
            line, in_comment = line[start + 4:], True
    return kept, in_comment


def hook_mode(root: Path) -> str:
    """`off` si el perfil lo pide (criterio de reversión de ADR-074); si no, `on`.

    Gana la última declaración con valor `on` u `off`. Lo que va dentro de un bloque
    de código (con valla o con cuatro espacios de sangría) o de un comentario HTML es
    un ejemplo, no una declaración (FW-350). Vallas y comentarios se leen juntos: un
    comentario no empieza dentro de una valla ni una valla dentro de un comentario."""
    try:
        text = (Path(root) / PROFILE_REL).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "on"
    mode = "on"
    fence = ""
    in_comment = False
    for line in text.splitlines():
        if not fence:
            line, in_comment = _strip_comments(line, in_comment)
        opener = _FENCE_RE.match(line)
        if opener:
            run, rest = opener.groups()
            if not fence:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                fence = ""
            continue
        match = None if fence else _SWITCH_RE.match(line)
        if match:
            value = match.group(1).split("#", 1)[0].strip().strip("`* ").strip().lower()
            if value in ("on", "off"):
                mode = value
    return mode


def _active_module(state: dict[str, Any]) -> str:
    statuses = state.get("module_statuses")
    if not isinstance(statuses, dict):
        return ""
    for name, value in statuses.items():
        status = value.get("status") if isinstance(value, dict) else value
        if status == "in_progress":
            return re.sub(r"[^\w.\-]", "_", str(name))[:80]
    return ""


def _is_subagent(event: dict[str, Any]) -> bool:
    return event.get("hook_event_name") == "SubagentStop" or bool(event.get("agent_id"))


# ------------------------------------------------------------ Transcripción


def _tail_objects(transcript_path: Any) -> list[dict[str, Any]]:
    """Objetos JSON del final de la transcripción. Lanza si no se puede leer."""
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        raise ValueError("el evento no trae transcript_path")
    path = Path(transcript_path).expanduser()
    if path.suffix != ".jsonl" or not path.is_file():
        raise ValueError("transcript_path no es un fichero .jsonl")
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        start = max(0, size - TAIL_BYTES)
        handle.seek(start)
        raw = handle.read(TAIL_BYTES)
    lines = raw.split(b"\n")
    if start:
        lines = lines[1:]  # la primera línea puede venir cortada
    objects: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    if not objects:
        raise ValueError("la transcripción no tiene líneas legibles")
    return objects


def _loop_finding(objects: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Bucle en curso, con la definición del medidor (`session_metrics`, D1)."""
    import session_metrics

    analysis = session_metrics.analyze_loop_events(session_metrics.events_from_objects(objects))
    trailing = analysis["trailing"]
    if trailing["count"] >= LOOP_THRESHOLD:
        return {"kind": "test", "count": trailing["count"],
                "key": _short_hash("test", trailing["command"], trailing["signature"])}
    reverts = analysis["reverts"]
    if reverts:
        target, count = max(reverts.items(), key=lambda item: item[1])
        if count >= LOOP_THRESHOLD:
            return {"kind": "revert", "count": count, "key": _short_hash("revert", target)}
    return None


def _session_shape(objects: list[dict[str, Any]]) -> tuple[list[str], int]:
    """(huellas de las compactaciones vistas en el final, contexto del último turno)."""
    compactions: list[str] = []
    context = 0
    for obj in objects:
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "compact_boundary":
            compactions.append(_short_hash(str(obj.get("uuid") or obj.get("timestamp") or len(compactions))))
        elif kind == "assistant" and not obj.get("isSidechain"):
            message = obj.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if isinstance(usage, dict):
                total = sum(
                    int(usage.get(key) or 0)
                    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                )
                if total:
                    context = total
    return compactions, context


# ----------------------------------------------------------------- Núcleo


@contextmanager
def _deadline(seconds: int) -> Iterator[None]:
    """Tiempo máximo propio (además del que declare el host)."""
    usable = hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()
    if not usable:
        yield
        return

    def _expired(signum: int, frame: Any) -> None:
        raise _Timeout()

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _validate_close(event: dict[str, Any], root: Path) -> tuple[list[Any], list[str]]:
    """(defectos de severidad error, códigos de aviso) del último mensaje."""
    import turn_close

    text = event.get("last_assistant_message")
    if not isinstance(text, str):
        raise ValueError("el evento no trae last_assistant_message")
    manifest = json.loads((root / MANIFEST_REL).read_text(encoding="utf-8"))
    defects = turn_close.validate_turn_close(text, manifest=manifest, mode="live")
    errors = [d for d in defects if d.severity == turn_close.SEVERITY_ERROR]
    warnings = [d.code for d in defects if d.severity != turn_close.SEVERITY_ERROR and d.code in turn_close.ALL_CODES]
    return errors, warnings


def _evaluate(event: dict[str, Any], host: str, root: Path, gycd: bool) -> dict[str, Any]:
    if _is_subagent(event):
        return {}
    session_id = _safe_id(event.get("session_id"))
    state: dict[str, Any] | None = None
    state_error: Exception | None = None
    try:
        state = _read_state(root)
    except (OSError, ValueError) as exc:
        state_error = exc
    applies = state_error is None and state is not None and state.get("framework_mode") != "template"
    if not (gycd or applies):
        # Nada que cargar ni guardar: no se toca la caché (un proyecto sin estado no deja nada bajo HOME).
        if state_error is not None:
            _record(root, "turn_close_check", result=RESULT_FAILED_OPEN, stage="state",
                    error=type(state_error).__name__, host=host, session=session_id)
        return {}
    # El cerrojo cubre cargar -> decidir -> guardar: una segunda invocación de la misma sesión espera
    # y lee la racha ya guardada, así que no bloquea otra vez.
    with _session_guard(root, session_id) as guard:
        return _evaluate_session(event, host, root, gycd, session_id, guard, state, state_error)


def _evaluate_session(event: dict[str, Any], host: str, root: Path, gycd: bool, session_id: str, guard: _Guard,
                      state: dict[str, Any] | None, state_error: Exception | None) -> dict[str, Any]:
    session = _load_session(root, session_id, guard.sessions_fd) if guard.usable else {}
    saved = json.dumps(session, sort_keys=True)
    base = {"host": host, "session": session_id}
    if guard.status == "contended":
        _record(root, "turn_close_check", result=RESULT_FAILED_OPEN, stage="lock", error="LockUnavailable", **base)
    messages: list[str] = []
    if gycd and not session.get("gycd"):
        session["gycd"] = True
        messages.append(GYCD_MESSAGE)

    def persist() -> bool:
        """Guarda el estado de sesión solo si cambió. False si hacía falta y no se pudo."""
        nonlocal saved
        current = json.dumps(session, sort_keys=True)
        if current != saved:
            if not guard.usable or not _save_session(root, session_id, session, guard.sessions_fd):
                return False
            saved = current
        return True

    def finish(output: dict[str, Any]) -> dict[str, Any]:
        persist()
        if messages:
            output["systemMessage"] = " ".join(messages)
        return output

    if state_error is not None:
        _record(root, "turn_close_check", result=RESULT_FAILED_OPEN, stage="state",
                error=type(state_error).__name__, **base)
        return finish({})
    if state is None or state.get("framework_mode") == "template":
        return finish({})

    mode = hook_mode(root)
    base["mode"] = mode
    active = event.get("stop_hook_active") is True
    reasons: list[str] = []

    # 1. Cierre de turno.
    close_failed = False
    errors: list[Any] = []
    warnings: list[str] = []
    try:
        errors, warnings = _validate_close(event, root)
    except _Timeout:
        raise
    except Exception as exc:  # noqa: BLE001 - D2: si el hook falla, deja pasar y lo registra
        close_failed = True
        _record(root, "turn_close_check", result=RESULT_FAILED_OPEN, stage="close", error=type(exc).__name__, **base)

    # 2 y 3. Bucles y sesión larga: solo donde la transcripción tiene el formato del medidor.
    loop: dict[str, Any] | None = None
    if host == "claude":
        objects: list[dict[str, Any]] | None = None
        try:
            objects = _tail_objects(event.get("transcript_path"))
        except _Timeout:
            raise
        except Exception as exc:  # noqa: BLE001
            _record(root, "turn_close_check", result=RESULT_FAILED_OPEN, stage="transcript", error=type(exc).__name__, **base)
        if objects is not None:
            try:
                loop = _loop_finding(objects)
            except _Timeout:
                raise
            except Exception as exc:  # noqa: BLE001
                _record(root, "turn_close_check", result=RESULT_FAILED_OPEN, stage="loops", error=type(exc).__name__, **base)
            if loop is not None and loop["key"] in session.get("loops", []):
                loop = None  # ya se cortó una vez
            try:
                message = _long_session(objects, session, root, base, emit=mode == "on")
            except _Timeout:
                raise
            except Exception as exc:  # noqa: BLE001
                message = ""
                _record(root, "turn_close_check", result=RESULT_FAILED_OPEN, stage="long_session", error=type(exc).__name__, **base)
            if message:
                messages.append(message)
    if loop is not None:
        reasons.append(
            f"Llevas {loop['count']} intentos sobre lo mismo. No sigas probando: dile al builder que cierre con /gycd "
            "y abra sesión nueva con el prompt corregido, o que pida ayuda con /reconciliar, y termina con el cierre de turno."
        )
    if errors:  # FW-402: solo las líneas de cierre, sin repetir la respuesta; con bucle, también la frase de arriba
        codes, lead = ", ".join(dict.fromkeys(d.code for d in errors)), "con esa frase y" if loop else "solo con"
        reasons.append(
            f"Cierre de turno incompleto ({codes}). Responde {lead} las líneas de cierre corregidas («Siguiente: …» "
            "y, si toca, «Modelo: …»), sin repetir la respuesta anterior: "
            + " ".join(dict.fromkeys(d.fix[:_FIX_MAX_CHARS] for d in errors))
        )

    # Un solo bloqueo seguido, lo marque o no el arnés con `stop_hook_active`. La huella del turno (hash de
    # la sesión y del último mensaje, nunca el mensaje) distingue un turno nuevo de una segunda invocación
    # del mismo cierre: esa no consume la racha ni cambia el estado, así que no vuelve a bloquear (FW-355).
    wants_block = bool(reasons)
    message = event.get("last_assistant_message")
    has_message = isinstance(message, str)
    fingerprint = _short_hash(session_id, message if has_message else "")
    # Con la racha puesta, la misma huella es un duplicado quiera o no bloquear esta vez (tras un bloqueo solo por
    # bucle la segunda invocación ya no ve el bucle; una validación puede fallar de forma transitoria). Sin racha,
    # solo es duplicado el que habría bloqueado. Sin mensaje no hay turno que reconocer: todas las huellas serían
    # la misma y la racha no bajaría nunca, así que ahí no hay duplicados.
    duplicate = (has_message and session.get("turn") == fingerprint
                 and (wants_block or bool(session.get("streak"))))
    limited = active or bool(session.get("streak"))
    block = wants_block and mode == "on" and not limited and not duplicate
    if block:
        session["streak"] = 1
        session["turn"] = fingerprint
    elif not duplicate:
        if session.pop("streak", None):
            session["turn"] = fingerprint  # el paso que consume la racha: su duplicado tampoco bloqueará
    loop_cut = loop is not None and (block or mode == "off")
    if loop_cut:
        session["loops"] = (list(session.get("loops", [])) + [loop["key"]])[-_MAX_KEYS:]
    if not persist():
        # Sin el estado guardado nada garantiza un solo bloqueo seguido: deja pasar (D2).
        # Y sin poder bajar la racha el hook queda sin efecto: se dice, bloqueara o no.
        block = False
        session.clear()
        session.update(json.loads(saved))  # lo no guardado no se reintenta al salir
        if guard.status == "contended":
            messages.append(LOCK_BUSY_MESSAGE)  # ya registrado como stage=lock: no es un fallo de guardado
        else:
            messages.append(STATE_UNSAVED_MESSAGE)
            _record(root, "turn_close_check", result=RESULT_FAILED_OPEN, stage="session", error="OSError", **base)

    if loop_cut:
        _record(root, "loop_detected", module=_active_module(state), count=loop["count"], kind=loop["kind"],
                blocked=block, **base)
    if not close_failed:
        if block and errors:
            result = RESULT_BLOCKED
        elif duplicate and not errors:
            result = RESULT_PASS  # un duplicado sin defecto no "pasó tras un bloqueo": solo es el mismo turno otra vez
        elif limited or duplicate:
            result = RESULT_PASSED_AFTER_BLOCK
        else:
            result = RESULT_PASS
        extra = {"duplicate": True} if duplicate else {}
        _record(root, "turn_close_check", result=result, valid=not errors,
                codes=list(dict.fromkeys(d.code for d in errors)), warnings=list(dict.fromkeys(warnings)),
                stop_hook_active=active, **extra, **base)

    output: dict[str, Any] = {}
    if block:
        output = {"decision": "block", "reason": " ".join(reasons)}
    return finish(output)


def _long_session(objects: list[dict[str, Any]], session: dict[str, Any], root: Path, base: dict[str, Any],
                  *, emit: bool) -> str:
    """Aviso de sesión larga, una vez por umbral. Devuelve el texto para el builder o «»."""
    seen, context = _session_shape(objects)
    known = list(dict.fromkeys(list(session.get("compact_seen", [])) + seen))[-_MAX_KEYS:]
    session["compact_seen"] = known
    compactions = len(known)
    thresholds: list[str] = []
    parts: list[str] = []
    if compactions > int(session.get("compact_warned") or 0):
        session["compact_warned"] = compactions
        thresholds.append("compaction")
        parts.append("ha compactado 1 vez" if compactions == 1 else f"ha compactado {compactions} veces")
    if context >= CONTEXT_THRESHOLD and not session.get("context_warned"):
        session["context_warned"] = True
        thresholds.append("context")
        parts.append(f"el último turno va por unos {round(context / 1000)} k de contexto")
    if not thresholds:
        return ""
    _record(root, "long_session_warned", compactions=compactions, context=context, thresholds=thresholds,
            emitted=emit, **base)
    if not emit:
        return ""
    return ("Sesión larga: " + " y ".join(parts)
            + ". Conviene cerrar con /gycd y pegar el «Siguiente:» en una sesión nueva.")


def evaluate(event: dict[str, Any], *, host: str, root: Path | None = None, gycd: bool = True) -> dict[str, Any]:
    """Salida del hook para el evento `Stop` (`{}` = dejar pasar sin decir nada).

    Puede llevar `decision: "block"` con `reason` (para el agente) y `systemMessage`
    (para el builder). Nunca lanza: ante cualquier fallo deja pasar y lo registra.
    """
    project = Path(root) if root is not None else ROOT
    try:
        if host not in HOSTS:
            raise ValueError("host desconocido")
        if not isinstance(event, dict):
            raise ValueError("el evento no es un objeto JSON")
        with _deadline(HOOK_TIMEOUT_S):
            return _evaluate(event, host, project, gycd)
    except Exception as exc:  # noqa: BLE001 - D2: fail-open
        _record(project, "turn_close_check", result=RESULT_FAILED_OPEN, stage="hook", error=type(exc).__name__,
                host=str(host)[:20])
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hook Stop: cierre de turno, bucles y sesión larga")
    parser.add_argument("--host", choices=HOSTS)
    parser.add_argument("--prune-cache", action="store_true",
                        help="mantenimiento a petición: lista (o con --apply borra) carpetas viejas de la caché")
    parser.add_argument("--days", type=int, help=f"antigüedad mínima para --prune-cache (por defecto {PRUNE_DEFAULT_DAYS})")
    parser.add_argument("--apply", action="store_true", help="con --prune-cache, borra en vez de listar")
    args = parser.parse_args(argv)
    if args.prune_cache:
        if args.days is not None and args.days < 1:
            parser.error("--days debe ser 1 como mínimo")
        return _prune_command(PRUNE_DEFAULT_DAYS if args.days is None else args.days, args.apply)
    if args.apply or args.days is not None:
        parser.error("--days y --apply solo se usan con --prune-cache")
    if args.host is None:
        parser.error("the following arguments are required: --host")
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        event = None
    output = evaluate(event, host=args.host)  # type: ignore[arg-type]
    if output:
        sys.stdout.write(json.dumps(output))  # ASCII: no depende de la codificación de stdout
    return 0


if __name__ == "__main__":
    sys.exit(main())
