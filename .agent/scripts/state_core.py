#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""state_core.py - State model: load/save, validation, mutations.

Zero-dependency. Owns 03_project_state.json as the single source of truth.
Projection rendering lives in projections.py; transport in state_manager.py.
NOTE: projections is imported LAZILY inside render_projections to avoid a
circular import (projections imports state_core for the path constants).
Path constants are module-level so projections.py and tests can read/patch them
via the state_core module object at call time.
"""
from __future__ import annotations
import contextlib
import copy
import base64
import json
import os
import sys
import datetime
import hashlib
import re
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _transitions

FRAMEWORK_VERSION = "5.3.3"
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE_PATH = WORKSPACE_ROOT / "_project" / "03_project_state.json"
CORE_FILE_PATH = WORKSPACE_ROOT / "_project" / "core.md"
BUGS_FILE_PATH = WORKSPACE_ROOT / "_project" / "06_bug_fixes.md"
CLARIFICATIONS_FILE_PATH = WORKSPACE_ROOT / "_project" / "07_pending_clarifications.md"
DECISIONS_FILE_PATH = WORKSPACE_ROOT / "_project" / "03_arch_decisions.md"
SESSION_BRIEF_PATH = WORKSPACE_ROOT / "_project" / "00_session_brief.json"

# Higiene del audit trail: `reason` y compañía son texto libre que el motor
# persiste para siempre en `state_log.jsonl`. Un secreto pegado ahí no se puede
# "desregistrar" — la bitácora está encadenada por hash. Retroportado de Boton,
# donde llevaba tiempo en producción (FW-173).
#
# Los patrones son deliberadamente GENÉRICOS. Boton llevaba además móviles
# ecuatorianos (`09\d{8}`), email y cualquier número de 10 dígitos; esos quedan
# fuera del framework porque rechazarían un `reason` legítimo que cite una
# incidencia o un contacto. Un proyecto que los necesite los añade en su capa.
MAX_AUDIT_TEXT_LENGTH = 2000
AUDIT_SECRET_PATTERN = re.compile(
    r"(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?token|bearer)\s*[:=]"
)
AUDIT_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\beyJ[a-z0-9_-]{20,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\b"),  # JWT
    re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),  # número de tarjeta
)

# Extensiones del motor (ADR-052, raíz de PA FW-040): acciones custom del
# producto viven en este archivo, que los upgrades NUNCA tocan (no está en la
# lista rsync de /actualizar-framework). Antes, la única vía era parchear
# state_core.py y el parche se perdía en cada upgrade.
EXTENSIONS_FILE_PATH = WORKSPACE_ROOT / ".agent" / "extensions" / "state_actions.py"

# Catálogo canónico de acciones built-in. Una extensión no puede sombrear
# ninguna (error de carga): el dispatch consulta extensiones solo cuando la
# acción no es del motor, y la colisión declarada a tiempo evita confusión.
BUILTIN_ACTIONS = frozenset({
    "add_active_mcp", "add_closure_rejection", "add_module", "add_work_tier",
    "amend_bug", "append_to_list_field", "ciclo_finished", "ciclo_paused", "ciclo_started",
    "classify_module_spec", "complete_module", "defer_bug_to_tech_debt", "drop_module",
    # PA-FW-037 (5.3.3): retiro de un módulo ya arrancado y re-homing de bugs.
    "abandon_module", "reassign_bug_module",
    "record_approval_receipt",
    "record_checkpoint",
    "record_data_safety_check",
    "record_harvest",
    "record_review_receipt", "invalidate_stale_review", "refresh_approval_receipt", "register_bug",
    "register_clarification", "register_decision", "reopen_module", "resolve_bug",
    "resolve_clarification", "resolve_closure_rejections", "restore_closure_rejections",
    "backfill_closure_rejection_provenance",
    "set_active_module", "set_execution_profile",
    "set_focus", "set_framework_version", "set_phase", "set_session_pointer",
    "record_execution_receipt", "start_independent_review", "supersede_decision",
    "update_last_step", "update_module_status",
    # FW-225: modo mantenimiento (lifecycle: production).
    "set_lifecycle", "open_change", "close_change", "escalate_change",
    # FW-275 (5.2.2 D2): uso real confirmado por el builder.
    "confirm_builder_use",
    # L7 (5.2): respaldo del estado por la bitácora. `seed_snapshot` siembra
    # entidades anteriores al log (FW-252); `acknowledge_state_delta` es el
    # escape auditado del gate de estado respaldado (N2).
    "seed_snapshot", "acknowledge_state_delta",
})

# Acciones de extensión de producto que ya no se registran (L6, L12b): sus
# eventos están en bitácoras ya escritas. NO son built-in: en vivo siguen siendo
# "Acción desconocida"; solo el replay las salta con aviso.
#   drift_fix                            Example CLI (nunca llegó a registrarse)
#   set_dependencies                     Research App
#   retire_dependency, set_stack_frontend  Example Lab
RETIRED_REPLAY_ACTIONS = frozenset({
    "drift_fix", "set_dependencies", "retire_dependency", "set_stack_frontend",
})


def _builder_facing_commands(root: Path) -> frozenset[str]:
    """Return the public command verbs declared by the local manifest.

    The session pointer is a persisted contract, so accepting a command that
    is merely known to this Python process would let an internal workflow leak
    into the builder-facing continuation.  The manifest is the source of
    truth for that boundary; an absent or malformed manifest fails closed when
    a pointer actually needs command validation.
    """
    manifest_path = Path(root) / ".agent" / "workflows" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"next_command no se puede validar: falta {manifest_path}"
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"next_command no se puede validar: manifest.json inválido ({exc})"
        ) from exc

    commands = manifest.get("commands") if isinstance(manifest, dict) else None
    if not isinstance(commands, list):
        raise ValueError(
            "next_command no se puede validar: manifest.json.commands debe ser una lista"
        )

    public: set[str] = set()
    for entry in commands:
        if not isinstance(entry, dict) or entry.get("builder_facing") is not True:
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or name.strip().startswith("/"):
            raise ValueError(
                "next_command no se puede validar: manifest.json contiene un "
                "builder_facing sin nombre válido"
            )
        public.add(f"/{name.strip()}")
    if not public:
        raise ValueError(
            "next_command no se puede validar: manifest.json no declara comandos builder-facing"
        )
    return frozenset(public)


def validate_session_pointer(
    session: object,
    *,
    root: Path | None = None,
) -> None:
    """Validate the persisted builder-facing continuation pointer.

    ``next_command`` is deliberately an exact command token (or the explicit
    blocking sentinel); ``next_action`` owns the complete instruction.  No
    normalization or migration happens here: callers receive a clear error
    and can repair both fields through ``set_session_pointer``.

    A missing session block remains compatible with template/legacy states.
    A partial session containing only ``next_action`` also keeps the old
    projection default.  Once ``next_command`` is present, both fields form an
    atomic contract and must be valid together.
    """
    if session is None:
        return
    if not isinstance(session, dict):
        raise ValueError("session debe ser objeto")

    if "next_action" in session and not isinstance(session["next_action"], str):
        raise ValueError("session.next_action debe ser string")

    if "next_command" not in session:
        return

    command = session["next_command"]
    if not isinstance(command, str) or not command.strip():
        raise ValueError("session.next_command debe ser un string no vacío")
    if command != command.strip():
        raise ValueError(
            "session.next_command no debe tener espacios exteriores ni prompt embebido"
        )

    action = session.get("next_action")
    if not isinstance(action, str) or not action.strip():
        raise ValueError(
            "session.next_action debe contener la instrucción completa cuando "
            "next_command está presente"
        )

    if command == "ninguno":
        return

    builder_commands = _builder_facing_commands(Path(root or WORKSPACE_ROOT))
    allowed = set(builder_commands)
    if "/ciclo" in builder_commands:
        allowed.update({"/ciclo fase", "/ciclo fase --paralelo"})

    if command in allowed:
        return

    base_command = command.split(maxsplit=1)[0]
    if base_command.startswith("/") and base_command not in builder_commands:
        raise ValueError(
            "session.next_command debe ser un comando builder-facing de "
            f"manifest.json; no se permite un workflow interno o desconocido: {command!r}"
        )
    raise ValueError(
        "session.next_command solo admite un comando builder-facing exacto, "
        "/ciclo fase, /ciclo fase --paralelo o 'ninguno'; no admite prompts "
        f"embebidos ni separadores: {command!r}"
    )


def _extra_audit_patterns() -> tuple:
    """Patrones de audit ADICIONALES que declare el producto en su capa.

    Los del framework son deliberadamente genéricos: credenciales, JWT y
    tarjetas. Los calibrados a un dominio — móviles de un país, email, un
    formato de identificador local — son ciertos para ese producto y falsos
    positivos para el resto, así que no viajan en la plantilla pero tampoco
    obligan a forkear el motor (FW-182).

    Se declaran como `AUDIT_EXTRA_PATTERNS: tuple[re.Pattern, ...]` en
    `.agent/extensions/state_actions.py`, que los upgrades nunca tocan."""
    path = EXTENSIONS_FILE_PATH
    if not path.exists():
        return ()
    import importlib.util
    spec = importlib.util.spec_from_file_location("state_actions_ext_audit", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ValueError(
            f"Extensiones de estado inválidas en {path} (state_actions.py no importable): {e}"
        ) from e
    extra = getattr(module, "AUDIT_EXTRA_PATTERNS", ())
    if not isinstance(extra, tuple) or not all(hasattr(x, "search") for x in extra):
        raise ValueError(
            f"{path}: AUDIT_EXTRA_PATTERNS debe ser tuple de patrones compilados (re.compile)"
        )
    return extra


def _load_extension_actions() -> dict:
    """Carga EXTENSION_ACTIONS del archivo de extensiones, o {} si no existe.

    Nunca falla en silencio: archivo roto, contrato inválido o colisión con
    una acción built-in → ValueError con mensaje accionable."""
    path = EXTENSIONS_FILE_PATH
    if not path.exists():
        return {}
    import importlib.util
    spec = importlib.util.spec_from_file_location("state_actions_ext", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ValueError(
            f"Extensiones de estado inválidas en {path} (state_actions.py no importable): {e}"
        ) from e
    actions = getattr(module, "EXTENSION_ACTIONS", None)
    if not isinstance(actions, dict):
        raise ValueError(
            f"{path} debe definir EXTENSION_ACTIONS: dict[str, callable(manager, payload, now_iso) -> str]"
        )
    colisiones = sorted(set(actions) & BUILTIN_ACTIONS)
    if colisiones:
        raise ValueError(
            f"Extensiones en {path} sombrean acciones built-in del motor: {colisiones}. "
            "Renombrar la extensión — las acciones del motor siempre ganan."
        )
    return actions


def _event_hash(entry: dict) -> str:
    """Huella canónica de un evento, excluyendo su propio campo `hash`."""
    import hashlib
    material = {k: v for k, v in entry.items() if k != "hash"}
    canonico = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _last_event_hash(log_path: Path) -> str | None:
    """Hash del último evento del log, o None si el log no existe / está vacío.

    Si el último evento es anterior a la cadena (logs de los proyectos que ya
    existían), se calcula su huella al vuelo: así la cadena ancla en el
    contenido real de ese evento y cualquier retoque suyo se detecta, aunque
    él mismo no llevara hash escrito.
    """
    if not log_path.exists():
        return None
    try:
        lineas = [l for l in log_path.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
    except OSError:
        return None
    if not lineas:
        return None
    try:
        ultimo = json.loads(lineas[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(ultimo, dict):
        return None
    return ultimo.get("hash") or _event_hash(ultimo)


def verify_log_chain(log_path: Path) -> list[str]:
    """Comprueba la cadena de hashes del log. Devuelve los problemas ([] si sana).

    Solo audita desde el primer evento que lleva `hash`: los proyectos vivos
    tienen historia previa a la cadena y esa historia no se invalida — pero
    tampoco queda protegida, y conviene saberlo.

    Límite honesto: esto detecta manipulaciones puntuales (editar, borrar,
    insertar). No detecta que alguien reescriba la cadena entera recalculando
    todos los hashes. Contra eso el ancla es git: el log está versionado y una
    reescritura completa aparece como un diff enorme.
    """
    if not log_path.exists():
        return []
    problemas: list[str] = []
    prev_hash: str | None = None
    cadena_empezada = False
    for numero, linea in enumerate(log_path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if not linea.strip():
            continue
        try:
            evento = json.loads(linea)
        except json.JSONDecodeError:
            if cadena_empezada:
                problemas.append(f"evento {numero}: línea ilegible dentro de la cadena")
            continue
        if not isinstance(evento, dict):
            continue
        propio = evento.get("hash")
        if propio is None:
            if cadena_empezada:
                problemas.append(
                    f"evento {numero}: sin hash dentro de una cadena ya iniciada "
                    "(evento insertado a mano, o la cadena se interrumpió)"
                )
            prev_hash = _event_hash(evento)
            continue
        cadena_empezada = True
        recalculado = _event_hash(evento)
        if recalculado != propio:
            problemas.append(
                f"evento {numero} ({evento.get('action')}): el contenido no coincide con su "
                "hash — el evento fue editado después de escribirse"
            )
        elif evento.get("prev") != prev_hash:
            problemas.append(
                f"evento {numero} ({evento.get('action')}): no encadena con el anterior — "
                "se borró o se insertó un evento entre ambos"
            )
        prev_hash = propio
    return problemas


VALID_CLOSURE_REASONS = {
    "critical_bug_exists", "clar_pending", "ac_incomplete", "ac_evidence_missing",
    "module_specs_missing", "data_model_missing", "api_contract_missing",
    "data_safety_missing", "domain_sweep_missing", "expert_audit_missing",
}


def log_err(msg: str) -> None:
    sys.stderr.write(f"[STATE_MANAGER] {msg}\n")
    sys.stderr.flush()


LOCK_RETRY_INTERVAL = 0.05
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_STALE_SECONDS = 60.0


def _read_lock_pid(lock_path: Path) -> int | None:
    """Lee de forma segura el PID del dueño del lockfile, o None si no es legible.

    Usa open() estándar en lugar de Path.read_text() para no registrar el lockfile
    interno de sincronización como dependencia observada en context_measurement.
    """
    try:
        with open(lock_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()
    except OSError:
        return None
    if not content:
        return None
    token = content.split()[0]
    try:
        pid = int(token)
        return pid if pid > 0 else None
    except ValueError:
        return None


def _is_process_alive(pid: int) -> bool:
    """Verifica si un proceso está vivo sin enviarle señal destructiva.

    El framework opera en entornos POSIX (macOS, Linux o Windows vía WSL;
    Windows nativo no está soportado según README.md:81). En POSIX, os.kill(pid, 0)
    determina liveness:
      - Proceso vivo: None o PermissionError (si pertenece a otro usuario) -> True.
      - Proceso muerto: ProcessLookupError (o OSError ESRCH) -> False.
      - Errores de plataforma o inesperados (p. ej. ValueError si corre en Windows nativo):
        se capturan y degradan defensivamente a True para pecar de conservadores y nunca
        romper un lock activo por error.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return True


# FW-233: marca de un lockfile creado bajo `flock` del directorio. Lleva la
# identidad del núcleo porque solo dentro del mismo núcleo "tengo el flock"
# prueba "su titular está muerto" (un contenedor, una VM o una carpeta de red
# son otro dominio de locks).
_LOCK_MARK = "flock"
# Un proceso de una versión anterior escribe el PID microsegundos después de
# crear el fichero: vacío durante más de esto, bajo nuestro flock, es huérfano.
_EMPTY_LOCK_GRACE_SECONDS = 2.0
_kernel_identity_cache: list[str | None] = []
_flock_fallback_warned: list[bool] = []

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows nativo no está soportado
    fcntl = None


def _kernel_identity() -> str | None:
    """Identificador del arranque del núcleo, o None si no se puede leer."""
    if _kernel_identity_cache:
        return _kernel_identity_cache[0]
    identity = None
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id")
        if boot_id.exists():
            with open(boot_id, "r", encoding="utf-8") as handle:
                identity = handle.read().strip() or None
        elif sys.platform == "darwin":
            import subprocess

            out = subprocess.run(
                ["sysctl", "-n", "kern.bootsessionuuid"], capture_output=True, text=True, timeout=2,
                stdin=subprocess.DEVNULL,  # el stdin del servidor MCP es su canal JSON-RPC
            )
            identity = out.stdout.strip() if out.returncode == 0 else None
    except Exception as exc:  # noqa: BLE001 — sin identidad se usa la regla antigua
        log_err(f"No se pudo leer la identidad del núcleo ({exc}); lock sin ruptura instantánea")
    if identity is not None and not re.fullmatch(r"[A-Za-z0-9-]{8,64}", identity):
        identity = None
    _kernel_identity_cache.append(identity)
    return identity


def _warn_flock_fallback(reason: object) -> None:
    if not _flock_fallback_warned:
        _flock_fallback_warned.append(True)
        log_err(f"flock no disponible sobre el directorio del estado ({reason}); lock en modo anterior")


def _release_directory_lock(fd: int, owner_pid: int) -> None:
    try:
        if os.getpid() == owner_pid:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _try_flock_directory(directory: Path) -> tuple[str, int | None]:
    """`("held", fd)`, `("busy", None)` o `("unsupported", None)`."""
    if fcntl is None:
        return ("unsupported", None)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(directory, flags)
    except OSError as exc:
        _warn_flock_fallback(exc)
        return ("unsupported", None)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return ("busy", None)
    except OSError as exc:
        os.close(fd)
        _warn_flock_fallback(exc)
        return ("unsupported", None)
    owner_pid = os.getpid()
    try:
        held, current = os.fstat(fd), os.stat(directory)
        same_directory = (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)
    except OSError:
        same_directory = False
    if not same_directory:
        # `_project` se recreó entre el open y el flock: ese candado ya no protege la ruta.
        _release_directory_lock(fd, owner_pid)
        return ("busy", None)
    try:
        probe = os.open(directory, flags)
    except OSError as exc:
        _release_directory_lock(fd, owner_pid)
        _warn_flock_fallback(exc)
        return ("unsupported", None)
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        excludes = True
    except OSError:
        excludes = False
    else:
        excludes = False
        with contextlib.suppress(OSError):
            fcntl.flock(probe, fcntl.LOCK_UN)
    finally:
        os.close(probe)
    if not excludes:
        _release_directory_lock(fd, owner_pid)
        _warn_flock_fallback("un segundo descriptor también obtiene el flock")
        return ("unsupported", None)
    return ("held", fd)


def _lock_mark(directory_fd: int) -> str | None:
    """Marca `<boot id>:<dev>:<ino>` del flock que sostenemos, o None.

    El núcleo solo no basta: WSL2 o Docker Desktop comparten núcleo pero montan
    el mismo directorio con otro dispositivo, y el flock no excluye entre
    montajes (revisión de la implementación 2026-09-15)."""
    identity = _kernel_identity()
    if identity is None:
        return None
    held = os.fstat(directory_fd)
    return f"{identity}:{held.st_dev}:{held.st_ino}"


def _read_lock_record(lock_path: Path) -> tuple[int | None, str | None]:
    """(PID, marca `flock`) del lockfile."""
    try:
        with open(lock_path, "r", encoding="utf-8", errors="ignore") as f:
            fields = f.read().split()
    except OSError:
        return (None, None)
    mark = next((field.split(":", 1)[1] for field in fields[2:] if field.startswith(f"{_LOCK_MARK}:")), None)
    return (_read_lock_pid(lock_path), mark or None)


def _create_lockfile(lock_path: Path, mark: str | None) -> tuple[int, int]:
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        stamp = f"{os.getpid()} {datetime.datetime.now(datetime.timezone.utc).isoformat()}"
        if mark:
            stamp += f" {_LOCK_MARK}:{mark}"
        os.write(fd, (stamp + "\n").encode("utf-8"))
        created = os.fstat(fd)
    finally:
        os.close(fd)
    return (created.st_dev, created.st_ino)


def _break_if_orphan(lock_path: Path, stale: float, *, holds_directory: bool, mark: str | None) -> tuple[bool, str]:
    """Borra el lockfile si se demuestra huérfano.

    Devuelve (ya no bloquea, motivo). No escribe en stderr: quien llama avisa
    tras soltar el flock, para que un stderr roto no lo deje retenido."""
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return (True, "")  # el titular lo liberó entre el open y el stat
    owner_pid, found_mark = _read_lock_record(lock_path)
    reason = None
    if holds_directory and mark and found_mark == mark:
        reason = f"Lock huérfano (pid={owner_pid}) en {lock_path}: su titular sostenía el flock y ya no; se rompe"
    elif holds_directory and owner_pid is None and age > _EMPTY_LOCK_GRACE_SECONDS:
        reason = f"Lock vacío o ilegible ({age:.0f}s) en {lock_path}; se rompe"
    elif age > stale and not (owner_pid is not None and _is_process_alive(owner_pid)):
        reason = f"Lock viejo ({age:.0f}s, pid={owner_pid}) en {lock_path}; se rompe (proceso muerto confirmado o no identificable)"
    if reason is None:
        return (False, "")
    with contextlib.suppress(OSError):
        lock_path.unlink()
    return (True, reason)


@contextlib.contextmanager
def state_write_lock(timeout: float | None = None, stale: float | None = None):
    """Lock advisory contra doble escritor (servidor MCP + CLI en paralelo).

    FW-233: candado híbrido. `flock` sobre el directorio del estado, sostenido
    toda la sección crítica, serializa la decisión de crear o romper el
    lockfile `.state.lock` (`O_EXCL`), que sigue siendo la señal para lectores y
    para versiones anteriores del motor. Antes, dos procesos que encontraban el
    mismo lock viejo lo rompían a la vez y se perdía una actualización.

    - Lockfile con marca de nuestro núcleo y el flock en nuestra mano: su
      titular murió (el SO suelta el flock al morir) y se rompe al instante.
    - Sin marca, u otro núcleo: regla anterior (más viejo que `stale` y PID
      muerto).
    - Si `flock` no está disponible o no excluye (NFS, permisos): modo anterior.

    El path se deriva de STATE_FILE_PATH en tiempo de llamada (parcheable en
    tests). Límite declarado: un proceso de una versión anterior que rompe un
    huérfano de más de `stale` a la vez que uno nuevo aún puede dejar dos
    titulares; tras actualizar, reiniciar la sesión (servidor MCP)."""
    # Defaults resueltos en tiempo de llamada (no de definición) para que los
    # tests puedan parchear LOCK_TIMEOUT_SECONDS y no pagar los 10s reales
    # ejercitando la MISMA ruta de código (triaje 2026-07-09 C4).
    if timeout is None:
        timeout = LOCK_TIMEOUT_SECONDS
    if stale is None:
        stale = LOCK_STALE_SECONDS
    lock_path = STATE_FILE_PATH.parent / ".state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    owner_pid = os.getpid()
    directory_fd: int | None = None
    while True:
        status, fd = _try_flock_directory(lock_path.parent)
        if status == "busy":
            if time.monotonic() >= deadline:
                _raise_lock_timeout(lock_path, timeout)
            time.sleep(LOCK_RETRY_INTERVAL)
            continue
        handed_over = False
        broken, reason = False, ""
        try:
            # Todo lo que ocurre con el flock en la mano va dentro: una excepción
            # aquí (stderr roto, disco) no puede dejarlo retenido.
            mark = _lock_mark(fd) if fd is not None else None
            try:
                created = _create_lockfile(lock_path, mark)
                directory_fd, handed_over = fd, True
            except FileExistsError:
                broken, reason = _break_if_orphan(lock_path, stale, holds_directory=fd is not None, mark=mark)
        finally:
            if fd is not None and not handed_over:
                _release_directory_lock(fd, owner_pid)
        if handed_over:
            break
        if reason:
            log_err(reason)
        if broken:
            continue
        if time.monotonic() >= deadline:
            _raise_lock_timeout(lock_path, timeout)
        time.sleep(LOCK_RETRY_INTERVAL)
    try:
        yield
    finally:
        try:
            # Un hijo de `fork` que salga por aquí no debe soltar ni borrar lo del padre.
            if os.getpid() == owner_pid:
                current = os.stat(lock_path)
                if (current.st_dev, current.st_ino) == created and _read_lock_pid(lock_path) == owner_pid:
                    lock_path.unlink()
        except OSError:
            pass
        finally:
            if directory_fd is not None:
                _release_directory_lock(directory_fd, owner_pid)


def _raise_lock_timeout(lock_path: Path, timeout: float) -> None:
    current_owner = _read_lock_pid(lock_path)
    if current_owner is not None:
        owner_info = f" (proceso {current_owner})"
    elif not lock_path.exists():
        owner_info = " (titular sin lockfile visible)"
    else:
        owner_info = ""
    raise RuntimeError(
        f"No se pudo adquirir {lock_path} en {timeout:.1f}s: hay otra mutación en curso{owner_info}. "
        "Reintentar; si persiste, borrar el lockfile manualmente."
    )



def build_session_context(state: dict) -> dict:
    """Contexto mínimo de sesión — función pura COMPARTIDA por el MCP
    (get_session_brief) y las proyecciones, para que el brief cumpla su propia
    descripción (módulo activo, bugs, blockers) sin una segunda lectura
    (triaje 2026-07-09, P2 'query-first en una sola llamada')."""
    in_progress = state.get("modules", {}).get("in_progress", [])
    active_mod = in_progress[0] if in_progress else "ninguno"
    open_bugs = [
        {"id": b.get("id"), "severity": b.get("severity"), "module_id": b.get("module_id")}
        for b in state.get("_bugs", []) if b.get("status") == "Abierto"
    ]
    blockers = [
        {"id": c.get("id"), "blocked_scope": c.get("blocked_scope")}
        for c in state.get("_clarifications", [])
        if c.get("status") == "Abierta" and (
            c.get("blocks_start") or c.get("blocks_implementation") or c.get("blocks_closure")
        )
    ]
    sess = state.get("session", {})
    migrations = state.get("framework_migrations", {})
    applied_migrations = [
        {"id": migration_id, "type": entry.get("type"), "status": entry.get("status")}
        for migration_id, entry in migrations.items()
        if isinstance(entry, dict)
    ] if isinstance(migrations, dict) else []
    return {
        "project": state.get("project", "[NOMBRE_PROYECTO]"),
        "framework_version": state.get("framework_version", FRAMEWORK_VERSION),
        "current_phase": state.get("current_phase", "phase_01"),
        "active_module": active_mod,
        "active_module_tier": state.get("work_tiers", {}).get(active_mod, {}).get("tier", "no clasificado"),
        "last_completed_step": state.get("last_completed_step", ""),
        "next_action": sess.get("next_action", ""),
        "next_command": sess.get("next_command", ""),
        "framework_migrations": applied_migrations,
        "open_bugs": open_bugs,
        "open_blockers": blockers,
    }


def _unwrap_transport_list(value):
    """Desenvuelve la corrupción del transport MCP de OpenCode (FW-103, Bot
    financiero): su wrapper del tool convierte un array simple de strings al
    pasarlo por XML->JSON en `{"item": [...]}`, o en el array anidado
    `[["item", ...resto]]`. El motor entonces validaba `adr_refs` como
    `list[str]` y el string literal `"item"` colaba como si fuera un ID de
    ADR, produciendo el error críptico
    `APPROVE_RECEIPT referencia ADRs inexistentes: ['item']`.

    Reconoce ESTRECHAMENTE esos dos patrones exactos y devuelve la lista
    real. Cualquier otra forma (incluida una malformación genuina del
    payload) se devuelve SIN TOCAR — identidad del objeto preservada — para
    que la validación de tipo existente falle exactamente como hoy. No
    ensancha la validación: solo repara la corrupción específica de ese
    transport."""
    if (
        isinstance(value, dict)
        and set(value.keys()) == {"item"}
        and isinstance(value["item"], list)
    ):
        return value["item"]
    if (
        isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], list)
        and len(value[0]) >= 1
        and value[0][0] == "item"
    ):
        return value[0][1:]
    return value


# Campos `list[str]` del payload, por acción, expuestos a la corrupción del
# transport OpenCode (FW-103). Whitelist deliberadamente estrecha: solo las
# acciones que hoy reciben un array simple de strings directamente del
# caller MCP sin pasar antes por un chequeo de tipo propio.
_TRANSPORT_LIST_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "record_approval_receipt": ("adr_refs", "covers_dependencies", "covers_changes"),
    "refresh_approval_receipt": ("derived_from",),
    # TL FW-019 / BOTÓN FW-115/FW-113: module_id ahora puede ser lista (ADR
    # paraguas multi-módulo), expuesta a la misma corrupción de transport.
    "register_decision": ("module_id",),
}

_SEED_STATE_REL = "_project/03_project_state.json"


def state_hash_at_commit(repo, commit: str) -> str:
    """sha256 del estado canónico (JSON ordenado) que `commit` tiene en `repo`.

    L14 / D9c: la semilla por referencia se ancla a este hash. ValueError si el
    clon no alcanza el commit o ahí no hay estado legible.
    """
    import subprocess
    repo = Path(repo)
    res = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{_SEED_STATE_REL}"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise ValueError(
            f"seed_snapshot: el clon no alcanza el commit {commit} en {repo.name} "
            f"(¿clon superficial o commit ajeno?); no se puede cargar la base desde git"
        )
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise ValueError(f"seed_snapshot: el estado en {commit} no es JSON legible: {e}") from e
    canon = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _state_at_commit(repo, commit: str) -> dict:
    import subprocess
    res = subprocess.run(["git", "-C", str(Path(repo)), "show", f"{commit}:{_SEED_STATE_REL}"],
                         capture_output=True, text=True)
    return json.loads(res.stdout)


def seed_reference_payload(repo, commit: str, reason: str) -> dict:
    """Payload de `seed_snapshot` por referencia: commit + hash, sin entidades."""
    return {"source_commit": commit, "reason": reason, "state_hash": state_hash_at_commit(repo, commit)}


_SEEDABLE = (
    "_bugs", "_clarifications", "_decisions", "modules", "module_statuses",
    "work_tiers", "module_specs", "dependencies", "architecture_constraints", "stack",
)


# Contratos canónicos de payloads por acción (Plan 4.5 Lote 2).
# _REQUIRED_PAYLOAD_KEYS: campos obligatorios para cada acción built-in.
# _OPTIONAL_PAYLOAD_KEYS: campos opcionales admitidos para cada acción.
# _STRICT_PAYLOAD_KEYS: unión canónica (whitelist) que protege contra typos silenciosos
# derivada en tiempo de ejecución para evitar catálogos paralelos (Astra Lote 2).
_REQUIRED_PAYLOAD_KEYS: dict[str, set[str]] = {
    "acknowledge_state_delta": {"reason", "approver", "paths"},
    "seed_snapshot": {"source_commit", "reason"},
    "add_active_mcp": {"name"},
    "add_closure_rejection": {"module_id", "reason"},
    "add_module": {"id"},
    "add_work_tier": {"id", "tier", "rationale"},
    "amend_bug": {"id", "reason"},
    "append_to_list_field": {"field", "value"},
    "backfill_closure_rejection_provenance": {"module_id", "resolved_ids", "resolution_event_ts"},
    "ciclo_finished": {"module", "result"},
    "ciclo_paused": set(),
    "ciclo_started": {"module"},
    "classify_module_spec": {"id", "data_model", "api_contract", "classified_by"},
    "close_change": {"id", "evidence", "tests", "changelog"},
    "complete_module": {"id"},
    "confirm_builder_use": {"module", "evidence", "builder_confirmed"},
    "defer_bug_to_tech_debt": {"id"},
    "drop_module": {"id", "reason"},
    "abandon_module": {"id", "reason", "evidence"},
    "reassign_bug_module": {"id", "module_id", "reason"},
    "escalate_change": {"id", "module_id", "reason"},
    "open_change": {"summary", "affected_modules", "tier", "criteria"},
    "record_approval_receipt": {"module", "approved_by", "evidence", "risk_summary"},
    "record_checkpoint": {"kind", "scope_id", "evidence", "reference"},
    "record_data_safety_check": {"module_id", "target_env", "backup_verified", "evidence"},
    "record_execution_receipt": {"module", "kind", "evidence"},
    "record_harvest": {"module_id", "classes"},
    "record_review_receipt": {"module", "reviewer_id", "result", "evidence"},
    "refresh_approval_receipt": {"module", "parent_fingerprint", "evidence", "derived_from"},
    "register_bug": {"module_id", "severity", "description"},
    "register_clarification": {"blocked_scope", "question"},
    "register_decision": {"title", "context", "decision"},
    "reopen_module": {"id", "reason"},
    "resolve_bug": {"id", "fix"},
    "resolve_clarification": {"id", "answer"},
    "resolve_closure_rejections": {"module_id", "resolution", "evidence"},
    "restore_closure_rejections": {"module_id", "archive_ids", "reason"},
    "set_active_module": {"id"},
    "set_execution_profile": {"capability_tier"},
    "set_focus": {"area"},
    "set_framework_version": {"version"},
    "set_lifecycle": {"stage", "reason", "builder_confirmed"},
    "set_phase": {"current_phase"},
    "set_session_pointer": {"next_action", "next_command"},
    "start_independent_review": {"module", "reviewer_id", "implementer_id", "mode"},
    "supersede_decision": {"id", "superseded_by", "reason"},
    "update_last_step": {"last_completed_step"},
    "update_module_status": {"id", "status"},
    # Acciones de migración interna (compatibilidad de replay):
    "retire_codex_budget": {
        "migration_id", "expected_fingerprint", "expected_pointer",
        "archive_path", "archive_sha256", "next_action", "next_command",
    },
    "restore_codex_budget": {
        "migration_id", "expected_fingerprint", "archive_path", "archive_sha256",
        "pointer", "session_before",
    },
    "grandfather_checkpoint_gate": {"modules", "from_version", "reason"},
    "invalidate_stale_review": {"module", "approved_by", "reason"},
    # Acción interna de importación Markdown (state_manager import):
    "import_markdown_entities": set(),
}

_OPTIONAL_PAYLOAD_KEYS: dict[str, set[str]] = {
    # L14 (D9c): por referencia lleva `state_hash`; en línea lleva `entities`.
    "seed_snapshot": {"entities", "state_hash"},
    "add_active_mcp": {"config"},
    "add_closure_rejection": set(),
    "add_module": {"stabilization"},
    "add_work_tier": {
        "classified_by", "requires_second_review", "handles_real_data",
    },
    "amend_bug": {"description", "fix", "affected_files"},
    "append_to_list_field": {"unique"},
    "backfill_closure_rejection_provenance": {"legacy_refs", "provenance_ids"},
    "ciclo_finished": {"mode"},
    "ciclo_paused": {"reason"},
    "ciclo_started": {"mode"},
    "classify_module_spec": {"justification", "integration_real"},
    "close_change": set(),
    "complete_module": set(),
    "defer_bug_to_tech_debt": set(),
    "drop_module": set(),
    "abandon_module": set(),
    "reassign_bug_module": {"evidence"},
    "escalate_change": set(),
    "open_change": {"id"},
    "record_approval_receipt": {"adr_refs", "covers_dependencies", "covers_changes"},
    "record_checkpoint": {"module", "recorded_by"},
    "record_data_safety_check": {"recorded_by"},
    "record_execution_receipt": {"command", "exit_code", "test_changes", "base_ref", "tests_run"},
    "record_harvest": set(),
    "record_review_receipt": set(),
    "refresh_approval_receipt": {"additive_rationale"},
    "register_bug": {"id", "affects_ac"},
    "register_clarification": {
        "id", "title", "blocks_start", "blocks_implementation",
        "blocks_closure", "blocking_condition", "options", "impact",
    },
    "register_decision": {"id", "module_id", "alternatives", "consequences", "expertise", "documents_decision"},
    "reopen_module": set(),
    "resolve_bug": {"affected_files"},
    "resolve_clarification": set(),
    "resolve_closure_rejections": {"resolved_ids"},
    "restore_closure_rejections": {"restored_ids"},
    "set_active_module": set(),
    "set_execution_profile": {"model", "rationale"},
    "set_focus": set(),
    "set_framework_version": set(),
    "set_lifecycle": {"escalation_patterns"},
    "confirm_builder_use": set(),
    "set_phase": {"current_step"},
    "set_session_pointer": {
        "active_module_description", "next_module", "next_module_description",
    },
    "start_independent_review": set(),
    "supersede_decision": set(),
    "update_last_step": set(),
    "update_module_status": set(),
    "retire_codex_budget": set(),
    "restore_codex_budget": set(),
    "grandfather_checkpoint_gate": set(),
    "import_markdown_entities": {"bugs", "clarifications", "decisions", "imported_ids"},
}

_STRICT_PAYLOAD_KEYS: dict[str, set[str]] = {
    action: set(_REQUIRED_PAYLOAD_KEYS.get(action, set())) | set(_OPTIONAL_PAYLOAD_KEYS.get(action, set()))
    for action in set(_REQUIRED_PAYLOAD_KEYS) | set(_OPTIONAL_PAYLOAD_KEYS)
}


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except (ValueError, AttributeError):
        raise ValueError(f"versión de framework inválida: {version!r}") from None


def _state_fingerprint(data: dict) -> str:
    """Fingerprint CAS del estado previo a una migracion.

    No es un receipt de revision: solo evita aplicar una compensacion sobre
    trabajo que cambio desde la inspeccion.
    """
    raw = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _verify_migration_archive(manager: "StateManager", relative_path: object, expected_sha256: object) -> None:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("migracion requiere archive_path")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("migracion requiere archive_sha256 hexadecimal")
    candidate = Path(relative_path)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or "\\" in relative_path
        or not relative_path.startswith("_project/migrations/codex-budget-retirement/")
    ):
        raise ValueError("archive_path fuera del area de migracion permitida")
    root = manager._workspace_root().resolve()
    archive = (root / candidate).resolve()
    try:
        archive.relative_to(root)
    except ValueError as exc:
        raise ValueError("archive_path fuera del workspace") from exc
    if not archive.is_file():
        raise ValueError("archive_path no existe")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError("archive_sha256 no coincide con el archivo")


def closure_rejection_is_active(
    rejection: object,
    *,
    resolution_backed: bool = True,
) -> bool:
    """Predicado único para decidir si un rechazo sigue bloqueando.

    Un rechazo restaurado vuelve a estar activo aunque conserve metadatos de
    una resolución anterior. Uno sin resolución también está activo. Un
    rechazo resuelto deja de bloquear solo cuando la evidencia de resolución
    está respaldada; los consumidores que aún no pueden consultar la bitácora
    deben pasar ``resolution_backed=False`` para fallar cerrado.
    """
    if not isinstance(rejection, dict):
        return False
    if rejection.get("restored_at") or not rejection.get("resolved_at"):
        return True
    return not resolution_backed


def validate_runtime_state(data: object, *, root: Path | None = None) -> None:
    """Valida el estado completo en la frontera de lectura.

    La carga del archivo mantiene compatibilidad con herramientas de replay y
    migración que preparan su modo antes de validar. Las lecturas normales,
    en cambio, no pueden servir una forma parcial o legacy como si fuese
    canónica. Se usa un manager efímero para reutilizar exactamente el
    validador de escritura sin mutar el objeto consultado.
    """
    if not isinstance(data, dict):
        raise ValueError("El estado debe ser un objeto JSON")
    manager = StateManager.__new__(StateManager)
    manager._is_replay = False
    manager._migration_mode = False
    manager.validate_state(data, root=root)

def _reject_unknown_payload_keys(action: str, payload: dict) -> None:
    allowed = _STRICT_PAYLOAD_KEYS.get(action)
    if allowed is None:
        return
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(
            f"{action}: clave(s) de payload no reconocidas {unknown} — "
            f"claves válidas: {sorted(allowed)}"
        )


class StateManager:
    def __init__(self) -> None:
        self.state_data: dict = {}
        self._state_sig: tuple[int, int] | None = None
        self.load_state()

    @staticmethod
    def _next_closure_rejection_id(state: dict) -> str:
        used = {
            entry.get("id")
            for bucket in ("closure_rejections", "closure_rejection_archive")
            for entry in state.get(bucket, [])
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        }
        number = 1
        while f"CRR-{number:04d}" in used:
            number += 1
        return f"CRR-{number:04d}"

    def _archive_module_closure_rejections(
        self, mod_id: str, now_iso: str, *, abandonment: tuple[str, str] | None = None
    ) -> list[str]:
        """Archiva los rechazos de cierre de `mod_id` en closure_rejection_archive.

        Sin `abandonment` (cierre, FW-175) solo se archivan los resueltos
        explícitamente; los activos permanecen y siguen contando para la
        Regla 7. Con `abandonment=(resolution, evidence)` (abandon_module,
        PA-FW-037) el módulo ya no volverá a ready_for_validation, donde
        resolve_closure_rejections podría cerrarlos: los activos se resuelven
        con ese motivo y se archivan también. Devuelve los ids archivados."""
        archive = self.state_data.setdefault("closure_rejection_archive", [])
        archived_ids = {
            entry.get("id")
            for entry in archive
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        }
        remaining_rejections = []
        moved: list[str] = []
        for rejection in self.state_data.get("closure_rejections", []):
            if rejection.get("module_id") != mod_id:
                remaining_rejections.append(rejection)
                continue
            active = not rejection.get("resolved_at") or bool(rejection.get("restored_at"))
            if active and abandonment is None:
                remaining_rejections.append(rejection)
                continue
            archived = copy.deepcopy(rejection)
            archived.setdefault("id", self._next_closure_rejection_id(self.state_data))
            if active:
                resolution, evidence = abandonment
                archived["resolved_at"] = now_iso
                archived["resolution"] = resolution
                archived["evidence"] = evidence
                archived["resolved_by"] = "abandon_module"
                # El evento de una resolución anterior (deshecha por un
                # restore) ya no corresponde a este resolved_at.
                archived.pop("resolution_event", None)
            archived["archived_at"] = now_iso
            archived.pop("restored_at", None)
            archived.pop("restore_reason", None)
            if archived["id"] not in archived_ids:
                archive.append(archived)
                archived_ids.add(archived["id"])
            moved.append(archived["id"])
        self.state_data["closure_rejections"] = remaining_rejections
        return moved

    @staticmethod
    def _is_dropped_module(state: dict, module_id: object) -> bool:
        """True si `module_id` fue abandonado (estado terminal `dropped`, PA-FW-037)."""
        if not isinstance(module_id, str):
            return False
        module_id = module_id.strip()
        return (
            state.get("module_statuses", {}).get(module_id) == "dropped"
            or module_id in state.get("modules", {}).get("dropped", [])
        )

    @staticmethod
    def _is_known_module(state: dict, module_id: str) -> bool:
        modules = state.get("modules", {})
        declared = {
            module
            for bucket in ("completed", "in_progress", "pending")
            for module in modules.get(bucket, [])
        }
        return module_id in declared or module_id in state.get("module_statuses", {})

    @staticmethod
    def _is_active_closure_rejection(rejection: dict) -> bool:
        return closure_rejection_is_active(rejection)

    @staticmethod
    def _validate_audit_text(field: str, value: str) -> str:
        """Rechaza secretos y texto desmedido antes de que entren al audit trail.

        Falla ruidosamente (ValueError) en vez de redactar en silencio: el
        builder debe saber que pegó una credencial, no descubrir un `[REDACTED]`
        meses después."""
        value = value.strip()
        if len(value) > MAX_AUDIT_TEXT_LENGTH:
            raise ValueError(
                f"{field} supera el límite de {MAX_AUDIT_TEXT_LENGTH} caracteres"
            )
        if AUDIT_SECRET_PATTERN.search(value):
            raise ValueError(
                f"{field} no debe contener credenciales ni secretos en texto libre"
            )
        if any(pattern.search(value) for pattern in AUDIT_SENSITIVE_PATTERNS + _extra_audit_patterns()):
            raise ValueError(
                f"{field} no debe contener tokens ni identificadores sensibles en texto libre"
            )
        return value

    @staticmethod
    def _file_signature() -> tuple[int, int] | None:
        """(mtime_ns, size) del estado en disco, o None si no existe. Firma
        barata para detectar que otro proceso reescribió el archivo."""
        try:
            st = STATE_FILE_PATH.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _workspace_root(self) -> Path:
        """Root de trabajo de esta instancia; replay puede operar sobre otro."""
        return getattr(self, "_replay_workspace_root", WORKSPACE_ROOT)

    @staticmethod
    def _mutation_manifest_path() -> Path:
        return STATE_FILE_PATH.parent / ".state_transaction.json"

    @classmethod
    def _write_mutation_manifest(cls, snapshot: dict[Path, bytes | None]) -> None:
        """Persiste una recuperación durable antes de publicar una mutación."""
        manifest_path = cls._mutation_manifest_path()
        payload = {
            "version": 1,
            "files": {
                str(path): (
                    None
                    if content is None
                    else base64.b64encode(content).decode("ascii")
                )
                for path, content in snapshot.items()
            },
        }
        tmp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, manifest_path)

    @classmethod
    def _clear_mutation_manifest(cls) -> None:
        try:
            cls._mutation_manifest_path().unlink()
        except FileNotFoundError:
            pass

    @classmethod
    def ensure_transaction_clean(cls, *, lock_held: bool = False) -> bool:
        """No sirvas una vista mientras una mutación multiarchivo está abierta.

        No existe un rename atómico de varios nombres independientes. El
        manifiesto convierte esa limitación física en una transacción
        crash-consistent: un lector canónico espera al escritor activo y, si
        el proceso murió con el manifiesto presente, restaura el snapshot
        completo antes de observar estado o proyecciones.
        """
        manifest_path = cls._mutation_manifest_path()
        if not manifest_path.exists():
            return False
        lock_path = STATE_FILE_PATH.parent / ".state.lock"
        if lock_path.exists() and not lock_held:
            # The writer owns the manifest while it publishes. Waiting for its
            # lock prevents a reader from restoring the pre-mutation snapshot
            # underneath a live writer.
            with state_write_lock():
                pass
        if manifest_path.exists():
            cls._recover_pending_mutation()
        return True

    @classmethod
    def _recover_pending_mutation(cls) -> None:
        """Recupera automáticamente una publicación cuyo rollback quedó abierto."""
        manifest_path = cls._mutation_manifest_path()
        if not manifest_path.exists():
            return
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            if raw.get("version") != 1 or not isinstance(raw.get("files"), dict):
                raise ValueError("manifiesto de transacción inválido")
            allowed_paths = set(cls._mutation_paths())
            manifest_paths = {Path(path) for path in raw["files"]}
            if not manifest_paths.issubset(allowed_paths):
                raise ValueError("manifiesto de transacción referencia rutas fuera de la allowlist")
            snapshot = {
                Path(path): (
                    None
                    if content is None
                    else base64.b64decode(content.encode("ascii"), validate=True)
                )
                for path, content in raw["files"].items()
            }
            cls._restore_mutation_files(snapshot)
            cls._clear_mutation_manifest()
        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(
                f"transacción pendiente no recuperable en {manifest_path}; "
                f"detener y recuperar desde el snapshot/log ({exc})"
            ) from exc

    def load_state(self, *, lock_held: bool = False) -> None:
        """Carga el estado canónico.

        Archivo ausente → estado vacío (proyecto sin inicializar). Archivo
        presente pero ilegible/corrupto → fallo RUIDOSO: degradar en silencio
        a `{}` haría que un render posterior proyectara vacío sobre las vistas.
        Recuperación: restaurar desde git o reconstruir con la bitácora
        `_project/state_log.jsonl`."""
        self.ensure_transaction_clean(lock_held=lock_held)
        if not STATE_FILE_PATH.exists():
            self.state_data = {}
            self._state_sig = None
            return
        try:
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
                raw_state = f.read()
            # El lector canónico usa ``open`` por compatibilidad histórica;
            # registrarlo aquí permite que el modo diagnóstico mida el estado
            # real sin reemplazar globalmente builtins.open.
            try:
                import context_measurement
                context_measurement.record_read(
                    STATE_FILE_PATH,
                    operation="open_read",
                    content_bytes=len(raw_state.encode("utf-8")),
                    consumer="state_core:load_state",
                    category="canonical_state",
                )
            except ImportError:
                pass
            self.state_data = json.loads(raw_state)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            log_err(f"Estado corrupto o ilegible en {STATE_FILE_PATH}: {e}")
            raise RuntimeError(
                f"03_project_state.json corrupto o ilegible ({e}). "
                "NO continuar: restaurar desde git o reconstruir desde _project/state_log.jsonl."
            ) from e
        self._normalize_legacy_review_shape()
        self._state_sig = self._file_signature()

    def _normalize_legacy_review_shape(self) -> None:
        """FW-165: antes de 4.24.0, `workflow_receipts.reviews[module]` era un
        único dict (se sobreescribía cada ronda). Un proyecto que actualiza el
        framework con un estado escrito por una versión anterior traería esa
        forma antigua y rompería la validación de esquema en memoria en el
        primer `mutate_state` — la misma clase de fallo que FW-162 (el motor
        gana un requisito que el upgrade no satisface). Envolver en silencio
        `dict` -> `[dict]` es idempotente y no requiere script de migración
        aparte."""
        reviews = (
            self.state_data.get("workflow_receipts", {}).get("reviews", {})
            if isinstance(self.state_data.get("workflow_receipts"), dict)
            else {}
        )
        for module, entry in list(reviews.items()):
            if isinstance(entry, dict):
                reviews[module] = [entry]

    def reload_if_changed(self, *, lock_held: bool = False) -> bool:
        """Recarga el estado si el archivo en disco cambió desde la última carga
        (FW-041). En flujo multi-agente otro proceso puede mutar el estado vía su
        propio servidor/CLI; las lecturas del MCP (get_full_state / get_session_brief)
        servían la copia en memoria obsoleta. `mutate` ya recarga bajo lock; esto
        cierra el mismo hueco para las lecturas. Devuelve True si recargó.

        El lock se sostiene a través de la comprobación Y la lectura: mirar el
        manifiesto y luego leer sin lock deja una ventana TOCTOU en la que un
        escritor publica una transacción multiarchivo a medio observar."""
        if not lock_held:
            with state_write_lock():
                return self.reload_if_changed(lock_held=True)
        recovered = self.ensure_transaction_clean(lock_held=lock_held)
        if recovered:
            self.load_state(lock_held=lock_held)
            return True
        if not STATE_FILE_PATH.exists():
            return False
        if self._file_signature() == self._state_sig:
            return False
        self.load_state(lock_held=lock_held)
        return True

    def _mutation_paths() -> tuple[Path, ...]:
        """Archivos publicados por una mutación y restaurables como unidad."""
        candidates = (
            STATE_FILE_PATH,
            STATE_FILE_PATH.parent / "state_log.jsonl",
            CORE_FILE_PATH,
            BUGS_FILE_PATH,
            CLARIFICATIONS_FILE_PATH,
            DECISIONS_FILE_PATH,
            SESSION_BRIEF_PATH,
            # FW-225: proyección del registro de cambios en mantenimiento.
            STATE_FILE_PATH.parent / "24_change_log.md",
        )
        return tuple(dict.fromkeys(candidates))

    @classmethod
    def _capture_mutation_files(cls) -> dict[Path, bytes | None]:
        return {
            path: path.read_bytes() if path.exists() else None
            for path in cls._mutation_paths()
        }

    @staticmethod
    def _restore_mutation_files(snapshot: dict[Path, bytes | None]) -> None:
        """Restaura los archivos publicados si una mutación no se puede sellar."""
        errors: list[str] = []
        staged: dict[Path, Path] = {}

        # Prepare every original before publishing any rollback replacement.
        # This removes the common partial-rollback case (a write/fsync failure
        # while preparing a later file); publication starts only after all
        # bytes have been verified in same-directory temporaries.
        for path, content in snapshot.items():
            if content is None:
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = path.with_suffix(path.suffix + ".rollback.tmp")
                with open(tmp_path, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if tmp_path.read_bytes() != content:
                    raise OSError("snapshot temporal no coincide con el original")
                staged[path] = tmp_path
            except OSError as exc:
                errors.append(f"{path}: {exc}")

        if not errors:
            # Publish the prepared originals. If an OS-level rename fails, keep
            # trying the remaining paths, then verify and fail closed below;
            # the caller never continues after an incomplete rollback.
            for path, tmp_path in staged.items():
                try:
                    os.replace(tmp_path, path)
                except OSError as exc:
                    errors.append(f"{path}: {exc}")
            for path, content in snapshot.items():
                if content is not None:
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    errors.append(f"{path}: {exc}")

        for tmp_path in staged.values():
            with contextlib.suppress(OSError):
                tmp_path.unlink()

        # A rollback is itself a gate: never report recovery as complete when
        # one published file still differs from the pre-mutation snapshot.
        for path, expected in snapshot.items():
            try:
                actual = path.read_bytes() if path.exists() else None
            except OSError as exc:
                errors.append(f"{path}: no se pudo verificar ({exc})")
                continue
            if actual != expected:
                errors.append(f"{path}: contenido posterior al rollback no coincide")
        if errors:
            raise RuntimeError(
                "rollback transaccional incompleto; detener y recuperar desde el estado/log: "
                + "; ".join(errors)
            )

    def save_state(self) -> None:
        """Escritura atómica: volcar a un temporal en el mismo directorio y
        os.replace() sobre el destino, para que un crash a mitad de escritura
        nunca deje la fuente única de verdad truncada."""
        try:
            STATE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = STATE_FILE_PATH.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.state_data, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, STATE_FILE_PATH)
            self._state_sig = self._file_signature()
        except Exception as e:
            log_err(f"Error al guardar estado: {e}")
            raise e

    def append_event(self, action: str, payload: dict, message: str, ts: str) -> None:
        """Bitácora append-only de mutaciones: una línea JSON por evento.

        Se crea perezosamente junto al estado en la primera mutación (el path
        se deriva de STATE_FILE_PATH en tiempo de llamada para respetar el
        parcheo de rutas en tests). Un fallo de escritura se propaga y revierte
        la mutación completa junto con sus proyecciones. En framework_mode
        template no debe existir (framework_doctor lo exige).

        Cada evento sella el hash del anterior (`prev`) y el suyo (`hash`), de
        modo que editar, borrar o insertar un evento invalida todos los
        posteriores. El sabotaje controlado de 2026-07-24 dejó ver por qué hace
        falta: con la auditoría de respaldo, fabricar evidencia en el estado ya
        no cuela, pero seguía siendo posible escribir en la bitácora — es decir,
        en la fuente contra la que se contrasta. Un fallo de escritura propaga
        error para que ``_mutate_locked`` restaure el snapshot completo.
        """
        log_path = STATE_FILE_PATH.parent / "state_log.jsonl"
        try:
            entry = {"ts": ts, "action": action, "payload": payload, "result": message}
            entry["prev"] = _last_event_hash(log_path)
            entry["hash"] = _event_hash(entry)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as e:
            # La bitácora forma parte de la transacción: ocultar este fallo
            # dejaría un estado nuevo sin el evento que lo respalda y rompería
            # la cadena de auditoría. El llamador restaura el snapshot completo.
            log_err(f"No se pudo escribir state_log.jsonl: {e}")
            raise RuntimeError(
                f"No se pudo sellar la mutación en state_log.jsonl: {e}"
            ) from e

    def validate_state(self, data: dict, *, root: Path | None = None) -> None:
        required_root = [
            "project", "framework_mode", "framework_version", "current_phase",
            "current_step", "last_completed_step", "stack", "dependencies",
            "architecture_constraints", "contracts", "modules", "module_statuses",
            "work_tiers", "module_specs", "closure_rejections", "active_mcps", "_refs"
        ]
        for key in required_root:
            if key not in data:
                raise ValueError(f"Falta clave raíz obligatoria: {key}")
        if data["framework_mode"] not in ["template", "initialized"]:
            raise ValueError("framework_mode debe ser 'template' o 'initialized'")
        if not isinstance(data["current_phase"], str) or not re.match(r"^phase_[0-9]{2}$", data["current_phase"]):
            raise ValueError("current_phase debe ser string 'phase_XX'")
        if not isinstance(data["stack"], dict) or not all(k in data["stack"] for k in ["backend", "frontend", "database", "auth"]):
            raise ValueError("stack incompleto")
        for k in ["dependencies", "architecture_constraints", "active_mcps"]:
            if not isinstance(data[k], list) or not all(isinstance(x, str) for x in data[k]):
                raise ValueError(f"{k} debe ser lista de strings")
        modules = data["modules"]
        if not isinstance(modules, dict) or not all(k in modules for k in ["completed", "in_progress", "pending"]):
            raise ValueError("modules incompleto")
        module_statuses = data["module_statuses"]
        if not isinstance(module_statuses, dict) or not all(isinstance(v, str) for v in module_statuses.values()):
            raise ValueError("module_statuses debe ser objeto de strings")
        for mod, stat in module_statuses.items():
            if stat not in _transitions.VALID_MODULE_STATUSES:
                raise ValueError(f"Estado de módulo inválido para {mod}: {stat}")
        work_tiers = data["work_tiers"]
        if not isinstance(work_tiers, dict):
            raise ValueError("work_tiers debe ser objeto")
        for unit, entry in work_tiers.items():
            if not isinstance(entry, dict) or not all(k in entry for k in ["tier", "rationale", "classified_at", "classified_by"]):
                raise ValueError(f"work_tiers.{unit} incompleto")
            if entry["tier"] not in ["tier_0", "tier_1", "tier_2"]:
                raise ValueError(f"work_tiers.{unit}.tier inválido")
        module_specs = data["module_specs"]
        if not isinstance(module_specs, dict):
            raise ValueError("module_specs debe ser objeto")
        for mod, entry in module_specs.items():
            if not isinstance(entry, dict):
                raise ValueError(f"module_specs.{mod} debe ser objeto")
            if set(entry.keys()) == {"stabilization"}:
                if not isinstance(entry["stabilization"], bool):
                    raise ValueError(f"module_specs.{mod}.stabilization debe ser booleano")
                continue
            if not all(k in entry for k in ["data_model", "api_contract", "classified_at", "classified_by"]):
                raise ValueError(f"module_specs.{mod} incompleto")
            if (entry["data_model"] == "n/a" or entry["api_contract"] == "n/a") and not entry.get("justification"):
                raise ValueError(f"module_specs.{mod} requiere justification por ser n/a")
            ir = entry.get("integration_real")
            if ir is not None and ir not in ["required", "n/a", "done"]:
                raise ValueError(f"module_specs.{mod}.integration_real inválido: {ir!r}")
            if ir == "n/a" and not entry.get("justification"):
                raise ValueError(
                    f"module_specs.{mod}.integration_real es 'n/a' sin justification"
                )

        # FW-225: lifecycle y changes son opcionales (ausente = development).
        import maintenance as _maintenance
        _maintenance.validate_lifecycle_state(data)

        # contracts.public_interfaces (schema-required)
        contracts = data["contracts"]
        if not isinstance(contracts, dict) or "public_interfaces" not in contracts \
                or not isinstance(contracts["public_interfaces"], dict):
            raise ValueError("contracts.public_interfaces debe ser objeto")

        # closure_rejections records
        rejection_ids: list[str] = []
        for r in data.get("closure_rejections", []):
            if not isinstance(r, dict) or not all(k in r for k in ["module_id", "reason", "timestamp"]):
                raise ValueError("closure_rejections requiere module_id, reason, timestamp")
            if r["reason"] not in VALID_CLOSURE_REASONS:
                raise ValueError(f"closure_rejections.reason inválido: {r['reason']}")
            if "id" in r:
                if not isinstance(r["id"], str) or not r["id"].strip():
                    raise ValueError("closure_rejections.id debe ser string no vacío")
                rejection_ids.append(r["id"])
            event = r.get("resolution_event")
            if event is not None:
                if not isinstance(event, dict) or event.get("action") != "resolve_closure_rejections":
                    raise ValueError("closure_rejections.resolution_event inválido")
                if event.get("id") != r.get("id") or event.get("timestamp") != r.get("resolved_at"):
                    raise ValueError("closure_rejections.resolution_event no coincide con la resolución")

        # closure_rejection_archive is optional for backwards compatibility,
        # but every entry written by the lifecycle must remain auditable.
        archive = data.get("closure_rejection_archive", [])
        if not isinstance(archive, list):
            raise ValueError("closure_rejection_archive debe ser lista")
        for r in archive:
            required = [
                "id", "module_id", "reason", "timestamp", "resolved_at",
                "resolution", "evidence", "archived_at",
            ]
            if not isinstance(r, dict) or not all(k in r for k in required):
                raise ValueError(
                    "closure_rejection_archive requiere id, module_id, reason, "
                    "timestamp, resolved_at, resolution, evidence y archived_at"
                )
            if r["reason"] not in VALID_CLOSURE_REASONS:
                raise ValueError(f"closure_rejection_archive.reason inválido: {r['reason']}")
            for field in required:
                if not isinstance(r[field], str) or not r[field].strip():
                    raise ValueError(f"closure_rejection_archive.{field} debe ser string no vacío")
            rejection_ids.append(r["id"])
            event = r.get("resolution_event")
            if event is not None:
                if not isinstance(event, dict) or event.get("action") != "resolve_closure_rejections":
                    raise ValueError("closure_rejection_archive.resolution_event inválido")
                if event.get("id") != r.get("id") or event.get("timestamp") != r.get("resolved_at"):
                    raise ValueError("closure_rejection_archive.resolution_event no coincide con la resolución")
        duplicate_ids = sorted({identifier for identifier in rejection_ids if rejection_ids.count(identifier) > 1})
        if duplicate_ids:
            raise ValueError(f"IDs de closure_rejections duplicados: {duplicate_ids}")

        # _bugs records
        for b in data.get("_bugs", []):
            if not isinstance(b, dict) or not all(k in b for k in ["id", "module_id", "severity", "description", "status"]):
                raise ValueError("_bugs entry incompleto")
            if b["severity"] not in {"Crítico", "Mayor", "Menor"}:
                raise ValueError(f"_bugs.severity inválido: {b['severity']}")
            if b["status"] not in {"Abierto", "Resuelto", "Deuda Técnica"}:
                raise ValueError(f"_bugs.status inválido: {b['status']}")

        # _clarifications records
        for c in data.get("_clarifications", []):
            if not isinstance(c, dict) or not all(k in c for k in ["id", "blocked_scope", "question", "status"]):
                raise ValueError("_clarifications entry incompleto")
            if c["status"] not in {"Abierta", "Resuelta"}:
                raise ValueError(f"_clarifications.status inválido: {c['status']}")

        # _decisions records
        for d in data.get("_decisions", []):
            if not isinstance(d, dict) or not all(k in d for k in ["id", "title", "context", "decision"]):
                raise ValueError("_decisions entry incompleto")

        # session (optional, object if present)
        if "session" in data and not isinstance(data["session"], dict):
            raise ValueError("session debe ser objeto")
        if isinstance(data.get("session"), dict):
            validate_session_pointer(data["session"], root=root or WORKSPACE_ROOT)
        if isinstance(data.get("session"), dict) and "codex_execution" in data["session"]:
            if not (
                getattr(self, "_is_replay", False)
                or getattr(self, "_migration_mode", False)
            ):
                raise ValueError(
                    "LEGACY_MIGRATION_REQUIRED: session.codex_execution pertenece "
                    "al adaptador Codex retirado; sube primero a 5.3.0 con la plantilla en la "
                    "etiqueta v5.3.0, que trae el migrador"
                )
            from codex_budget_legacy import validate_pointer as _validate_legacy_pointer
            _validate_legacy_pointer(data["session"]["codex_execution"])

        migrations = data.get("framework_migrations")
        if migrations is not None:
            if not isinstance(migrations, dict):
                raise ValueError("framework_migrations debe ser objeto")
            for migration_id, entry in migrations.items():
                if not isinstance(migration_id, str) or not migration_id.strip():
                    raise ValueError("framework_migrations requiere IDs no vacios")
                if not isinstance(entry, dict):
                    raise ValueError(f"framework_migrations.{migration_id} debe ser objeto")
                required = {"type", "status", "archive_path", "archive_sha256"}
                if not required.issubset(entry):
                    raise ValueError(f"framework_migrations.{migration_id} incompleta")
                if entry["type"] != "codex_budget_retirement":
                    raise ValueError(f"framework_migrations.{migration_id}.type invalido")
                if entry["status"] not in {"applied", "reverted"}:
                    raise ValueError(f"framework_migrations.{migration_id}.status invalido")
                archive_path = entry["archive_path"]
                if (
                    not isinstance(archive_path, str)
                    or not archive_path.startswith("_project/migrations/codex-budget-retirement/")
                    or Path(archive_path).is_absolute()
                    or ".." in Path(archive_path).parts
                    or "\\" in archive_path
                ):
                    raise ValueError(f"framework_migrations.{migration_id}.archive_path invalido")
                if not isinstance(entry["archive_sha256"], str) or not re.fullmatch(
                    r"[0-9a-f]{64}", entry["archive_sha256"]
                ):
                    raise ValueError(f"framework_migrations.{migration_id}.archive_sha256 invalido")

        receipts = data.get("workflow_receipts")
        if receipts is not None:
            if not isinstance(receipts, dict):
                raise ValueError("workflow_receipts debe ser objeto")
            entries = receipts.get("approvals", {})
            if not isinstance(entries, dict) or not all(isinstance(v, dict) for v in entries.values()):
                raise ValueError("workflow_receipts.approvals debe ser objeto de recibos")
            for module, receipt in entries.items():
                if not isinstance(module, str) or receipt.get("module") != module:
                    raise ValueError("workflow_receipts.approvals tiene módulo inconsistente")
            for module, receipt in receipts.get("approvals", {}).items():
                required = {
                    "status", "tier", "approved_by", "approval_evidence",
                    "risk_summary", "adr_refs", "basis", "fingerprint",
                }
                if not required.issubset(receipt):
                    raise ValueError(f"APPROVE_RECEIPT incompleto para '{module}'")
                if receipt.get("status") != "approved" or receipt.get("approved_by") != "builder":
                    raise ValueError(f"APPROVE_RECEIPT inválido para '{module}'")
                if not isinstance(receipt.get("adr_refs"), list) or not isinstance(receipt.get("basis"), dict):
                    raise ValueError(f"APPROVE_RECEIPT mal formado para '{module}'")
                for field in ("approval_evidence", "risk_summary", "fingerprint"):
                    if not isinstance(receipt.get(field), str) or not receipt[field].strip():
                        raise ValueError(f"APPROVE_RECEIPT.{field} vacío para '{module}'")

            # FW-165: `reviews[module]` es historial append-only (lista), no un
            # recibo único — cada ronda de revisión independiente es una
            # entrada propia; el gate de cierre solo mira la última.
            review_entries = receipts.get("reviews", {})
            if not isinstance(review_entries, dict):
                raise ValueError("workflow_receipts.reviews debe ser objeto de historiales")
            for module, history in review_entries.items():
                if not isinstance(module, str) or not isinstance(history, list) or not history:
                    raise ValueError(f"workflow_receipts.reviews[{module}] debe ser una lista no vacía de recibos")
                for idx, receipt in enumerate(history):
                    if not isinstance(receipt, dict) or receipt.get("module") != module:
                        raise ValueError(f"workflow_receipts.reviews[{module}] tiene ronda inconsistente")
                    required = {"status", "reviewer_id", "implementer_id", "mode", "diff_hash", "started_at"}
                    if not required.issubset(receipt):
                        raise ValueError(f"review receipt incompleto para '{module}' (ronda {idx})")
                    if receipt.get("status") not in {"pending", "pass", "fail"}:
                        raise ValueError(f"review receipt.status inválido para '{module}' (ronda {idx})")
                    if receipt.get("status") == "pending" and idx != len(history) - 1:
                        raise ValueError(f"review receipt 'pending' solo es válido en la última ronda de '{module}'")
                    if receipt.get("mode") not in {
                        "READ_ONLY_SUBAGENTS", "INDEPENDENT_SUBAGENT_NO_ISOLATION",
                        "HUMAN_INDEPENDENT_REVIEW",
                    }:
                        raise ValueError(f"review receipt.mode inválido para '{module}' (ronda {idx})")
                    for field in ("reviewer_id", "implementer_id", "diff_hash", "started_at"):
                        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
                            raise ValueError(f"review receipt.{field} vacío para '{module}' (ronda {idx})")
                    if receipt.get("reviewer_id") == receipt.get("implementer_id"):
                        raise ValueError(f"review receipt sin independencia para '{module}' (ronda {idx})")
                    if receipt.get("status") in {"pass", "fail"}:
                        if receipt.get("result") != receipt.get("status") or not str(receipt.get("evidence") or "").strip():
                            raise ValueError(f"review receipt final sin resultado/evidencia para '{module}' (ronda {idx})")

    @staticmethod
    def _now_iso() -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _reopen_completed_module_state(self, mod_id: str, reason: str) -> None:
        """Núcleo compartido de reapertura: mueve completed → in_progress e
        invalida la evidencia de cierre (approval/review receipts quedan
        obsoletos). Usado por `reopen_module` (reapertura explícita) y por
        `register_bug` (FW-020): un bug Crítico o Mayor+affects_ac sobre un
        módulo `completed` reabre por el mismo camino, para no dejar receipts
        de cierre obsoletos apuntando a un módulo que ya no está cerrado."""
        modules = self.state_data.setdefault(
            "modules", {"completed": [], "in_progress": [], "pending": []}
        )
        if mod_id in modules.get("completed", []):
            modules["completed"].remove(mod_id)
        if mod_id not in modules.setdefault("in_progress", []):
            modules["in_progress"].append(mod_id)
        self.state_data.setdefault("module_statuses", {})[mod_id] = "in_progress"
        receipts = self.state_data.get("workflow_receipts", {})
        receipts.get("approvals", {}).pop(mod_id, None)
        receipts.get("reviews", {}).pop(mod_id, None)
        self.state_data["last_completed_step"] = f"Reabierto módulo {mod_id}: {reason}"

    def mutate(self, action: str, payload: dict) -> str:
        """Única vía de mutación. Serializada con state_write_lock para que el
        servidor MCP y el CLI no se pisen (read-modify-write completo bajo lock)."""
        with state_write_lock():
            return self._mutate_locked(action, payload)

    def _mutate_locked(self, action: str, payload: dict) -> str:
        self._post_log_notice = ""
        for internal in ("_receipt", "_keeps_completed"):
            if internal in payload:
                raise ValueError(f"{internal} es metadata interna del motor; no se acepta desde el caller")
        if action in {
            "retire_codex_budget", "restore_codex_budget", "grandfather_checkpoint_gate",
        } and not getattr(self, "_migration_mode", False):
            raise ValueError(
                f"{action} solo puede ejecutarse mediante el migrador de compatibilidad"
            )
        _reject_unknown_payload_keys(action, payload)
        payload = copy.deepcopy(payload)
        # `mutate()` ya posee `state_write_lock`: una transacción pendiente debe
        # recuperarse a través de ESE lock, no intentar una adquisición anidada
        # que agota el timeout. Sin esto, una recuperación durante una mutación
        # se cuelga 10s y aborta (FW-182).
        self.load_state(lock_held=True)
        self.state_data.setdefault("_bugs", [])
        self.state_data.setdefault("_clarifications", [])
        self.state_data.setdefault("_decisions", [])
        now_iso = self._now_iso()

        # FW-103 (Example CLI): normalizar ANTES de cualquier validación
        # los campos list[str] del payload conocidos por sufrir la
        # corrupción del transport MCP de OpenCode. `payload` ya es la copia
        # local (deepcopy arriba), así que mutarla in-place aquí es seguro y
        # el valor normalizado es lo único que ve el resto del pipeline
        # (validación, _apply_event, y lo que queda en state_log.jsonl).
        _transport_warnings: list[str] = []
        for _field in _TRANSPORT_LIST_PAYLOAD_FIELDS.get(action, ()):
            if _field not in payload:
                continue
            _original = payload[_field]
            _unwrapped = _unwrap_transport_list(_original)
            if _unwrapped is not _original:
                payload[_field] = _unwrapped
                _transport_warnings.append(
                    f"payload normalizado: patrón de transport OpenCode detectado en {_field}"
                )

        # A4 (gate): add_work_tier debe referirse a un módulo declarado.
        # Vive aquí, no en _apply_event, para que la replay_state_log pueda
        # aceptar eventos históricos sin re-validar.
        if action == "add_work_tier":
            unit = payload.get("id")
            # Retroporte Example CLI FW-005: los bugfixes/tareas puntuales
            # no son módulos declarados pero sí necesitan tier (cambio-seguro
            # paso 0.25). El prefijo explícito (`bug:`/`task:`) declara la
            # naturaleza de la unidad y exime el check de módulo — un id sin
            # prefijo sigue exigiendo módulo declarado (typo-guard intacto).
            is_task_unit = isinstance(unit, str) and unit.startswith(("task:", "bug:"))
            known_modules = set(
                self.state_data.get("modules", {}).get("completed", [])
            ) | set(
                self.state_data.get("modules", {}).get("in_progress", [])
            ) | set(
                self.state_data.get("modules", {}).get("pending", [])
            )
            if not is_task_unit and unit not in known_modules:
                raise ValueError(
                    f"add_work_tier: '{unit}' no es un módulo declarado. "
                    f"Decláralo primero en modules.completed/in_progress/pending, "
                    f"o usa el prefijo 'task:'/'bug:' para una unidad puntual. "
                    f"Conocidos: {sorted(known_modules) or 'ninguno'}"
                )

        # A5 (advertencia): leer la fase previa ANTES de _apply_event porque
        # _apply_event ya modifica state["current_phase"]. La warn es
        # cosmética, no de validación — la replay no debe re-notificar.
        prev_phase_before = None
        # A7 (Regla 6, triaje 2026-07-09 C1): `completed` NO es alcanzable por
        # la acción genérica — solo `complete_module` (que ejecuta los gates de
        # cierre dentro del motor). Vive aquí y no en _apply_event para que
        # replay_state_log pueda reproducir eventos históricos.
        if action == "update_module_status" and payload.get("status") == "completed":
            raise ValueError(
                "update_module_status no puede transicionar a 'completed' (Regla 6): "
                "usar la acción 'complete_module', que ejecuta los gates de cierre "
                "(tier, specs, bloqueos, ACs) dentro del motor. El módulo debe estar "
                "en 'ready_for_validation' (el paso 0 de /cierre-modulo aplica esa "
                "transición; FW-003 BF)."
            )

        if action == "set_phase":
            prev_phase_before = self.state_data.get("current_phase", "phase_00")

        # C5 (triaje 2026-07-09): mutación transaccional. Snapshot de la
        # memoria antes de aplicar; validar ANTES de cualquier I/O; ante
        # cualquier fallo, restaurar el snapshot para que memoria y disco
        # nunca diverjan (el MCP sirve self.state_data).
        import copy as _copy
        _snapshot = _copy.deepcopy(self.state_data)
        # El snapshot de memoria no basta: una mutación publica estado,
        # proyecciones y bitácora, y no existe rename atómico de varios nombres
        # independientes. El manifiesto convierte esa limitación física en una
        # transacción crash-consistent — si el proceso muere a mitad, el
        # siguiente lector restaura los bytes exactos previos antes de servir
        # nada. Retroportado de Boton (FW-174).
        _file_snapshot = self._capture_mutation_files()

        # Resolver un rechazo puede saltarse SOLO el criterio de muerte — que es
        # la razón de existir de esta acción reparadora. El resto de gates de
        # cierre siguen teniendo que pasar antes de tocar el estado.
        if action == "resolve_closure_rejections" and not getattr(self, "_is_replay", False):
            try:
                self._validate_closure_rejection_resolution(payload)
            except BaseException:
                self.state_data = _snapshot
                raise

        affected_ids: list[str] | None = None
        if action == "resolve_closure_rejections":
            _active = [
                rejection
                for rejection in self.state_data.get("closure_rejections", [])
                if isinstance(rejection, dict)
                and rejection.get("module_id") == payload.get("module_id")
                and self._is_active_closure_rejection(rejection)
            ]
            for rejection in _active:
                rejection.setdefault("id", self._next_closure_rejection_id(self.state_data))
            affected_ids = [rejection["id"] for rejection in _active]
        elif action in {"restore_closure_rejections", "backfill_closure_rejection_provenance"}:
            affected_ids = list(payload.get("archive_ids") or payload.get("resolved_ids") or [])
        elif action == "complete_module":
            _target = payload.get("id")
            affected_ids = [
                rejection["id"]
                for rejection in self.state_data.get("closure_rejections", [])
                if isinstance(rejection, dict)
                and rejection.get("module_id") == _target
                and rejection.get("resolved_at")
                and not rejection.get("restored_at")
                and isinstance(rejection.get("id"), str)
            ]

        self._write_mutation_manifest(_file_snapshot)
        try:
            msg = self._apply_event(action, payload, now_iso)
            self.validate_state(self.state_data)

            # A6: render ANTES de save_state/log para que un fallo de render
            # aborte la mutación sin persistir. Con C5, además: la validación
            # corre ANTES del render (un candidato inválido jamás se proyecta)
            # y las vistas se construyen completas y se publican atómicas
            # (projections._atomic_write), así que un fallo de construcción no
            # deja proyecciones parciales.
            self.render_projections()
        except BaseException:
            self.state_data = _snapshot
            self._restore_mutation_files(_file_snapshot)
            self._clear_mutation_manifest()
            self._state_sig = self._file_signature()
            raise

        if action == "set_phase" and prev_phase_before is not None:
            phase = payload.get("current_phase")
            if re.match(r"^phase_[0-9]{2}$", prev_phase_before or "") and re.match(r"^phase_[0-9]{2}$", phase):
                prev_num = int(prev_phase_before.split("_")[1])
                new_num = int(phase.split("_")[1])
                if abs(new_num - prev_num) > 1:
                    msg = (
                        f"WARN: salto de fase {prev_phase_before} → {phase} detectado "
                        f"(diferencia de {abs(new_num - prev_num)} fases). "
                        f"Para cambios entre fases, usa `/nueva-fase` que valida el "
                        f"criterio de salida de la fase previa. {msg}"
                    )

        if _transport_warnings:
            msg = f"{msg} " + " ".join(_transport_warnings)

        # save también dentro de la protección transaccional: si falla, la
        # memoria vuelve al snapshot para no divergir del disco. El log queda
        # fuera (best-effort por diseño: un fallo de bitácora no revierte una
        # mutación ya persistida).
        # `state_log.jsonl` entra en la transacción: antes era best-effort y un
        # fallo de bitácora dejaba estado nuevo SIN su evento, que es
        # exactamente lo que la cadena de hashes existe para impedir.
        try:
            self.save_state()
            # Los ids afectados viajan EN el evento: es lo que permite a
            # validate_closure comprobar después que una resolución estaba
            # respaldada por la bitácora y no inventada en el estado.
            event_payload = copy.deepcopy(payload)
            if action == "resolve_closure_rejections":
                event_payload["resolved_ids"] = affected_ids or []
            elif action == "restore_closure_rejections":
                event_payload["restored_ids"] = affected_ids or []
            elif action == "backfill_closure_rejection_provenance":
                event_payload["provenance_ids"] = affected_ids or []
            elif action == "complete_module":
                event_payload["archived_ids"] = affected_ids or []
            self.append_event(action, event_payload, msg, now_iso)
            self._clear_mutation_manifest()
            msg += getattr(self, "_post_log_notice", "")
        except BaseException:
            self.state_data = _snapshot
            self._restore_mutation_files(_file_snapshot)
            self._clear_mutation_manifest()
            self._state_sig = self._file_signature()
            raise
        return msg

    def _validate_closure_rejection_resolution(self, payload: dict) -> None:
        module_id = payload.get("module_id")
        resolution = payload.get("resolution")
        evidence = payload.get("evidence")
        if not isinstance(module_id, str) or not module_id.strip():
            raise ValueError("resolve_closure_rejections requiere 'module_id' no vacío")
        if not isinstance(resolution, str) or not resolution.strip():
            raise ValueError("resolve_closure_rejections requiere 'resolution' no vacío")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("resolve_closure_rejections requiere 'evidence' no vacío")
        self._validate_audit_text("resolution", resolution)
        self._validate_audit_text("evidence", evidence)
        module_id = module_id.strip()
        if not self._is_known_module(self.state_data, module_id):
            raise ValueError(f"resolve_closure_rejections: módulo desconocido '{module_id}'")
        if self.state_data.get("module_statuses", {}).get(module_id) != "ready_for_validation":
            raise ValueError(
                f"resolve_closure_rejections: '{module_id}' debe estar en ready_for_validation"
            )
        active = [
            rejection
            for rejection in self.state_data.get("closure_rejections", [])
            if isinstance(rejection, dict)
            and rejection.get("module_id") == module_id
            and self._is_active_closure_rejection(rejection)
        ]
        if not active:
            raise ValueError(
                f"resolve_closure_rejections: no hay rechazos activos para '{module_id}'"
            )

        import validate_acceptance as _va
        import validate_closure as _vc
        import workflow_receipts as _wr

        problems = [
            problem
            for problem in _vc.closure_gate(
                self.state_data,
                module_id,
                current_diff_hash=_wr.workspace_diff_hash(self._workspace_root()),
                workspace_root=self._workspace_root(),
            )
            if not problem.startswith("death criterion:")
        ]
        acceptance = self._workspace_root() / "_project" / "08_module_acceptance.md"
        if not acceptance.exists():
            problems.append("falta _project/08_module_acceptance.md")
        else:
            acceptance_text = acceptance.read_text(encoding="utf-8", errors="ignore")
            problems.extend(_vc.ac_coverage_problems(acceptance_text, module_id))
            problems.extend(_va.evidence_gate(acceptance_text, module_id, root=self._workspace_root(), state=self.state_data))
        if problems:
            raise ValueError(
                f"resolve_closure_rejections bloqueado para '{module_id}':\n  - "
                + "\n  - ".join(problems)
            )

    def _module_blockers(self, mod_id: str) -> tuple[list[dict], list[dict]]:
        """Devuelve (blocking_bugs, blocking_clars) activos para mod_id (N3)."""
        bugs = self.state_data.get("_bugs", [])
        clars = self.state_data.get("_clarifications", [])
        blocking_bugs = [
            b for b in bugs
            if b.get("module_id") == mod_id
            and b.get("status") == "Abierto"
            and (b.get("severity") == "Crítico" or (b.get("severity") == "Mayor" and b.get("affects_ac")))
        ]
        blocking_clars = [
            c for c in clars
            if c.get("blocked_scope") == mod_id
            and c.get("status") == "Abierta"
            and (c.get("blocks_start") or c.get("blocks_implementation") or c.get("blocks_closure"))
        ]
        return blocking_bugs, blocking_clars

    def _scope_module_hint(self, described: str) -> str:
        """Aviso de texto si el scope declara un módulo distinto del descrito (solo lectura)."""
        try:
            import context_sources

            scope = (WORKSPACE_ROOT / "_project" / "04_active_scope.md").resolve()
            if not scope.is_relative_to(WORKSPACE_ROOT.resolve()) or not scope.is_file():
                return ""
            declared = context_sources.declared_scope_module(scope.read_text(encoding="utf-8"))
        except Exception:
            return ""
        known = self.state_data.get("module_statuses") or {}
        if not declared or declared == described or declared not in known:
            return ""
        return (
            f" AVISO: la descripción se sella con el módulo activo '{described}', pero"
            f" _project/04_active_scope.md declara '{declared}'. Si trabajas en '{declared}',"
            f" reordena con set_session_pointer y next_module='{declared}'."
        )

    def _apply_event(self, action: str, payload: dict, now_iso: str) -> str:
        """Aplica la mutación lógica al state_data sin side effects (no save, no
        log, no render). Lo usa `_mutate_locked` (camino normal) y
        `replay_state_log.py` (disaster recovery, sin gates A4/A5).

        Este método NO enforce A4 (add_work_tier módulo declarado) ni A5
        (set_phase warn): esos gates viven en `_mutate_locked` para que la
        replay pueda aceptar eventos históricos sin re-validar con reglas
        que pudieron no existir cuando se loguearon.
        """
        if action == "update_module_status":
            mod_id = payload.get("id")
            new_status = payload.get("status")
            if not mod_id or not new_status:
                raise ValueError("Se requiere 'id' y 'status'")
            prev_status = self.state_data.setdefault("module_statuses", {}).get(mod_id)
            # PA-FW-037 (revisión 5.3.3): `dropped -> dropped` es legal en
            # LEGAL_PREV (el gate de git compara estados sin cambio), pero aquí
            # metía el módulo en modules.pending sin sacarlo de modules.dropped.
            # update_module_status nunca entra ni sale de `dropped`.
            if new_status == "dropped" or self._is_dropped_module(self.state_data, mod_id):
                raise ValueError(
                    f"Transición ilegal para '{mod_id}': {prev_status} -> {new_status} — "
                    "`dropped` solo se alcanza con abandon_module (motivo + evidencia) y es "
                    "terminal: un módulo abandonado no se reabre ni vuelve a arrancar (PA-FW-037)"
                )
            if not _transitions.is_legal(prev_status, new_status):
                # FW-186: el replay reproduce lo que ocurrió. Un evento en la
                # bitácora encadenada ya fue aceptado por el motor cuando se
                # escribió; una transición que las reglas ACTUALES de
                # `_transitions.LEGAL_PREV` rechazan (p. ej. `None ->
                # ready_for_validation`, legal antes de que se restringiera) no
                # debe abortar la reconstrucción — mismo principio que A4/A5,
                # que `_apply_event` ya no re-valida en replay. Un `new_status`
                # que nunca fue un estado válido sí sigue fallando.
                replay_tolerable = (
                    getattr(self, "_is_replay", False)
                    and new_status in _transitions.LEGAL_PREV
                )
                if not replay_tolerable:
                    message = f"Transición ilegal para '{mod_id}': {prev_status} -> {new_status}"
                    if prev_status == "in_progress" and new_status == "completed":
                        message += (
                            " — pasa primero por 'ready_for_validation'"
                            " (el paso 0 de /cierre-modulo aplica esa transición antes de cerrar; FW-003 BF)"
                        )
                    raise ValueError(message)

            is_replay = getattr(self, "_is_replay", False)
            if not is_replay:
                # N3: Rechazo al salir de blocked con bloqueantes vivos
                if prev_status == "blocked" and new_status in ("in_progress", "not_started"):
                    blocking_bugs, blocking_clars = self._module_blockers(mod_id)
                    if blocking_bugs or blocking_clars:
                        blockers_list = []
                        for b in blocking_bugs:
                            blockers_list.append(f"{b.get('id', 'BUG-?')} ({b.get('severity', 'Mayor')})")
                        for c in blocking_clars:
                            blockers_list.append(f"{c.get('id', 'CLAR-?')}")
                        raise ValueError(
                            f"No se puede desbloquear '{mod_id}' ({prev_status} -> {new_status}) mientras tenga "
                            f"bloqueantes abiertos: {', '.join(blockers_list)}. Resuélvelos con resolve_bug / "
                            "resolve_clarification (desbloquean solos al quedar sin bloqueantes) o defer_bug_to_tech_debt."
                        )

                # N4: Criterio de parada al arrancar módulo nuevo (None/not_started -> in_progress)
                if new_status == "in_progress" and prev_status in (None, "not_started"):
                    specs_entry = self.state_data.get("module_specs", {}).get(mod_id, {})
                    is_stabilization = isinstance(specs_entry, dict) and bool(specs_entry.get("stabilization"))
                    if not is_stabilization:
                        crit_bugs = [
                            b.get("id", "BUG-?")
                            for b in self.state_data.get("_bugs", [])
                            if b.get("status") == "Abierto" and b.get("severity") == "Crítico"
                        ]
                        rejections = self.state_data.get("closure_rejections", [])
                        active_rejections = [
                            (r.get("id") or f"{r.get('module_id')}/{r.get('reason')}") for r in rejections
                            if isinstance(r, dict) and (not r.get("resolved_at") or r.get("restored_at"))
                        ]
                        motivos = []
                        if crit_bugs:
                            motivos.append(f"bug(s) crítico(s) abierto(s): {', '.join(crit_bugs)}")
                        if len(active_rejections) >= 2:
                            motivos.append(f"{len(active_rejections)} rechazos de cierre sin resolver: {', '.join(active_rejections)}")
                        elif len(rejections) >= 2 and not active_rejections:
                            motivos.append(f"{len(rejections)} rechazos de cierre en el historial")

                        if motivos:
                            motivo_str = "; ".join(motivos)
                            raise ValueError(
                                f"criterio de parada activo ({motivo_str}): abre un módulo de estabilización "
                                "(add_module con stabilization: true) o cierra los rechazos con resolve_closure_rejections."
                            )

            self.state_data["module_statuses"][mod_id] = new_status
            modules = self.state_data.setdefault("modules", {"completed": [], "in_progress": [], "pending": []})
            for b in ["completed", "in_progress", "pending"]:
                if mod_id in modules.get(b, []):
                    modules[b].remove(mod_id)
            if new_status == "completed":
                modules.setdefault("completed", []).append(mod_id)
                # ARCHIVAR, no borrar. La Regla 7 (≥2 rechazos → módulo
                # "Estabilización") se apoya en `closure_rejections`, y el
                # cierre los eliminaba sin dejar rastro: la evidencia del
                # criterio de muerte desaparecía justo al cerrar, de modo que
                # un módulo rechazado dos veces quedaba indistinguible de uno
                # limpio. Ahora solo se retiran los rechazos EXPLÍCITAMENTE
                # resueltos (vía `resolve_closure_rejections`, que exige
                # resolución y evidencia auditables), y se archivan con
                # procedencia. Un rechazo no resuelto permanece y sigue
                # contando (FW-175).
                self._archive_module_closure_rejections(mod_id, now_iso)
                # Transition module specs from required to done.
                # `setdefault(mod_id, {})` creaba una entrada VACÍA cuando el
                # módulo no tenía spec clasificada — y una entrada vacía es
                # exactamente lo que validate_state rechaza
                # ("module_specs.X incompleto"), así que el motor producía un
                # estado que su propia validación no acepta. En uso normal no
                # se veía (el gate de cierre exige la spec antes), pero sí en
                # replay: era la segunda causa de que la auditoría fuera
                # imposible (sabotaje controlado 2026-07-24). Si no hay spec,
                # no hay nada que transicionar.
                specs = self.state_data.get("module_specs", {}).get(mod_id)
                if isinstance(specs, dict) and specs:
                    for key in ["data_model", "api_contract"]:
                        if specs.get(key) == "required":
                            specs[key] = "done"
            elif new_status in ["in_progress", "blocked", "ready_for_validation"]:
                modules.setdefault("in_progress", []).append(mod_id)
            else:
                modules.setdefault("pending", []).append(mod_id)

            # Example Tool FW-T08: un módulo podía arrancar (not_started → in_progress)
            # e implementarse por completo sin pasar nunca por /scope-siguiente
            # ni /enriquecer-ticket — el hueco (module_specs y ACs ausentes)
            # solo se descubría en /cierre-modulo, obligando a reconstruir ACs
            # retroactivamente durante el propio cierre. AVISO temprano, no
            # bloqueo (el gate duro ya existe en el cierre — GUARDRAILS FW-T08
            # solo pide anticiparlo, no duplicar el gate ni endurecerlo).
            warn_suffix = ""
            if new_status == "in_progress" and prev_status in (None, "not_started"):
                specs = self.state_data.get("module_specs", {}).get(mod_id)
                if not isinstance(specs, dict) or not specs:
                    warn_suffix = (
                        " AVISO: '" + mod_id + "' arranca sin module_specs "
                        "clasificado — /cierre-modulo lo exigirá igual; "
                        "clasificar ahora con classify_module_spec evita "
                        "reconstruir ACs retroactivamente al cerrar."
                    )
            return f"Módulo '{mod_id}' actualizado a '{new_status}'.{warn_suffix}"

        elif action == "register_bug":
            mod_id = payload.get("module_id")
            severity = payload.get("severity")
            desc = payload.get("description")
            affects_ac = payload.get("affects_ac", False)
            if not mod_id or not severity or not desc:
                raise ValueError("Se requiere 'module_id', 'severity' y 'description'")
            if severity not in ["Crítico", "Mayor", "Menor"]:
                raise ValueError("Severidad inválida")
            # PA-FW-037 (revisión 5.3.3): un bug sobre un módulo `dropped` lo
            # resucitaba (blocked + modules.in_progress sin salir de
            # modules.dropped) o dejaba un bug abierto sin módulo vivo.
            if self._is_dropped_module(self.state_data, mod_id):
                raise ValueError(
                    f"register_bug: el módulo '{mod_id}' está dropped (abandonado, terminal). "
                    "Registra el bug en el módulo abierto que absorbió su trabajo o en un "
                    "módulo de estabilización (add_module con stabilization: true)"
                )

            bugs = self.state_data["_bugs"]
            max_num = 0
            for b in bugs:
                if b["id"].startswith("BUG-"):
                    try: max_num = max(max_num, int(b["id"].split("-")[1]))
                    except (IndexError, ValueError): pass
            bug_id = f"BUG-{max_num + 1:03d}"
            bugs.append({
                "id": bug_id, "module_id": mod_id, "severity": severity,
                "description": desc, "affects_ac": affects_ac,
                "status": "Abierto", "fix": "", "affected_files": [], "created_at": now_iso
            })
            # BOTÓN FW-114: el id se autoasigna DESPUÉS de leer el payload del
            # caller, así que nunca quedaba en el evento logueado — el
            # auditor (_logged_ids en replay_state_log.py) busca "id" en el
            # payload de cada register_bug y nunca lo encontraba, marcando
            # TODO bug como "sin respaldo" pese a existir el evento real.
            # Anotarlo aquí (mismo patrón que "_receipt"/"_status_effect")
            # es suficiente: "id" ya es uno de los campos que _logged_ids lee.
            payload["id"] = bug_id
            # FW-300 (5.3.1 L3.2, D3-A): un módulo `completed` no se reabre ni
            # se bloquea por registrar un bug; el bug bloquea por S4 en la
            # vista y reabrir es `reopen_module` con motivo. La marca
            # `_keeps_completed` va en el evento logueado: la reproducción la
            # respeta, y un register_bug histórico sin marca reabre como antes.
            keeps_completed = (
                (severity == "Crítico" or (severity == "Mayor" and affects_ac))
                and self.state_data.get("module_statuses", {}).get(mod_id) == "completed"
                and (not getattr(self, "_is_replay", False) or payload.get("_keeps_completed") is True)
            )
            if keeps_completed:
                payload["_keeps_completed"] = True
                self.state_data["last_completed_step"] = f"Registrado bug {bug_id}."
                return (
                    f"Bug {bug_id} registrado. {mod_id} sigue completed; "
                    "para reabrirlo, reopen_module con motivo."
                )
            if severity == "Crítico" or (severity == "Mayor" and affects_ac):
                # FW-020 (TL): un bug crítico/mayor+affects_ac sobre un módulo
                # `completed` reabría con una mutación directa a 'blocked' que
                # no invalidaba la evidencia de cierre obsoleta (approval/
                # review receipts seguían apuntando a un módulo que ya no
                # está cerrado). Reabrir por el mismo camino que
                # reopen_module deja esa evidencia limpia; la acción sigue
                # siendo 'register_bug' en state_log.jsonl, que el TRANSITION
                # GATE (validate_transition._status_effect) ya reconoce como
                # respaldo legal de completed → blocked.
                if self.state_data.get("module_statuses", {}).get(mod_id) == "completed":
                    self._reopen_completed_module_state(
                        mod_id, f"Bug {bug_id} ({severity}): {desc}"
                    )
                self.state_data.setdefault("module_statuses", {})[mod_id] = "blocked"
                modules = self.state_data.setdefault("modules", {"completed": [], "in_progress": [], "pending": []})
                for b in ["completed", "pending"]:
                    if mod_id in modules.get(b, []): modules[b].remove(mod_id)
                if mod_id not in modules.setdefault("in_progress", []): modules["in_progress"].append(mod_id)

            self.state_data["last_completed_step"] = f"Registrado bug {bug_id}."
            return f"Bug {bug_id} registrado."

        elif action == "amend_bug":
            # FW-122 / FW-110: el registro era append-only sin enmienda — un
            # dato erróneo quedaba erróneo para siempre o se editaba el JSON a
            # mano (violando la Regla 2). La enmienda conserva el valor previo:
            # se corrige el dato, no se borra el rastro.
            ENMENDABLES = ("description", "fix", "affected_files")
            NO_ENMENDABLES = {
                "severity": "cambia la semántica de bloqueo; registra un bug nuevo",
                "status": "usa resolve_bug o defer_bug_to_tech_debt",
                "affects_ac": "cambia la semántica de bloqueo; registra un bug nuevo",
                "module_id": "usa reassign_bug_module (motivo obligatorio, solo bugs Abierto)",
                "id": "el id es inmutable",
            }
            bug_id = payload.get("id")
            reason = payload.get("reason")
            if not isinstance(bug_id, str) or not bug_id.strip():
                raise ValueError("amend_bug requiere 'id' (string no vacío)")
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise ValueError(
                    "amend_bug requiere 'reason' de al menos 20 caracteres "
                    "explicando por qué el dato registrado era incorrecto"
                )
            reason = self._validate_audit_text("reason", reason)
            # "id" y "reason" son campos meta del payload (selector y motivo),
            # nunca candidatos a enmienda: se excluyen antes de chequear.
            campos_dados = set(payload) - {"id", "reason"}
            prohibidos = sorted(campos_dados & set(NO_ENMENDABLES))
            if prohibidos:
                detalle = "; ".join(f"{k}: {NO_ENMENDABLES[k]}" for k in prohibidos)
                raise ValueError(f"amend_bug no puede enmendar {prohibidos} — {detalle}")
            bugs = self.state_data["_bugs"]
            bug = next((b for b in bugs if b["id"] == bug_id.strip()), None)
            if not bug:
                raise ValueError(f"Bug {bug_id} no encontrado")
            cambios = {k: payload[k] for k in ENMENDABLES if k in payload}
            if not cambios:
                raise ValueError(
                    "amend_bug requiere al menos un campo enmendable: "
                    + ", ".join(ENMENDABLES)
                )
            bug.setdefault("amendments", []).append({
                "at": now_iso,
                "reason": reason.strip(),
                "previous": {k: bug.get(k) for k in cambios},
            })
            bug.update(cambios)
            self.state_data["last_completed_step"] = f"Enmendado bug {bug_id}."
            return f"Bug {bug_id} enmendado ({', '.join(sorted(cambios))}). Motivo: {reason.strip()}"

        elif action == "resolve_bug":
            bug_id = payload.get("id")
            fix = payload.get("fix")
            affected_files = payload.get("affected_files", [])
            if not bug_id or not fix:
                raise ValueError("Se requiere 'id' y 'fix'")
            bugs = self.state_data["_bugs"]
            bug = next((b for b in bugs if b["id"] == bug_id), None)
            if not bug:
                raise ValueError(f"Bug {bug_id} no encontrado")
            bug["status"] = "Resuelto"
            bug["fix"] = fix
            bug["affected_files"] = affected_files
            bug["resolved_at"] = now_iso

            mod_id = bug["module_id"]
            blocking_bugs, blocking_clars = self._module_blockers(mod_id)
            if not blocking_bugs and not blocking_clars:
                if self.state_data.get("module_statuses", {}).get(mod_id) == "blocked":
                    is_pending = mod_id in self.state_data.get("modules", {}).get("pending", [])
                    new_status = "not_started" if is_pending else "in_progress"
                    self.state_data["module_statuses"][mod_id] = new_status
                    # TL FW-109 / TL FW-235: el efecto de resolve_bug es condicional (solo
                    # desbloquea si no quedan otros bloqueantes) — no se puede
                    # inferir desde action+payload solos como register_bug/
                    # reopen_module. Se anota en el propio payload logueado
                    # (mismo patrón que "_receipt") para que
                    # validate_transition._status_effect respalde SOLO la
                    # transición que el motor de verdad produjo, nunca una
                    # bendecida a ciegas por el nombre de la acción.
                    payload["_status_effect"] = {"module": mod_id, "status": new_status}
            self.state_data["last_completed_step"] = f"Resuelto bug {bug_id}."
            return f"Bug {bug_id} resuelto."

        elif action == "register_clarification":
            blocked_scope = payload.get("blocked_scope")
            question = payload.get("question")
            title = payload.get("title", "Pregunta")
            def _coerce_bool(value, field):
                # Triaje 2026-07-09: `"false"` (string) se persistía y se
                # evaluaba truthy — bloqueaba el módulo y se proyectaba como
                # true. Solo bool o strings true/false explícitos.
                if isinstance(value, bool):
                    return value
                if isinstance(value, str) and value.strip().lower() in ("true", "false"):
                    return value.strip().lower() == "true"
                raise ValueError(f"{field} debe ser booleano (recibido: {value!r})")
            b_start = _coerce_bool(payload.get("blocks_start", False), "blocks_start")
            b_impl = _coerce_bool(payload.get("blocks_implementation", False), "blocks_implementation")
            b_close = _coerce_bool(payload.get("blocks_closure", False), "blocks_closure")
            cond = payload.get("blocking_condition", "")
            opts = payload.get("options", "")
            imp = payload.get("impact", "")
            if not blocked_scope or not question:
                raise ValueError("Se requiere 'blocked_scope' y 'question'")
            if self._is_dropped_module(self.state_data, blocked_scope):
                # PA-FW-037 (revisión 5.3.3): con blocks_* pasaba el módulo
                # abandonado a `blocked` sin sacarlo de modules.dropped.
                raise ValueError(
                    f"register_clarification: el módulo '{blocked_scope}' está dropped "
                    "(abandonado, terminal). Apunta la clarificación al módulo abierto "
                    "que absorbió su trabajo"
                )
            clars = self.state_data["_clarifications"]
            max_num = 0
            for c in clars:
                if c["id"].startswith("CLAR-"):
                    try: max_num = max(max_num, int(c["id"].split("-")[1]))
                    except (IndexError, ValueError): pass
            clar_id = f"CLAR-{max_num + 1:03d}"
            prev_status = self.state_data.get("module_statuses", {}).get(blocked_scope)
            clars.append({
                "id": clar_id, "title": title, "blocked_scope": blocked_scope,
                "blocks_start": b_start, "blocks_implementation": b_impl, "blocks_closure": b_close,
                "blocking_condition": cond, "question": question, "options": opts, "impact": imp,
                "status": "Abierta", "answer": "", "created_at": now_iso,
                "prev_status": prev_status,
            })
            if (b_start or b_impl or b_close) and blocked_scope in self.state_data.get("module_statuses", {}):
                self.state_data["module_statuses"][blocked_scope] = "blocked"
                payload["_status_effect"] = {"module": blocked_scope, "status": "blocked"}
            self.state_data["last_completed_step"] = f"Registrada clarificación {clar_id}."
            return f"Clarificación {clar_id} registrada."

        elif action == "resolve_clarification":
            clar_id = payload.get("id")
            answer = payload.get("answer")
            if not clar_id or not answer:
                raise ValueError("Se requiere 'id' y 'answer'")
            clars = self.state_data["_clarifications"]
            clar = next((c for c in clars if c["id"] == clar_id), None)
            if not clar:
                raise ValueError(f"Clarificación {clar_id} no encontrada")
            clar["status"] = "Resuelta"
            clar["answer"] = answer
            clar["resolved_at"] = now_iso

            mod_id = clar["blocked_scope"]
            blocking_bugs, blocking_clars = self._module_blockers(mod_id)
            if not blocking_bugs and not blocking_clars:
                if self.state_data.get("module_statuses", {}).get(mod_id) == "blocked":
                    is_pending = mod_id in self.state_data.get("modules", {}).get("pending", [])
                    new_status = "not_started" if is_pending else "in_progress"
                    self.state_data["module_statuses"][mod_id] = new_status
                    payload["_status_effect"] = {"module": mod_id, "status": new_status}
            self.state_data["last_completed_step"] = f"Resuelta clarificación {clar_id}."
            return f"Clarificación {clar_id} resuelta."

        elif action == "update_last_step":
            step = payload.get("last_completed_step")
            if not step: raise ValueError("Se requiere 'last_completed_step'")
            self.state_data["last_completed_step"] = step
            return "last_completed_step actualizado."

        elif action == "add_work_tier":
            unit = payload.get("id")
            tier = payload.get("tier")
            rationale = payload.get("rationale")
            classified_by = payload.get("classified_by", "manual")
            if not unit or not tier or not rationale:
                raise ValueError("id, tier y rationale requeridos")
            entry = {
                "tier": tier, "rationale": rationale,
                "classified_at": now_iso, "classified_by": classified_by
            }
            # FW-151: `executor_problems` exige requires_second_review en TODO
            # tier cuando el ejecutor es 'limited', y `review_receipt_problems`
            # lo honra si el payload lo pide — pero esta acción, única vía
            # canónica de escribir la entrada, solo lo ponía en tier_2. Un
            # ejecutor 'limited' quedaba en deadlock sobre cualquier tier_1: el
            # gate era insatisfacible y la única salida, editar el JSON a mano.
            # Cosechado de Example Service, que lo había parcheado en local sin subirlo
            # (la clase de divergencia silenciosa que registró FW-143).
            if (
                tier == "tier_2"
                or payload.get("requires_second_review") is True
                or (self.state_data.get("execution_profile") or {}).get("capability_tier") == "limited"
            ):
                entry["requires_second_review"] = True
            if payload.get("handles_real_data") is True:
                # FW-083: declaración de riesgo de datos, mismo momento y
                # mismo sitio que el resto de la clasificación de tier.
                entry["handles_real_data"] = True
            self.state_data.setdefault("work_tiers", {})[unit] = entry
            return f"Tier para '{unit}' registrado."

        elif action == "record_approval_receipt":
            import workflow_receipts as _wr
            module = payload.get("module")
            replay_receipt = payload.get("_receipt")
            if getattr(self, "_is_replay", False) and isinstance(replay_receipt, dict):
                # FW-186: el replay reproduce el receipt tal como se selló. Un
                # evento en la bitácora encadenada ya fue aceptado por el motor;
                # la precondición de work_tier no se re-valida (mismo principio
                # que A4/A5) — el `add_work_tier` pudo no haberse logueado si la
                # bitácora se sembró por migración incompleta (FW-173).
                self.state_data.setdefault("workflow_receipts", {}).setdefault(
                    "approvals", {}
                )[module] = copy.deepcopy(replay_receipt)
                return f"APPROVE_RECEIPT reproducido para '{module}'."
            if module not in self.state_data.get("work_tiers", {}):
                raise ValueError("record_approval_receipt requiere módulo con work_tier")
            receipt = _wr.record_approval(
                self.state_data, WORKSPACE_ROOT, module,
                approved_by=payload.get("approved_by", ""),
                evidence=payload.get("evidence", ""),
                risk_summary=payload.get("risk_summary", ""),
                adr_refs=payload.get("adr_refs", []),
                now_iso=now_iso,
                covers_dependencies=payload.get("covers_dependencies"),
                covers_changes=payload.get("covers_changes"),
            )
            payload["_receipt"] = copy.deepcopy(receipt)
            covers = receipt.get("covers_changes")
            declared = f" Declarado en covers_changes (no caduca): {', '.join(covers)}." if covers else ""
            return f"APPROVE_RECEIPT registrado para '{module}' ({receipt['fingerprint'][:12]}).{declared}"

        elif action == "refresh_approval_receipt":
            import workflow_receipts as _wr
            module = payload.get("module")
            replay_receipt = payload.get("_receipt")
            if getattr(self, "_is_replay", False) and isinstance(replay_receipt, dict):
                self.state_data.setdefault("workflow_receipts", {}).setdefault(
                    "approvals", {}
                )[module] = copy.deepcopy(replay_receipt)
                return f"APPROVE_RECEIPT derivado reproducido para '{module}'."
            receipt = _wr.refresh_derived_approval(
                self.state_data, WORKSPACE_ROOT, module,
                parent_fingerprint=payload.get("parent_fingerprint", ""),
                derived_from=payload.get("derived_from", []),
                evidence=payload.get("evidence", ""),
                now_iso=now_iso,
                additive_rationale=payload.get("additive_rationale"),
            )
            payload["_receipt"] = copy.deepcopy(receipt)
            return f"APPROVE_RECEIPT derivado actualizado para '{module}' ({receipt['fingerprint'][:12]})."

        elif action == "start_independent_review":
            import workflow_receipts as _wr
            module = payload.get("module")
            replay_receipt = payload.get("_receipt")
            if getattr(self, "_is_replay", False) and isinstance(replay_receipt, dict):
                # FW-165: el replay reproduce la ronda como una entrada nueva
                # en el historial (append), no reemplaza `reviews[module]`
                # entero — de lo contrario un replay borraría rondas previas
                # de un módulo que ya tuvo más de una.
                # FW-186: y sin re-validar la precondición de work_tier — un
                # `add_work_tier` no logueado (migración incompleta, FW-173) no
                # debe abortar la reconstrucción.
                history = self.state_data.setdefault("workflow_receipts", {}).setdefault(
                    "reviews", {}
                ).setdefault(module, [])
                # FW-186: dos `start_independent_review` seguidos sin su
                # `record_review_receipt` (bitácora pre-FW-163b o migración
                # incompleta) — el segundo REINICIA la ronda abandonada en vez
                # de apilar un segundo `pending`, que `validate_state` (FW-165:
                # pending solo válido en la última ronda) rechazaría.
                if history and history[-1].get("status") == "pending":
                    history[-1] = copy.deepcopy(replay_receipt)
                else:
                    history.append(copy.deepcopy(replay_receipt))
                return f"Review independiente reproducido para '{module}'."
            if module not in self.state_data.get("work_tiers", {}):
                raise ValueError("start_independent_review requiere módulo con work_tier")
            receipt = _wr.start_review(
                self.state_data, WORKSPACE_ROOT, module,
                reviewer_id=payload.get("reviewer_id", ""),
                implementer_id=payload.get("implementer_id", ""),
                mode=payload.get("mode", ""),
                now_iso=now_iso,
            )
            payload["_receipt"] = copy.deepcopy(receipt)
            warns = "".join(f"\n{w}" for w in _wr.test_gate_warnings(self.state_data, module))
            return f"Review independiente iniciado para '{module}' ({receipt['diff_hash'][:12]})." + warns

        elif action == "record_review_receipt":
            import workflow_receipts as _wr
            module = payload.get("module")
            replay_receipt = payload.get("_receipt")
            if getattr(self, "_is_replay", False) and isinstance(replay_receipt, dict):
                # FW-165: el replay reemplaza solo la ÚLTIMA entrada del
                # historial (la ronda que este `finish` cierra), preservando
                # las rondas previas.
                history = self.state_data.setdefault(
                    "workflow_receipts", {}
                ).setdefault("reviews", {}).setdefault(module, [])
                if history:
                    history[-1] = copy.deepcopy(replay_receipt)
                else:
                    history.append(copy.deepcopy(replay_receipt))
                return f"Review independiente reproducido para '{module}'."
            receipt = _wr.finish_review(
                self.state_data, WORKSPACE_ROOT, module,
                reviewer_id=payload.get("reviewer_id", ""),
                result=payload.get("result", ""),
                evidence=payload.get("evidence", ""),
                now_iso=now_iso,
            )
            payload["_receipt"] = copy.deepcopy(receipt)
            return f"Review independiente '{receipt['status']}' registrado para '{module}'."

        elif action == "invalidate_stale_review":
            import workflow_receipts as _wr
            module = payload.get("module")
            if not isinstance(module, str) or not module.strip():
                raise ValueError("invalidate_stale_review requiere 'module'")
            module = module.strip()
            replay_receipt = payload.get("_receipt")
            if getattr(self, "_is_replay", False) and isinstance(replay_receipt, dict):
                history = self.state_data.setdefault(
                    "workflow_receipts", {}
                ).setdefault("reviews", {}).setdefault(module, [])
                if history:
                    history[-1] = copy.deepcopy(replay_receipt)
                else:
                    history.append(copy.deepcopy(replay_receipt))
                return f"Review stale invalidado reproducido para '{module}'."
            receipt = _wr.invalidate_stale_review(
                self.state_data,
                WORKSPACE_ROOT,
                module,
                approved_by=payload.get("approved_by", ""),
                reason=payload.get("reason", ""),
                now_iso=now_iso,
            )
            payload["_receipt"] = copy.deepcopy(receipt)
            return f"Review stale invalidado para '{module}'; iniciar una nueva ronda."

        elif action == "record_execution_receipt":
            # FW-144: sella el EJECUTABLE GATE (cierre-modulo paso 3.8). Mismo
            # patrón de replay que los receipts de review: la historia se
            # reproduce, no se vuelve a juzgar contra el workspace de hoy.
            import workflow_receipts as _wr
            module = payload.get("module")
            if not isinstance(module, str) or not module.strip():
                raise ValueError("record_execution_receipt requiere 'module'")
            module = module.strip()
            replay_receipt = payload.get("_receipt")
            if getattr(self, "_is_replay", False) and isinstance(replay_receipt, dict):
                self.state_data.setdefault("workflow_receipts", {}).setdefault(
                    "executions", {}
                )[module] = copy.deepcopy(replay_receipt)
                return f"Verificación ejecutable reproducida para '{module}'."
            receipt = _wr.record_execution(
                self.state_data, WORKSPACE_ROOT, module,
                kind=payload.get("kind", ""),
                command=payload.get("command", ""),
                exit_code=payload.get("exit_code"),
                evidence=payload.get("evidence", ""),
                now_iso=now_iso,
                test_changes=payload.get("test_changes"),
                base_ref=payload.get("base_ref"),
                tests_run=payload.get("tests_run"),
            )
            payload["_receipt"] = copy.deepcopy(receipt)
            # FW-250: "sin_base" nunca es silencioso.
            warns = "".join(f"\n{w}" for w in _wr.test_gate_warnings(self.state_data, module))
            return (
                f"Verificación ejecutable '{receipt['kind']}' registrada para "
                f"'{module}' ({receipt['diff_hash'][:12]})." + warns
            )

        elif action == "record_harvest":
            # FW-201/FW-185 (cierre-modulo paso 5.5): declara la cosecha de
            # fricción del módulo — el gate exige DECLARAR, no ENCONTRAR.
            # "none" es un valor de primera clase (cero hallazgos es normal y
            # preferible a inventar); una lista vacía o cualquier otra forma es
            # error de uso, no una declaración legítima de "nada". Sin
            # exención de replay propia: es determinista del payload, así que
            # el replay normal (mismo action, mismo payload) reproduce el
            # mismo estado sin necesitar una rama _is_replay separada — a
            # diferencia de los receipts, que dependen del workspace del
            # momento en que se sellaron.
            module_id = payload.get("module_id")
            if not isinstance(module_id, str) or not module_id.strip():
                raise ValueError("record_harvest requiere 'module_id' (string no vacío)")
            module_id = module_id.strip()
            classes = payload.get("classes")
            valid_none = classes == "none"
            valid_list = isinstance(classes, list) and len(classes) > 0 and all(
                isinstance(c, str) and c.strip() for c in classes
            )
            if not (valid_none or valid_list):
                raise ValueError(
                    "record_harvest: 'classes' debe ser el literal 'none' o una "
                    "lista no vacía de slugs (string) — otra forma no es una "
                    "declaración válida (ver .agent/prompts/cosecha_sesion.md)"
                )
            entry = self.state_data.get("work_tiers", {}).get(module_id)
            if not isinstance(entry, dict):
                raise ValueError(
                    f"record_harvest: '{module_id}' no tiene work_tiers — "
                    "clasifica el tier primero (add_work_tier)"
                )
            entry["harvest"] = {"classes": classes, "recorded_at": now_iso}
            if valid_none:
                return f"Cosecha de fricción registrada para '{module_id}': ninguna."
            return (
                f"Cosecha de fricción registrada para '{module_id}': "
                f"{len(classes)} clase(s) — {', '.join(classes)}."
            )

        elif action == "record_data_safety_check":
            # FW-083 (cierre-modulo paso 3.6): sella el DATA SAFETY GATE.
            # Determinista del payload, mismo idioma que record_harvest — sin
            # diff_hash porque un backup no se invalida por un commit de
            # código posterior, se invalida por una NUEVA escritura
            # destructiva (fuera del alcance mecánico de un diff de git).
            module_id = payload.get("module_id")
            if not isinstance(module_id, str) or not module_id.strip():
                raise ValueError("record_data_safety_check requiere 'module_id' (string no vacío)")
            module_id = module_id.strip()
            target_env = payload.get("target_env")
            if target_env not in {"dev", "staging", "prod"}:
                raise ValueError("record_data_safety_check: 'target_env' debe ser dev|staging|prod")
            backup_verified = payload.get("backup_verified")
            if not isinstance(backup_verified, bool):
                raise ValueError("record_data_safety_check: 'backup_verified' debe ser boolean")
            evidence = str(payload.get("evidence") or "").strip()
            if len(evidence) < 20:
                raise ValueError(
                    "record_data_safety_check requiere 'evidence' de >= 20 caracteres "
                    "(procedimiento de backup + dónde se verificó la restauración)"
                )
            entry = self.state_data.get("work_tiers", {}).get(module_id)
            if not isinstance(entry, dict):
                raise ValueError(
                    f"record_data_safety_check: '{module_id}' no tiene work_tiers — "
                    "clasifica el tier primero (add_work_tier)"
                )
            entry["data_safety"] = {
                "target_env": target_env,
                "backup_verified": backup_verified,
                "evidence": evidence,
                "recorded_by": payload.get("recorded_by", "builder"),
                "recorded_at": now_iso,
            }
            veredicto = "verificado" if backup_verified else "NO verificado"
            return f"Data safety check registrado para '{module_id}': backup {veredicto} ({target_env})."

        elif action == "record_checkpoint":
            # FW-134: sella una obligación endurecida en prosa (FW-131 barrido
            # de sub-dominios, FW-132 auditoría de experto). Sin diff_hash,
            # mismo idioma que record_harvest — determinista del payload, sin
            # rama de replay especial.
            import workflow_receipts as _wr
            receipt = _wr.record_checkpoint(
                self.state_data,
                payload.get("module", ""),
                kind=payload.get("kind", ""),
                scope_id=payload.get("scope_id", ""),
                evidence=payload.get("evidence", ""),
                reference=payload.get("reference", ""),
                recorded_by=payload.get("recorded_by", "agent"),
                now_iso=now_iso,
            )
            return f"Checkpoint '{receipt['kind']}' registrado para '{receipt['scope_id']}'."

        elif action == "register_decision":
            title = payload.get("title")
            context = payload.get("context")
            decision = payload.get("decision")
            alt = payload.get("alternatives", "")
            cons = payload.get("consequences", "")
            mod_id = payload.get("module_id", "framework")
            if not title or not context or not decision:
                raise ValueError("title, context y decision requeridos")
            # TL FW-019 / BOTÓN FW-115/FW-113: un ADR "paraguas" puede cubrir
            # varios módulos de una misma iniciativa (module_id como lista),
            # además del string único de siempre o "framework". Sin esto,
            # una iniciativa multi-módulo obligaba a crear un ADR casi
            # duplicado por módulo solo para que record_approval_receipt
            # (tier_2) aceptara la cita.
            if isinstance(mod_id, list):
                if not mod_id or not all(isinstance(m, str) and m.strip() for m in mod_id):
                    raise ValueError(
                        "module_id como lista requiere al menos un módulo, "
                        "todos strings no vacíos"
                    )
                mod_id = [m.strip() for m in mod_id]
            elif not isinstance(mod_id, str) or not mod_id.strip():
                raise ValueError("module_id debe ser un string no vacío o una lista de módulos")
            decisions = self.state_data["_decisions"]
            max_num = 0
            for d in decisions:
                if d["id"].startswith("ADR-"):
                    try: max_num = max(max_num, int(d["id"].split("-")[1]))
                    except (IndexError, ValueError): pass
            adr_id = f"ADR-{max_num + 1:03d}"
            # FW-125: el id se autoasigna y `payload["id"]` se ignoraba EN
            # SILENCIO — la acción devolvía éxito con un id distinto del
            # pedido, así que un agente que no comprobara el resultado creía
            # haber actualizado una ADR y en realidad había creado otra.
            # Ronda de revisión final (hallazgo 1): rechazar solo cuando el id
            # pedido es INCONSISTENTE con el que se habría autoasignado —
            # rechazar cualquier id presente rompía el replay estricto de
            # `state_log.jsonl` históricos que ya traían el id correcto (el
            # mismo que `next_id` habría calculado de todos modos).
            # FW-186: en replay el id LOGUEADO es la verdad. El contador puede
            # ir por detrás si la bitácora se sembró por migración incompleta
            # (FW-173) y no re-emitió eventos para el backlog de ADRs previo.
            logged_id = payload.get("id")
            if (
                getattr(self, "_is_replay", False)
                and isinstance(logged_id, str)
                and re.fullmatch(r"ADR-\d+", logged_id)
            ):
                adr_id = logged_id
            elif "id" in payload and payload["id"] != adr_id:
                raise ValueError(
                    "register_decision no acepta 'id' distinto del que se "
                    f"autoasignaría ({adr_id}): se autoasigna en orden. "
                    "Para reemplazar una ADR vigente usa supersede_decision "
                    "(marca la vieja como Superseded y apunta a la nueva)."
                )
            entry = {
                "id": adr_id, "title": title, "date": now_iso.split("T")[0],
                "status": "Aceptada", "module_id": mod_id, "context": context,
                "decision": decision, "alternatives": alt, "consequences": cons
            }
            # 4.1.0 (FW-072): trazabilidad de expertise — la decisión cita la
            # skill core o el experto de dominio que la fundó.
            expertise = payload.get("expertise")
            if expertise:
                entry["expertise"] = expertise if isinstance(expertise, str) else ", ".join(expertise)
            if payload.get("documents_decision"):
                entry["documents_decision"] = str(payload["documents_decision"]).strip()
            decisions.append(entry)
            self.state_data["last_completed_step"] = f"Registrada decisión {adr_id}."
            return f"Decisión {adr_id} registrada."

        elif action == "supersede_decision":
            # FW-125: sin esta acción la bitácora acumulaba ADRs "Aceptadas"
            # contradictorias entre sí y el lector no tenía forma de saber cuál
            # manda salvo leerlas todas y cruzarlas con el código.
            adr_id = payload.get("id")
            reemplazo = payload.get("superseded_by")
            reason = payload.get("reason")
            if not isinstance(adr_id, str) or not adr_id.strip():
                raise ValueError("supersede_decision requiere 'id' (string no vacío)")
            if not isinstance(reemplazo, str) or not reemplazo.strip():
                raise ValueError("supersede_decision requiere 'superseded_by' (string no vacío)")
            adr_id, reemplazo = adr_id.strip(), reemplazo.strip()
            if adr_id == reemplazo:
                raise ValueError("supersede_decision: una ADR no puede superseder a sí misma")
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise ValueError(
                    "supersede_decision requiere 'reason' de al menos 20 caracteres "
                    "explicando qué cambió respecto de la decisión anterior"
                )
            reason = self._validate_audit_text("reason", reason)
            decisions = self.state_data["_decisions"]
            objetivo = next((d for d in decisions if d["id"] == adr_id), None)
            # FW-186: en replay, si la ADR objetivo o su reemplazo no están en
            # la reconstrucción (bitácora sembrada por migración incompleta,
            # FW-173), no se puede marcar Superseded lo que no se reconstruyó —
            # se omite el evento sin abortar la recuperación.
            if not objetivo:
                if getattr(self, "_is_replay", False):
                    return f"supersede_decision omitido en replay: {adr_id} no está en la reconstrucción."
                raise ValueError(f"supersede_decision: {adr_id} no existe")
            if not any(d["id"] == reemplazo for d in decisions):
                if getattr(self, "_is_replay", False):
                    return f"supersede_decision omitido en replay: {reemplazo} no está en la reconstrucción."
                raise ValueError(
                    f"supersede_decision: {reemplazo} no existe; regístrala con "
                    "register_decision antes de supersedir la anterior"
                )
            if objetivo.get("status") == "Superseded":
                raise ValueError(
                    f"supersede_decision: {adr_id} ya está superseded por "
                    f"{objetivo.get('superseded_by')}"
                )
            objetivo["status"] = "Superseded"
            objetivo["superseded_by"] = reemplazo
            objetivo["superseded_at"] = now_iso
            objetivo["supersede_reason"] = reason.strip()
            self.state_data["last_completed_step"] = f"{adr_id} superseded por {reemplazo}."
            return f"{adr_id} marcada como Superseded por {reemplazo}."

        elif action == "set_codex_execution_pointer":
            # Compatibilidad estrictamente histórica. El action fue retirado
            # del catálogo público; solo replay_state_log puede reproducirlo.
            if not getattr(self, "_is_replay", False):
                raise ValueError(
                    "LEGACY_MIGRATION_REQUIRED: set_codex_execution_pointer solo "
                    "se acepta durante replay historico"
                )
            from codex_budget_legacy import apply_historical_pointer
            return apply_historical_pointer(self.state_data, payload)

        elif action == "retire_codex_budget":
            if not (
                getattr(self, "_migration_mode", False)
                or getattr(self, "_is_replay", False)
            ):
                raise ValueError("retire_codex_budget solo puede ejecutarse mediante migracion")
            migration_id = payload.get("migration_id")
            expected_fingerprint = payload.get("expected_fingerprint")
            expected_pointer = payload.get("expected_pointer")
            if not isinstance(migration_id, str) or not migration_id.strip():
                raise ValueError("retire_codex_budget requiere migration_id")
            if not isinstance(expected_fingerprint, str) or not re.fullmatch(
                r"[0-9a-f]{64}", expected_fingerprint
            ):
                raise ValueError("retire_codex_budget requiere expected_fingerprint")
            if not isinstance(payload.get("next_action"), str) or not isinstance(
                payload.get("next_command"), str
            ):
                raise ValueError("retire_codex_budget requiere continuidad textual")
            session = self.state_data.setdefault("session", {})
            current_pointer = session.get("codex_execution")
            if current_pointer != expected_pointer:
                raise ValueError("retire_codex_budget: precondicion de puntero fallida")
            if not getattr(self, "_is_replay", False):
                if _state_fingerprint(self.state_data) != expected_fingerprint:
                    raise ValueError("retire_codex_budget: fingerprint stale")
                _verify_migration_archive(
                    self,
                    payload.get("archive_path"),
                    payload.get("archive_sha256"),
                )
            migrations = self.state_data.setdefault("framework_migrations", {})
            if migration_id in migrations:
                raise ValueError(f"retire_codex_budget: migracion duplicada {migration_id}")
            session.pop("codex_execution", None)
            session["next_action"] = payload["next_action"]
            session["next_command"] = payload["next_command"]
            migrations[migration_id] = {
                "type": "codex_budget_retirement",
                "status": "applied",
                "archive_path": payload["archive_path"],
                "archive_sha256": payload["archive_sha256"],
                "expected_fingerprint": expected_fingerprint,
                "applied_at": now_iso,
            }
            return f"Presupuesto Codex retirado; evidencia archivada en {payload['archive_path']}."

        elif action == "restore_codex_budget":
            if not (
                getattr(self, "_migration_mode", False)
                or getattr(self, "_is_replay", False)
            ):
                raise ValueError("restore_codex_budget solo puede ejecutarse mediante migracion")
            migration_id = payload.get("migration_id")
            if not isinstance(migration_id, str) or not migration_id.strip():
                raise ValueError("restore_codex_budget requiere migration_id")
            migrations = self.state_data.setdefault("framework_migrations", {})
            entry = migrations.get(migration_id)
            if not isinstance(entry, dict) or entry.get("status") != "applied":
                raise ValueError("restore_codex_budget requiere una migracion aplicada")
            if not getattr(self, "_is_replay", False):
                expected = payload.get("expected_fingerprint")
                if not isinstance(expected, str) or _state_fingerprint(self.state_data) != expected:
                    raise ValueError("restore_codex_budget: fingerprint stale")
                _verify_migration_archive(
                    self,
                    payload.get("archive_path"),
                    payload.get("archive_sha256"),
                )
            pointer = payload.get("pointer")
            from codex_budget_legacy import validate_pointer as _validate_legacy_pointer
            _validate_legacy_pointer(pointer)
            session_before = payload.get("session_before")
            if not isinstance(session_before, dict):
                raise ValueError("restore_codex_budget requiere session_before")
            if session_before.get("codex_execution") != pointer:
                raise ValueError("restore_codex_budget: session_before no coincide con pointer")
            self.state_data["session"] = copy.deepcopy(session_before)
            entry["status"] = "reverted"
            entry["reverted_at"] = now_iso
            return f"Presupuesto Codex restaurado desde {payload['archive_path']}."

        elif action == "grandfather_checkpoint_gate":
            # FW-221: FW-134 (4.37.0) exige checkpoints domain_sweep/expert_audit
            # en todo módulo tier_2 completado, pero ningún proyecto adoptante
            # tuvo ese paso disponible antes de llegar a 4.37.0 — exigirlo
            # retroactivamente bloquea el upgrade de los 9 adoptantes reales sin
            # que exista evidencia honesta que producir (el barrido/auditoría que
            # la generaría nunca pudo ejecutarse). Solo el migrador de
            # compatibilidad invoca esto, y solo para módulos completados bajo
            # una versión anterior a la que introdujo el gate.
            from_version = payload.get("from_version")
            if not isinstance(from_version, str) or not from_version.strip():
                raise ValueError("grandfather_checkpoint_gate requiere 'from_version'")
            if _version_tuple(from_version) >= _version_tuple("4.37.0"):
                raise ValueError(
                    "grandfather_checkpoint_gate: from_version >= 4.37.0 no es elegible — "
                    "ese proyecto ya tenía el gate FW-134 disponible al completar esos módulos"
                )
            modules = payload.get("modules")
            if not isinstance(modules, list) or not modules or any(
                not isinstance(m, str) or not m.strip() for m in modules
            ):
                raise ValueError("grandfather_checkpoint_gate requiere 'modules' (lista de IDs no vacía)")
            reason = (payload.get("reason") or "").strip()
            if not reason:
                raise ValueError("grandfather_checkpoint_gate requiere 'reason'")
            completed = self.state_data.get("modules", {}).get("completed", [])
            faltantes = [m for m in modules if m not in completed]
            if faltantes:
                raise ValueError(
                    f"grandfather_checkpoint_gate: {faltantes} no están en modules.completed"
                )
            import workflow_receipts as _wr
            sealed = _wr.grandfather_checkpoints(
                self.state_data,
                modules=modules,
                from_version=from_version,
                reason=reason,
                now_iso=now_iso,
            )
            self.state_data["last_completed_step"] = (
                f"FW-221: checkpoints FW-134 grandfathered para {len(modules)} módulo(s) "
                f"tier_2 completados en {from_version}."
            )
            return (
                f"Checkpoints grandfathered: {', '.join(sealed)}"
                if sealed else "grandfather_checkpoint_gate: sin cambios (ya sellados)"
            )

        elif action == "set_session_pointer":
            sess = self.state_data.setdefault("session", {})
            for k in ["next_action", "next_command", "active_module_description",
                      "next_module", "next_module_description"]:
                if k in payload and payload[k] is not None:
                    sess[k] = payload[k]
            # N10 (FW-264, flac friction_buffer: cierre-no-reordena-modulo-activo):
            # si next_module está en modules.in_progress, moverlo a la posición 0.
            # Replay sigue tolerante (FW-186 / D7).
            next_mod = payload.get("next_module")
            if isinstance(next_mod, str) and next_mod.strip():
                next_mod_id = next_mod.strip()
                in_prog = self.state_data.get("modules", {}).get("in_progress", [])
                if isinstance(in_prog, list) and next_mod_id in in_prog:
                    in_prog.remove(next_mod_id)
                    in_prog.insert(0, next_mod_id)
            # FW-095 (Research App FW-043, reincidencia 2026-07-11): la
            # descripción del módulo activo se sella con el módulo al que
            # pertenece (in_progress[0] al momento de la mutación). El render
            # solo la muestra si sigue correspondiendo al módulo activo real;
            # para describir el próximo módulo existe `next_module_description`.
            msg = "Punteros de sesión actualizados."
            if payload.get("active_module_description") is not None:
                in_prog = self.state_data.get("modules", {}).get("in_progress", [])
                described = in_prog[0] if in_prog else None
                sess["described_module"] = described
                if described is None:
                    msg += (" AVISO: no hay módulo activo — si la descripción es del"
                            " próximo módulo, usa 'next_module_description'.")
                elif self.state_data.get("module_statuses", {}).get(described) == "blocked":
                    msg += (f" AVISO: el módulo activo '{described}' está blocked — si la"
                            " descripción es del próximo módulo, usa 'next_module_description'.")
                if described is not None and not getattr(self, "_is_replay", False):
                    # L8: aviso (nunca rechazo) si el scope declara otro módulo que el descrito.
                    # D10: el aviso nuevo solo lo ve quien llama; no entra en el log.
                    self._post_log_notice = self._scope_module_hint(described)
            return msg

        elif action == "classify_module_spec":
            mod_id = payload.get("id")
            dm = payload.get("data_model")
            ac = payload.get("api_contract")
            # FW-278 (TL FW-257, variante de cierre): reclasificar sin
            # `justification` (p. ej. required → done al cerrar) reconstruía la
            # entrada sin ella, cambiaba la base `module_spec` y dejaba stale el
            # APPROVE_RECEIPT. Se conserva la anterior y se escribe en el
            # payload para que el log (y el replay) lleven el valor efectivo.
            if not payload.get("justification") and not getattr(self, "_is_replay", False):
                prev_spec = self.state_data.get("module_specs", {}).get(mod_id)
                if isinstance(prev_spec, dict) and prev_spec.get("justification"):
                    payload["justification"] = prev_spec["justification"]
            justification = payload.get("justification", "")
            classified_by = payload.get("classified_by")
            # FW-111/FW-102/GW-045: el ValueError original no listaba los
            # valores válidos — cada fallo solo revelaba "inválido", sin decir
            # contra qué. Interpolar el contrato completo (mismo patrón que
            # add_closure_rejection ya usa para 'reason').
            valid_spec_values = ["required", "n/a", "done"]
            if not mod_id or dm not in valid_spec_values or ac not in valid_spec_values:
                raise ValueError(
                    "classify_module_spec requiere id, data_model y api_contract "
                    f"válidos (data_model/api_contract: uno de {valid_spec_values}); "
                    f"recibido data_model={dm!r}, api_contract={ac!r}"
                )
            valid_classified_by = ["scope-siguiente", "enriquecer-ticket", "migrar-proyecto", "manual"]
            if classified_by not in valid_classified_by:
                raise ValueError(
                    f"classified_by inválido: {classified_by!r} (uno de {valid_classified_by})"
                )
            if (dm == "n/a" or ac == "n/a") and not justification:
                raise ValueError("justification obligatoria cuando data_model o api_contract es 'n/a'")
            # FW-148: tercera spec, opcional para no romper llamadas existentes.
            ir = payload.get("integration_real")
            if ir is not None and ir not in valid_spec_values:
                raise ValueError(
                    "integration_real inválido: "
                    f"{ir!r} (uno de {valid_spec_values}, u omitir)"
                )
            if ir == "n/a" and not justification:
                raise ValueError("justification obligatoria cuando integration_real es 'n/a'")
            entry = {
                "data_model": dm, "api_contract": ac,
                "classified_at": now_iso, "classified_by": classified_by,
            }
            if ir is not None:
                entry["integration_real"] = ir
            if justification:
                entry["justification"] = justification
            existing_spec = self.state_data.get("module_specs", {}).get(mod_id)
            if isinstance(existing_spec, dict) and existing_spec.get("stabilization"):
                entry["stabilization"] = True
            self.state_data.setdefault("module_specs", {})[mod_id] = entry
            return f"module_specs para '{mod_id}' clasificado."

        elif action == "add_closure_rejection":
            mod_id = payload.get("module_id")
            reason = payload.get("reason")
            if not mod_id or reason not in VALID_CLOSURE_REASONS:
                raise ValueError(f"add_closure_rejection requiere module_id y reason válido (uno de {sorted(VALID_CLOSURE_REASONS)})")
            if self._is_dropped_module(self.state_data, mod_id):
                # PA-FW-037 (re-revisión 5.3.3): un rechazo activo sobre un dropped
                # contaría para la Regla 7 y nunca se resolvería
                # (resolve_closure_rejections exige ready_for_validation).
                raise ValueError(
                    f"add_closure_rejection: el módulo '{mod_id}' está dropped (abandonado, "
                    "terminal); su rechazo de cierre no se podría resolver con "
                    "resolve_closure_rejections (exige ready_for_validation). Regístralo "
                    "en el módulo abierto que absorbió su trabajo"
                )
            self.state_data.setdefault("closure_rejections", []).append({
                "module_id": mod_id, "reason": reason, "timestamp": now_iso,
            })
            return f"closure_rejection '{reason}' registrado para '{mod_id}'."

        elif action == "resolve_closure_rejections":
            module_id = payload.get("module_id")
            resolution = payload.get("resolution")
            evidence = payload.get("evidence")
            if not isinstance(module_id, str) or not module_id.strip():
                raise ValueError("resolve_closure_rejections requiere 'module_id' no vacío")
            if not isinstance(resolution, str) or not resolution.strip():
                raise ValueError("resolve_closure_rejections requiere 'resolution' no vacío")
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValueError("resolve_closure_rejections requiere 'evidence' no vacío")
            module_id = module_id.strip()
            if not self._is_known_module(self.state_data, module_id):
                raise ValueError(f"resolve_closure_rejections: módulo desconocido '{module_id}'")
            if self.state_data.get("module_statuses", {}).get(module_id) != "ready_for_validation":
                raise ValueError(
                    f"resolve_closure_rejections: '{module_id}' debe estar en ready_for_validation"
                )
            active = [
                rejection
                for rejection in self.state_data.get("closure_rejections", [])
                if isinstance(rejection, dict)
                and rejection.get("module_id") == module_id
                and self._is_active_closure_rejection(rejection)
            ]
            if not active:
                raise ValueError(
                    f"resolve_closure_rejections: no hay rechazos activos para '{module_id}'"
                )
            expected_ids = payload.get("resolved_ids")
            if expected_ids is not None:
                if (
                    not isinstance(expected_ids, list)
                    or not all(isinstance(identifier, str) and identifier.strip() for identifier in expected_ids)
                    or len(set(expected_ids)) != len(expected_ids)
                ):
                    raise ValueError("resolve_closure_rejections.resolved_ids inválido o duplicado")
                active_ids = [rejection.get("id") for rejection in active]
                if set(active_ids) != set(expected_ids):
                    raise ValueError(
                        "resolve_closure_rejections.resolved_ids no coincide con los rechazos activos"
                    )
            for rejection in active:
                rejection.setdefault("id", self._next_closure_rejection_id(self.state_data))
                rejection["resolved_at"] = now_iso
                rejection["resolution"] = self._validate_audit_text("resolution", resolution)
                rejection["evidence"] = self._validate_audit_text("evidence", evidence)
                rejection["resolution_event"] = {
                    "action": "resolve_closure_rejections",
                    "timestamp": now_iso,
                    "id": rejection["id"],
                }
                # A second resolution after a rollback creates a fresh active
                # resolution while the rollback reason remains in the log.
                rejection.pop("restored_at", None)
                rejection.pop("restore_reason", None)
            return f"{len(active)} rechazos de cierre resueltos para '{module_id}'."

        elif action == "backfill_closure_rejection_provenance":
            module_id = payload.get("module_id")
            resolved_ids = payload.get("resolved_ids")
            resolution_event_ts = payload.get("resolution_event_ts")
            allowed_fields = {
                "module_id", "resolved_ids", "resolution_event_ts", "legacy_refs"
            }
            if getattr(self, "_is_replay", False):
                allowed_fields.add("provenance_ids")
            unexpected_fields = set(payload) - allowed_fields
            if unexpected_fields:
                raise ValueError(
                    "backfill_closure_rejection_provenance contiene campos no permitidos"
                )
            if getattr(self, "_is_replay", False):
                provenance_ids = payload.get("provenance_ids")
                if (
                    not isinstance(provenance_ids, list)
                    or not all(isinstance(identifier, str) for identifier in provenance_ids)
                    or set(provenance_ids) != set(resolved_ids or [])
                ):
                    raise ValueError(
                        "backfill_closure_rejection_provenance.provenance_ids no coincide"
                    )
            if not isinstance(module_id, str) or not module_id.strip():
                raise ValueError("backfill_closure_rejection_provenance requiere 'module_id' no vacío")
            if (
                not isinstance(resolved_ids, list)
                or not resolved_ids
                or not all(isinstance(identifier, str) and identifier.strip() for identifier in resolved_ids)
                or len(set(resolved_ids)) != len(resolved_ids)
            ):
                raise ValueError("backfill_closure_rejection_provenance requiere IDs únicos")
            if not isinstance(resolution_event_ts, str) or not resolution_event_ts.strip():
                raise ValueError("backfill_closure_rejection_provenance requiere resolution_event_ts")
            module_id = module_id.strip()
            if not self._is_known_module(self.state_data, module_id):
                raise ValueError(f"backfill_closure_rejection_provenance: módulo desconocido '{module_id}'")
            legacy_refs = payload.get("legacy_refs")
            if getattr(self, "_is_replay", False) and legacy_refs is None:
                # The first historical event predated the strict payload and
                # cannot prove provenance by itself: old add events may be
                # outside the hash chain. Replay accepts it as a no-op and
                # waits for a later explicit backfill event carrying exact
                # legacy_refs. This keeps recovery compatible while remaining
                # fail-closed if that sealing event is absent.
                return (
                    "Backfill histórico sin legacy_refs diferido: "
                    "requiere un evento posterior con referencias explícitas."
                )
            if (
                not isinstance(legacy_refs, dict)
                or set(legacy_refs) != set(resolved_ids)
                or any(
                    not isinstance(ref, dict)
                    or not isinstance(ref.get("timestamp"), str)
                    or not isinstance(ref.get("reason"), str)
                    or not ref["timestamp"].strip()
                    or not ref["reason"].strip()
                    or ref["reason"] not in VALID_CLOSURE_REASONS
                    for ref in legacy_refs.values()
                )
            ):
                raise ValueError(
                    "backfill_closure_rejection_provenance requiere legacy_refs completos por ID"
                )
            self._validate_audit_text("resolution_event_ts", resolution_event_ts)
            for identifier, reference in legacy_refs.items():
                if len(identifier) > 128:
                    raise ValueError("backfill_closure_rejection_provenance: ID demasiado largo")
                if set(reference) != {"timestamp", "reason"}:
                    raise ValueError(
                        "backfill_closure_rejection_provenance: legacy_ref contiene campos no permitidos"
                    )
                self._validate_audit_text(
                    f"legacy_refs[{identifier}].timestamp", reference["timestamp"]
                )
                self._validate_audit_text(
                    f"legacy_refs[{identifier}].reason", reference["reason"]
                )
            entries = [
                rejection
                for rejection in self.state_data.get("closure_rejections", [])
                if isinstance(rejection, dict)
                and rejection.get("module_id") == module_id
                and rejection.get("id") in resolved_ids
                and rejection.get("resolved_at") == resolution_event_ts
                and not rejection.get("restored_at")
            ]
            if {entry.get("id") for entry in entries} != set(resolved_ids):
                raise ValueError(
                    "backfill_closure_rejection_provenance: IDs no coinciden con resoluciones activas"
                )
            import validate_closure as _vc
            identities = _vc.legacy_rejection_identity_map(self._workspace_root())
            if not _vc.legacy_resolution_event_exists(
                self._workspace_root(), module_id, resolution_event_ts
            ):
                raise ValueError(
                    "backfill_closure_rejection_provenance: no existe evento legacy respaldado"
                )
            for entry in entries:
                reference = legacy_refs[entry["id"]]
                key = (module_id, reference["timestamp"], reference["reason"])
                if (
                    entry.get("timestamp") != reference["timestamp"]
                    or entry.get("reason") != reference["reason"]
                    or identities.get(key) != entry["id"]
                ):
                    raise ValueError(
                        "backfill_closure_rejection_provenance: identidad legacy no coincide con el log"
                    )
                entry["resolution_event"] = {
                    "action": "resolve_closure_rejections",
                    "timestamp": resolution_event_ts,
                    "id": entry["id"],
                }
            return f"Provenance de resolución completada para {len(entries)} rechazos de '{module_id}'."

        elif action == "restore_closure_rejections":
            module_id = payload.get("module_id")
            archive_ids = payload.get("archive_ids")
            reason = payload.get("reason")
            if not isinstance(module_id, str) or not module_id.strip():
                raise ValueError("restore_closure_rejections requiere 'module_id' no vacío")
            if not isinstance(archive_ids, list) or not archive_ids:
                raise ValueError("restore_closure_rejections requiere 'archive_ids' no vacío")
            if not all(isinstance(identifier, str) and identifier.strip() for identifier in archive_ids):
                raise ValueError("restore_closure_rejections.archive_ids debe ser lista de strings no vacíos")
            if len(set(archive_ids)) != len(archive_ids):
                raise ValueError("restore_closure_rejections.archive_ids no puede contener duplicados")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("restore_closure_rejections requiere 'reason' no vacío")
            reason = self._validate_audit_text("reason", reason)
            module_id = module_id.strip()
            if not self._is_known_module(self.state_data, module_id):
                raise ValueError(f"restore_closure_rejections: módulo desconocido '{module_id}'")
            if self.state_data.get("module_statuses", {}).get(module_id) == "completed":
                raise ValueError(
                    f"restore_closure_rejections: '{module_id}' ya está completed"
                )
            if self._is_dropped_module(self.state_data, module_id):
                # PA-FW-037 (revisión 5.3.3): reactivarlos los dejaría contando
                # para la Regla 7 sin vía de resolución (dropped es terminal).
                raise ValueError(
                    f"restore_closure_rejections: '{module_id}' está dropped (abandonado, "
                    "terminal); sus rechazos quedan archivados como histórico"
                )
            archive = self.state_data.get("closure_rejection_archive", [])
            archive_seen: set[str] = set()
            duplicate_archive_ids: set[str] = set()
            for entry in archive:
                identifier = entry.get("id") if isinstance(entry, dict) else None
                if not isinstance(identifier, str):
                    continue
                if identifier in archive_seen:
                    duplicate_archive_ids.add(identifier)
                archive_seen.add(identifier)
            if duplicate_archive_ids:
                raise ValueError(
                    f"restore_closure_rejections: IDs duplicados en archivo: {sorted(duplicate_archive_ids)}"
                )
            expected_ids = payload.get("restored_ids")
            if expected_ids is not None and set(expected_ids) != set(archive_ids):
                raise ValueError(
                    "restore_closure_rejections.restored_ids no coincide con archive_ids"
                )
            by_id = {
                entry.get("id"): entry
                for entry in archive
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)
            }
            missing = [identifier for identifier in archive_ids if identifier not in by_id]
            if missing:
                raise ValueError(
                    f"restore_closure_rejections: archive_ids desconocidos: {missing}"
                )
            foreign = [
                identifier
                for identifier in archive_ids
                if by_id[identifier].get("module_id") != module_id
            ]
            if foreign:
                raise ValueError(
                    f"restore_closure_rejections: archive_ids de otro módulo: {foreign}"
                )
            active_ids = {
                rejection.get("id")
                for rejection in self.state_data.get("closure_rejections", [])
                if isinstance(rejection, dict)
            }
            if active_ids.intersection(archive_ids):
                raise ValueError(
                    f"restore_closure_rejections: entradas ya activas: {sorted(active_ids.intersection(archive_ids))}"
                )
            archive_id_set = set(archive_ids)
            remaining_archive = [
                entry
                for entry in archive
                if not (isinstance(entry, dict) and entry.get("id") in archive_id_set)
            ]
            restored_entries = []
            for identifier in archive_ids:
                restored = copy.deepcopy(by_id[identifier])
                restored["restored_at"] = now_iso
                restored["restore_reason"] = reason
                restored_entries.append(restored)
            self.state_data["closure_rejection_archive"] = remaining_archive
            self.state_data.setdefault("closure_rejections", []).extend(restored_entries)
            return f"{len(archive_ids)} rechazos de cierre restaurados para '{module_id}'."

        elif action == "set_phase":
            phase = payload.get("current_phase")
            if not phase or not re.match(r"^phase_[0-9]{2}$", phase):
                raise ValueError("current_phase debe tener formato 'phase_XX'")
            self.state_data["current_phase"] = phase
            if payload.get("current_step") is not None:
                self.state_data["current_step"] = payload["current_step"]
            return f"Fase actualizada a {phase}."

        elif action in {"set_lifecycle", "open_change", "close_change", "escalate_change", "confirm_builder_use"}:
            return self._apply_maintenance_action(action, payload, now_iso)

        elif action == "set_execution_profile":
            tier = payload.get("capability_tier")
            if tier not in ["strong", "standard", "limited"]:
                raise ValueError("capability_tier debe ser strong|standard|limited")
            ep = {"capability_tier": tier, "declared_at": now_iso}
            for k in ["model", "rationale"]:
                if payload.get(k):
                    ep[k] = payload[k]
            self.state_data["execution_profile"] = ep
            return f"execution_profile fijado a '{tier}'."

        elif action == "add_active_mcp":
            name = payload.get("name")
            if not name:
                raise ValueError("add_active_mcp requiere 'name'")
            mcps = self.state_data.setdefault("active_mcps", [])
            if name not in mcps:
                mcps.append(name)
            return f"MCP '{name}' marcado como activo."

        elif action == "complete_module":
            # C1 (triaje 2026-07-09, Regla 6): ÚNICO camino del motor a
            # 'completed'. Ejecuta los gates deterministas de cierre DENTRO del
            # motor (antes vivían solo en la prosa de /cierre-modulo):
            # tier, specs, muerte por rechazos, capacidad del ejecutor,
            # clarificaciones bloqueantes (closure_gate) + cobertura de ACs.
            mod_id = payload.get("id")
            if not isinstance(mod_id, str) or not mod_id.strip():
                raise ValueError("complete_module requiere 'id' (string no vacío)")
            mod_id = mod_id.strip()
            # Durante un replay la historia se REPRODUCE, no se vuelve a juzgar:
            # los gates de abajo miran el workspace ACTUAL (diff hash, ACs en
            # disco), así que re-ejecutarlos sobre un cierre de hace semanas lo
            # evalúa con un contexto que ya no es el suyo. El evento existe en
            # el log precisamente porque pasó los gates cuando ocurrió. Sin
            # esto, el replay estricto abortaba en 5 de 7 proyectos reales y la
            # vía de auditoría quedaba inservible (sabotaje controlado
            # 2026-07-24). Mismo patrón que los receipts de replay más arriba.
            if getattr(self, "_is_replay", False):
                # El sello de autoría viaja en el payload logueado (mismo idioma
                # que `_receipt` en los recibos): sin esto, un estado
                # reconstruido desde la bitácora perdía `closed_by` y divergía en
                # silencio del estado vivo — justo la clase de deriva que
                # `audit_state_backing` existe para no tener.
                _sello = payload.get("_closed_by")
                if isinstance(_sello, dict):
                    _entrada = self.state_data.get("work_tiers", {}).get(mod_id)
                    if isinstance(_entrada, dict):
                        _entrada["closed_by"] = copy.deepcopy(_sello)
                return self._apply_event(
                    "update_module_status", {"id": mod_id, "status": "completed"}, now_iso
                )
            prev = self.state_data.get("module_statuses", {}).get(mod_id)
            razones: list[str] = []
            if prev != "ready_for_validation":
                razones.append(
                    f"estado actual '{prev}' — debe estar en ready_for_validation "
                    "(el paso 0 de /cierre-modulo aplica esa transición)"
                )
            import validate_closure as _vc
            import workflow_receipts as _wr
            _diff_hash_actual = _wr.workspace_diff_hash(WORKSPACE_ROOT)
            razones.extend(
                _vc.closure_gate(
                    self.state_data, mod_id,
                    current_diff_hash=_diff_hash_actual,
                    workspace_root=WORKSPACE_ROOT,
                )
            )
            # Archivar en el cierre solo tiene sentido si el cierre EXIGE que no
            # queden rechazos activos; sin este gate, `complete_module` los
            # dejaría vivos y cerraría igual, que es el agujero que FW-175 vino
            # a tapar (FW-182).
            active_rejections = [
                rejection
                for rejection in self.state_data.get("closure_rejections", [])
                if isinstance(rejection, dict)
                and rejection.get("module_id") == mod_id
                and self._is_active_closure_rejection(rejection)
            ]
            if active_rejections:
                razones.append(
                    f"quedan {len(active_rejections)} closure_rejections activos para "
                    f"'{mod_id}' — resolverlos antes de completar"
                )
            # FW-144: EJECUTABLE GATE con receipt. Era el último gate del cierre
            # que vivía solo en la prosa del paso 3.8 — el agente declaraba
            # `verificacion: ok_automatizada` y el motor lo daba por hecho.
            razones.extend(
                _wr.execution_problems(self.state_data, mod_id, _diff_hash_actual)
            )
            # FW-145: STUB GATE. El evidence gate comprueba que el fichero
            # citado exista; esto comprueba que no sea un decorado.
            # La primera versión hacía `except ImportError: pass` — un gate
            # contra el fail-open que fallaba abierto: borrado o a medio instalar
            # el script, el gate desaparecía en silencio y el cierre continuaba.
            # Detectado borrando el fichero a propósito (el doctor seguía en OK).
            # Coherente con el invariante fail-closed de FW-146: la ausencia del
            # gate es una razón de bloqueo accionable, no un traceback ni un
            # salto mudo.
            try:
                import validate_no_stubs as _vns
                razones.extend(_vns.stub_problems(mod_id, WORKSPACE_ROOT))
            except ImportError as _exc:
                razones.append(
                    "STUB GATE no disponible: no se pudo importar "
                    f".agent/scripts/validate_no_stubs.py ({_exc}). Restaurarlo "
                    "desde la plantilla — un gate ausente no es un gate que pasa "
                    "(FW-145/FW-146)"
                )
            # Specs 'required' no se auto-completan: bloquean (antes la acción
            # genérica las volteaba a 'done' en silencio).
            specs = self.state_data.get("module_specs", {}).get(mod_id, {})
            for key in ("data_model", "api_contract", "integration_real"):
                if specs.get(key) == "required":
                    razones.append(
                        f"module_specs.{key} sigue 'required' — completar la spec "
                        "y marcarla 'done' antes de cerrar (no se auto-completa)"
                    )
            # Respaldo en la bitácora: un receipt puede escribirse a mano en
            # 03_project_state.json, y tanto el doctor como el pre-commit lo
            # darían por bueno — ambos leen el estado, y el estado ya afirma
            # que la revisión ocurrió. state_log.jsonl es la única fuente que
            # sabe si pasó de verdad (sabotaje controlado 2026-07-24).
            try:
                import replay_state_log as _rsl
                razones.extend(
                    _rsl.unbacked_module_receipts(
                        self.state_data, mod_id, WORKSPACE_ROOT / "_project" / "state_log.jsonl"
                    )
                )
            except ImportError:
                pass  # instalación parcial: el resto de gates sigue aplicando
            # Cobertura de ACs: falla cerrado si el archivo no existe.
            acceptance = WORKSPACE_ROOT / "_project" / "08_module_acceptance.md"
            if not acceptance.exists():
                razones.append(
                    "falta _project/08_module_acceptance.md — sin archivo de ACs "
                    "no hay evidencia de cobertura (fail closed)"
                )
            else:
                texto_acs = acceptance.read_text(encoding="utf-8", errors="ignore")
                razones.extend(_vc.ac_coverage_problems(texto_acs, mod_id))
                # FW-147: el sello del builder deja de ser un post-it que solo
                # leía /nueva-fase. Estado y aceptación cierran juntos o no
                # cierran.
                razones.extend(_vc.client_seal_problems(texto_acs, mod_id))
                # EVIDENCE GATE dentro del motor. `ac_coverage_problems` solo
                # comprueba que TODOS los ACs estén marcados; que cada uno
                # traiga evidencia sustantiva y que los artefactos citados
                # existan lo verificaba únicamente `validate_acceptance.py`,
                # invocado desde la prosa de /cierre-modulo. Si el agente no
                # ejecutaba ese script, un módulo con ACs marcados [x] y sin
                # ninguna evidencia se cerraba por el motor sin que nada lo
                # detectara: cada pieza pasaba sus tests y el hueco vivía en la
                # costura. Lo encontró el primer test end-to-end (2026-07-24).
                import validate_acceptance as _va
                razones.extend(
                    _va.evidence_gate(texto_acs, mod_id, root=WORKSPACE_ROOT, state=self.state_data)
                )
            if razones:
                raise ValueError(
                    f"complete_module bloqueado para '{mod_id}':\n  - "
                    + "\n  - ".join(razones)
                )
            # FW-149: sellar QUIÉN cerró. Sin esto, descubrir un patrón de
            # fabricación obliga a re-auditar el proyecto entero en vez de los
            # módulos que cerró ese ejecutor (Example Service, 2026-08-26). Se guarda
            # en la entrada de work_tiers ya existente: no añade clave de primer
            # nivel ni schema nuevo.
            _perfil = self.state_data.get("execution_profile") or {}
            _entrada_tier = self.state_data.get("work_tiers", {}).get(mod_id)
            if isinstance(_entrada_tier, dict):
                _sello = {
                    "model": _perfil.get("model") or "no declarado",
                    "capability_tier": _perfil.get("capability_tier") or "no declarado",
                    "closed_at": now_iso,
                }
                _entrada_tier["closed_by"] = _sello
                payload["_closed_by"] = copy.deepcopy(_sello)
            # Gates verdes → delegar la mecánica de buckets/rejections en la
            # rama completed existente (legal desde ready_for_validation).
            return self._apply_event(
                "update_module_status", {"id": mod_id, "status": "completed"}, now_iso
            )

        elif action == "reopen_module":
            # Retroporte Example Lab FW-006: /actualizar-invariante puede
            # detectar que un módulo `completed` ya no cumple un invariante
            # actualizado, pero la máquina de estados no ofrecía ningún camino
            # de reapertura — la remediación canónica quedaba bloqueada y la
            # única salida era editar el JSON a mano. Reapertura EXPLÍCITA:
            # exige `reason` sustantiva, invalida la evidencia de cierre
            # (approval/review receipts) y queda en la bitácora append-only.
            # El código existente se toca después vía /cambio-seguro, como
            # cualquier cambio sobre módulo ya construido.
            mod_id = payload.get("id")
            reason = str(payload.get("reason") or "").strip()
            if not isinstance(mod_id, str) or not mod_id.strip():
                raise ValueError("reopen_module requiere 'id' (string no vacío)")
            mod_id = mod_id.strip()
            if len(reason) < 20:
                raise ValueError(
                    "reopen_module requiere 'reason' sustantiva (≥20 chars) "
                    "citando el invariante/ADR/bug que motiva la reapertura"
                )
            prev = self.state_data.get("module_statuses", {}).get(mod_id)
            if prev != "completed":
                raise ValueError(
                    f"reopen_module: '{mod_id}' está en '{prev}', solo se "
                    "reabre un módulo 'completed'"
                )
            self._reopen_completed_module_state(mod_id, reason)
            return (
                f"Módulo '{mod_id}' reabierto (completed → in_progress). "
                "Evidencia de cierre invalidada; tocar código existente exige /cambio-seguro."
            )

        elif action == "add_module":
            # Retroporte Example Lab FW-003: no había forma canónica de
            # introducir un módulo nuevo al roadmap (∅ → pending); se editaba
            # `03_project_state.json` a mano, rompiendo la Regla 2.
            mod_id = payload.get("id")
            if not isinstance(mod_id, str) or not mod_id.strip():
                raise ValueError("add_module requiere 'id' (string no vacío)")
            mod_id = mod_id.strip()
            modules = self.state_data.setdefault(
                "modules", {"completed": [], "in_progress": [], "pending": []}
            )
            declarados = set(modules.get("completed", [])) | set(
                modules.get("in_progress", [])
            ) | set(modules.get("pending", [])) | set(modules.get("dropped", []))
            if mod_id in declarados:
                raise ValueError(f"add_module: '{mod_id}' ya está declarado")
            modules.setdefault("pending", []).append(mod_id)
            self.state_data.setdefault("module_statuses", {})[mod_id] = "not_started"
            stabilization = payload.get("stabilization")
            if stabilization is not None:
                if not isinstance(stabilization, bool):
                    raise ValueError("add_module: 'stabilization' debe ser booleano")
                if stabilization:
                    self.state_data.setdefault("module_specs", {})[mod_id] = {"stabilization": True}
            return f"Módulo '{mod_id}' añadido a modules.pending (not_started)."

        elif action == "drop_module":
            # FW-118a: un módulo declarado que nunca arrancó (típico tras
            # dividirlo en submódulos) no tenía salida del roadmap — las
            # transiciones legales solo van hacia adelante y forzar
            # `completed` habría corrompido la auditoría. Acotado a lo que
            # DEMOSTRABLEMENTE nunca empezó: todo lo demás sigue pasando por
            # la máquina de estados.
            mod_id = payload.get("id")
            reason = payload.get("reason")
            if not isinstance(mod_id, str) or not mod_id.strip():
                raise ValueError("drop_module requiere 'id' (string no vacío)")
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise ValueError(
                    "drop_module requiere 'reason' de al menos 20 caracteres "
                    "explicando por qué el módulo sale del roadmap"
                )
            reason = self._validate_audit_text("reason", reason)
            mod_id = mod_id.strip()
            modules = self.state_data.setdefault(
                "modules", {"completed": [], "in_progress": [], "pending": []}
            )
            # Orden deliberado: el status se consulta ANTES que la membresía
            # en pending. update_module_status mueve el módulo de bucket en
            # cuanto cambia de estado, así que un módulo que ya arrancó deja
            # de estar en pending — comprobar pending primero produciría un
            # mensaje genérico que oculta la causa real (ya arrancó) detrás
            # de un síntoma (no está en pending).
            status = self.state_data.get("module_statuses", {}).get(mod_id)
            if status not in (None, "not_started"):
                raise ValueError(
                    f"drop_module: '{mod_id}' tiene estado '{status}'; ya arrancó. "
                    "Usa la máquina de estados, no un retiro del roadmap"
                )
            if mod_id not in modules.get("pending", []):
                raise ValueError(
                    f"drop_module: '{mod_id}' no está en modules.pending; solo se "
                    "retira un módulo que nunca arrancó"
                )
            if mod_id in self.state_data.get("module_specs", {}):
                raise ValueError(
                    f"drop_module: '{mod_id}' ya tiene module_spec clasificado; "
                    "ya arrancó. Usa la máquina de estados"
                )
            # Hallazgo 7b (revisión Minor 2026-08-20): un bug `Abierto` que
            # referencia el módulo vía module_id se quedaría sin ningún
            # camino legal de re-homing si el módulo desaparece del roadmap
            # (amend_bug tiene module_id en NO_ENMENDABLES). Bloquear el
            # retiro hasta que esos bugs se resuelvan o se cierren.
            bugs_abiertos = [
                b for b in self.state_data.get("_bugs", [])
                if b.get("module_id") == mod_id and b.get("status") == "Abierto"
            ]
            if bugs_abiertos:
                ids = ", ".join(b.get("id", "?") for b in bugs_abiertos)
                raise ValueError(
                    f"drop_module: '{mod_id}' tiene {len(bugs_abiertos)} bug(s) "
                    f"abierto(s) apuntando a él ({ids}); quedarían sin módulo válido. "
                    "Muévelos a su módulo vivo con reassign_bug_module, o "
                    "resuélvelos con resolve_bug, antes de retirar el módulo"
                )
            modules["pending"].remove(mod_id)
            self.state_data.get("module_statuses", {}).pop(mod_id, None)
            # Hallazgo 7a (revisión Minor 2026-08-20): un módulo pending puede
            # tener tier registrado (add_work_tier no exige que el módulo esté
            # in_progress/completed); dejarlo huérfano en work_tiers es basura
            # que la Regla 2 del framework existe para evitar, aunque el
            # doctor no la marque como error (solo falla si a un módulo activo
            # le FALTA tier, no si a uno inexistente le sobra).
            self.state_data.get("work_tiers", {}).pop(mod_id, None)
            self.state_data["last_completed_step"] = f"Retirado módulo '{mod_id}' del roadmap."
            return f"Módulo '{mod_id}' retirado de modules.pending. Motivo: {reason.strip()}"

        elif action == "abandon_module":
            # PA-FW-037 (5.3.3): `drop_module` solo retira lo que nunca
            # arrancó. Un módulo ya arrancado y obsoleto (duplicado, absorbido)
            # no tenía salida legal: `complete_module` exige recibos y uso
            # real, y volver a `not_started` para hacer el drop falsea la
            # historia. El abandono lo lleva al estado terminal `dropped`
            # (distinto de `completed`: no cuenta como hecho ni como
            # pendiente) y deja el motivo y la evidencia en el estado, además
            # de en la bitácora.
            mod_id = payload.get("id")
            reason = payload.get("reason")
            evidence = payload.get("evidence")
            if not isinstance(mod_id, str) or not mod_id.strip():
                raise ValueError("abandon_module requiere 'id' (string no vacío)")
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise ValueError(
                    "abandon_module requiere 'reason' de al menos 20 caracteres "
                    "explicando por qué el módulo se abandona"
                )
            if not isinstance(evidence, str) or len(evidence.strip()) < 10:
                raise ValueError(
                    "abandon_module requiere 'evidence' (al menos 10 caracteres): "
                    "dónde consta la decisión (inventario, aprobación del builder, ADR)"
                )
            reason = self._validate_audit_text("reason", reason).strip()
            evidence = self._validate_audit_text("evidence", evidence).strip()
            mod_id = mod_id.strip()
            modules = self.state_data.setdefault(
                "modules", {"completed": [], "in_progress": [], "pending": []}
            )
            statuses = self.state_data.setdefault("module_statuses", {})
            if not self._is_known_module(self.state_data, mod_id) and mod_id not in modules.get("dropped", []):
                raise ValueError(f"abandon_module: módulo no declarado: '{mod_id}'")
            status = statuses.get(mod_id)
            if status is None and mod_id in modules.get("pending", []):
                status = "not_started"
            if self._is_dropped_module(self.state_data, mod_id):
                raise ValueError(
                    f"abandon_module: '{mod_id}' ya está dropped (estado terminal); "
                    "un módulo abandonado no se reabre ni se abandona de nuevo"
                )
            if status in _transitions.TERMINAL_MODULE_STATUSES:
                raise ValueError(
                    f"abandon_module: '{mod_id}' tiene estado terminal '{status}'; "
                    "un módulo completed no se abandona: si hay que rehacerlo, "
                    "reábrelo con reopen_module (motivo)"
                )
            bugs_abiertos = [
                b for b in self.state_data.get("_bugs", [])
                if b.get("module_id") == mod_id and b.get("status") == "Abierto"
            ]
            if bugs_abiertos:
                ids = ", ".join(b.get("id", "?") for b in bugs_abiertos)
                raise ValueError(
                    f"abandon_module: '{mod_id}' tiene {len(bugs_abiertos)} bug(s) "
                    f"abierto(s) apuntando a él ({ids}). Muévelos a su módulo vivo "
                    "con reassign_bug_module, o resuélvelos con resolve_bug, antes "
                    "de abandonarlo"
                )
            # Re-revisión 5.3.3: igual que los bugs abiertos, una clarificación
            # Abierta con blocks_* sobre el módulo seguiría bloqueando todo el
            # proyecto (la vista la cuenta sin mirar el módulo) tras el abandono.
            _, clars_bloqueantes = self._module_blockers(mod_id)
            if clars_bloqueantes:
                ids = ", ".join(c.get("id", "?") for c in clars_bloqueantes)
                raise ValueError(
                    f"abandon_module: '{mod_id}' tiene {len(clars_bloqueantes)} "
                    f"clarificación(es) abierta(s) con blocks_* apuntando a él ({ids}); "
                    "seguirían bloqueando el proyecto aunque el módulo ya no exista. "
                    f"Resuelve antes la clarificación {ids} con resolve_clarification"
                )
            for b in ("completed", "in_progress", "pending"):
                if mod_id in modules.get(b, []):
                    modules[b].remove(mod_id)
            modules.setdefault("dropped", []).append(mod_id)
            statuses[mod_id] = "dropped"
            self.state_data.setdefault("module_abandonments", {})[mod_id] = {
                "from_status": status,
                "reason": reason,
                "evidence": evidence,
                "abandoned_at": now_iso,
            }
            # Revisión 5.3.3: los rechazos de cierre del abandonado contaban
            # para la Regla 7 para siempre (resolve_closure_rejections exige
            # ready_for_validation y un dropped no vuelve). Se archivan con el
            # mismo mecanismo que el cierre, resueltos por el abandono.
            archivados = self._archive_module_closure_rejections(
                mod_id, now_iso,
                abandonment=(f"Módulo abandonado (abandon_module): {reason}", evidence),
            )
            # El puntero de sesión no debe seguir anunciando como siguiente un
            # módulo terminal; no se inventa otro: /scope-siguiente decide.
            sess = self.state_data.get("session")
            if isinstance(sess, dict) and sess.get("next_module") == mod_id:
                sess.pop("next_module", None)
                sess.pop("next_module_description", None)
            self.state_data["last_completed_step"] = f"Abandonado módulo '{mod_id}' ({status} -> dropped)."
            extra = (
                f" — rechazos de cierre archivados: {', '.join(archivados)}" if archivados else ""
            )
            return (
                f"Módulo '{mod_id}' abandonado ({status} -> dropped, terminal). "
                f"Motivo: {reason}{extra}"
            )

        elif action == "reassign_bug_module":
            # PA-FW-037 (5.3.3): un residuo real de un módulo que se retira
            # debe seguir abierto en su módulo vivo. `amend_bug` mantiene
            # `module_id` fuera de lo enmendable (cambia la semántica de
            # bloqueo); esta acción lo mueve con motivo y conserva el valor
            # previo en `amendments`. No transita ningún módulo: si el destino
            # debe bloquearse, lo decide el agente con update_module_status.
            bug_id = payload.get("id")
            target = payload.get("module_id")
            reason = payload.get("reason")
            if not isinstance(bug_id, str) or not bug_id.strip():
                raise ValueError("reassign_bug_module requiere 'id' (string no vacío)")
            if not isinstance(target, str) or not target.strip():
                raise ValueError("reassign_bug_module requiere 'module_id' (string no vacío)")
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise ValueError(
                    "reassign_bug_module requiere 'reason' de al menos 20 caracteres "
                    "explicando por qué el bug pertenece al módulo destino"
                )
            reason = self._validate_audit_text("reason", reason).strip()
            bug_id, target = bug_id.strip(), target.strip()
            bug = next((b for b in self.state_data.get("_bugs", []) if b.get("id") == bug_id), None)
            if not bug:
                raise ValueError(f"reassign_bug_module: bug {bug_id} no encontrado")
            if bug.get("status") != "Abierto":
                raise ValueError(
                    f"reassign_bug_module: {bug_id} tiene estado '{bug.get('status')}'; "
                    "solo se reasigna un bug Abierto"
                )
            if not self._is_known_module(self.state_data, target):
                raise ValueError(f"reassign_bug_module: módulo destino no declarado: '{target}'")
            target_status = self.state_data.get("module_statuses", {}).get(target)
            if self._is_dropped_module(self.state_data, target):
                raise ValueError(
                    f"reassign_bug_module: el módulo destino '{target}' está dropped; "
                    "un bug abierto debe vivir en un módulo vivo"
                )
            if target_status == "completed" or target in self.state_data.get("modules", {}).get("completed", []):
                # Revisión 5.3.3: mover un bloqueante a un completed lo sacaba
                # del gate de cierre del origen sin que nadie lo viera.
                raise ValueError(
                    f"reassign_bug_module: el módulo destino '{target}' está completed. "
                    "Reabre el destino con reopen_module (motivo) o elige un módulo abierto"
                )
            previous = bug.get("module_id")
            if previous == target:
                raise ValueError(f"reassign_bug_module: {bug_id} ya apunta a '{target}'")
            evidence = payload.get("evidence")
            bloqueante = bug.get("severity") == "Crítico" or (
                bug.get("severity") == "Mayor" and bool(bug.get("affects_ac"))
            )
            if evidence is not None or bloqueante:
                if not isinstance(evidence, str) or len(evidence.strip()) < 10:
                    raise ValueError(
                        f"reassign_bug_module: {bug_id} es {bug.get('severity')}"
                        f"{' con affects_ac' if bug.get('affects_ac') else ''} y bloquea el cierre "
                        "de su módulo; requiere 'evidence' (al menos 10 caracteres) con la "
                        "aprobación del builder para moverlo"
                        if bloqueante else
                        "reassign_bug_module: 'evidence', si se da, debe tener al menos 10 caracteres"
                    )
                evidence = self._validate_audit_text("evidence", evidence).strip()
            amendment = {
                "at": now_iso,
                "reason": reason,
                "previous": {"module_id": previous},
            }
            if evidence is not None:
                amendment["evidence"] = evidence
            bug.setdefault("amendments", []).append(amendment)
            bug["module_id"] = target
            self.state_data["last_completed_step"] = f"Reasignado bug {bug_id} a '{target}'."
            return f"Bug {bug_id} reasignado: '{previous}' -> '{target}'. Motivo: {reason}"

        elif action == "set_active_module":
            # Retroporte Example Lab FW-002: con 2+ módulos in_progress la
            # proyección usa in_progress[0] como activo; sin este verbo había
            # que reordenar la lista editando el JSON a mano.
            mod_id = payload.get("id")
            if not isinstance(mod_id, str) or not mod_id.strip():
                raise ValueError("set_active_module requiere 'id' (string no vacío)")
            mod_id = mod_id.strip()
            in_progress = self.state_data.get("modules", {}).get("in_progress", [])
            if self._is_dropped_module(self.state_data, mod_id):
                raise ValueError(
                    f"set_active_module: '{mod_id}' está dropped (abandonado, terminal); "
                    "elige un módulo de modules.in_progress"
                )
            if mod_id not in in_progress:
                raise ValueError(
                    f"set_active_module: '{mod_id}' no está en modules.in_progress "
                    f"(actual: {in_progress or 'vacío'}). Arráncalo primero con "
                    f"update_module_status."
                )
            in_progress.remove(mod_id)
            in_progress.insert(0, mod_id)
            return f"'{mod_id}' es ahora el módulo activo (in_progress[0])."

        elif action == "append_to_list_field":
            # Retroporte de fricción de campo (Example Lab FW-002/003/005,
            # Example Documents FW-001, Example CLI): varios campos-lista
            # planos carecían de action dedicada, forzando editar
            # `03_project_state.json` a mano — justo lo que la Regla 2 prohíbe.
            # Whitelist para NO permitir mutación arbitraria del estado. Los
            # campos de `modules.*` quedan FUERA a propósito: tocan la
            # consistencia con `module_statuses` y merecen su propia action
            # (`add_module`) con diseño de estado — ver candidato de retroporte.
            _APPENDABLE_FIELDS = {"dependencies", "architecture_constraints"}
            field = payload.get("field")
            value = payload.get("value")
            unique = payload.get("unique", True)
            if field not in _APPENDABLE_FIELDS:
                raise ValueError(
                    f"append_to_list_field: 'field' debe ser uno de "
                    f"{sorted(_APPENDABLE_FIELDS)} (recibido: {field!r})"
                )
            if not isinstance(value, str) or not value.strip():
                raise ValueError("append_to_list_field requiere 'value' (string no vacío)")
            value = value.strip()
            lst = self.state_data.setdefault(field, [])
            if not isinstance(lst, list):
                raise ValueError(f"'{field}' no es una lista en el estado")
            if not (unique and value in lst):
                lst.append(value)
            return f"'{value}' añadido a {field}."

        elif action == "set_framework_version":
            version = payload.get("version")
            if not isinstance(version, str) or not re.match(r"^\d+\.\d+(\.\d+)?$", version):
                raise ValueError("set_framework_version requiere 'version' con formato X.Y o X.Y.Z")
            self.state_data["framework_version"] = version
            return f"framework_version actualizado a {version} (upgrade de plantilla)."

        elif action == "set_focus":
            area = payload.get("area")
            if area is not None and (not isinstance(area, str) or not area.strip()):
                raise ValueError("set_focus requiere 'area' (string no vacío) o null para limpiar el foco")
            normalized = area.strip() if isinstance(area, str) else None
            if normalized == "":
                normalized = None
            self.state_data["active_focus"] = normalized
            if normalized:
                return f"Foco activo fijado a '{normalized}'."
            return "Foco limpiado."

        elif action == "ciclo_started":
            # 3.14.0: telemetría del driver acotado /ciclo. Pura traza a
            # state_log — no muta state_data. El driver llama esto al
            # arrancar un módulo (después de pasar el STOP GATE).
            module = payload.get("module")
            if not isinstance(module, str) or not module.strip():
                raise ValueError("ciclo_started requiere 'module' (string no vacío)")
            self._validate_cycle_mode("ciclo_started", payload)
            return f"ciclo_started: {module.strip()}"

        elif action == "ciclo_paused":
            # 3.14.0: el driver pausó (STOP GATE activo, builder pidió pausa,
            # o gate calibrado bloqueó). Pura traza.
            reason = payload.get("reason", "")
            if not isinstance(reason, str):
                raise ValueError("ciclo_paused.reason debe ser string")
            return f"ciclo_paused: {reason.strip() or '(sin razón)'}"

        elif action == "ciclo_finished":
            # 3.14.0: el driver terminó un módulo (ok / paused / blocked).
            # Pura traza. El 'result' se valida contra un set cerrado para
            # que el log sea queryable.
            module = payload.get("module")
            result = payload.get("result")
            if not isinstance(module, str) or not module.strip():
                raise ValueError("ciclo_finished requiere 'module' (string no vacío)")
            if result not in {"ok", "paused", "blocked"}:
                raise ValueError("ciclo_finished.result debe ser uno de: ok|paused|blocked")
            self._validate_cycle_mode("ciclo_finished", payload)
            return f"ciclo_finished: {module.strip()} (result={result})"

        elif action == "defer_bug_to_tech_debt":
            bug_id = payload.get("id")
            if not bug_id:
                raise ValueError("Se requiere 'id'")
            bug = next((b for b in self.state_data.get("_bugs", []) if b["id"] == bug_id), None)
            if not bug:
                raise ValueError(f"Bug {bug_id} no encontrado")
            bug["status"] = "Deuda Técnica"
            mod_id = bug["module_id"]
            blocking_bugs, blocking_clars = self._module_blockers(mod_id)
            if not blocking_bugs and not blocking_clars:
                if self.state_data.get("module_statuses", {}).get(mod_id) == "blocked":
                    is_pending = mod_id in self.state_data.get("modules", {}).get("pending", [])
                    new_status = "not_started" if is_pending else "in_progress"
                    self.state_data["module_statuses"][mod_id] = new_status
                    # TL FW-109 / TL FW-235: mismo caso condicional que resolve_bug arriba.
                    payload["_status_effect"] = {"module": mod_id, "status": new_status}
            return f"Bug {bug_id} trasladado a Deuda Técnica."

        elif action == "import_markdown_entities":
            # FW-BUG02: Ingesta agregada de entidades desde Markdown al estado JSON.
            # No muta module_statuses (la contención de bugs/clarificaciones bloqueantes
            # opera en query-time vía state_queries._blocking_items).
            bugs_payload = payload.get("bugs", []) or []
            clars_payload = payload.get("clarifications", []) or []
            decs_payload = payload.get("decisions", []) or []

            imported_ids: list[str] = []

            bugs = self.state_data.setdefault("_bugs", [])
            existing_bug_ids = {b.get("id") for b in bugs if isinstance(b, dict) and b.get("id")}
            for b in bugs_payload:
                bid = b.get("id")
                if bid and bid not in existing_bug_ids:
                    bugs.append(dict(b))
                    existing_bug_ids.add(bid)
                    imported_ids.append(bid)

            clars = self.state_data.setdefault("_clarifications", [])
            existing_clar_ids = {c.get("id") for c in clars if isinstance(c, dict) and c.get("id")}
            for c in clars_payload:
                cid = c.get("id")
                if cid and cid not in existing_clar_ids:
                    clars.append(dict(c))
                    existing_clar_ids.add(cid)
                    imported_ids.append(cid)

            decs = self.state_data.setdefault("_decisions", [])
            existing_dec_ids = {d.get("id") for d in decs if isinstance(d, dict) and d.get("id")}
            for d in decs_payload:
                did = d.get("id")
                if did and did not in existing_dec_ids:
                    decs.append(dict(d))
                    existing_dec_ids.add(did)
                    imported_ids.append(did)

            payload["imported_ids"] = imported_ids
            return f"Importadas {len(imported_ids)} entidades desde Markdown a 03_project_state.json."

        elif action == "acknowledge_state_delta":
            # L7 (N2): escape auditado del gate de estado respaldado. No muta
            # el estado: deja en la bitácora encadenada quién admitió qué
            # divergencia y por qué. Las rutas son literales (sin comodines).
            reason = payload.get("reason")
            approver = payload.get("approver")
            paths = payload.get("paths")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("acknowledge_state_delta requiere 'reason' (string no vacío)")
            if not isinstance(approver, str) or not approver.strip():
                raise ValueError("acknowledge_state_delta requiere 'approver' (string no vacío)")
            if (
                not isinstance(paths, list) or not paths
                or not all(isinstance(x, str) and x.strip() for x in paths)
            ):
                raise ValueError("acknowledge_state_delta requiere 'paths': lista no vacía de rutas literales")
            comodines = [x for x in paths if any(c in x for c in "*?")]
            if comodines:
                raise ValueError(
                    f"acknowledge_state_delta: las rutas son literales, sin comodines: {comodines}"
                )
            return f"acknowledge_state_delta: {len(paths)} ruta(s) admitidas por {approver.strip()}"

        elif action == "seed_snapshot":
            # FW-252 / L7: siembra las entidades anteriores al log, con el commit
            # del que provienen. Solo AÑADE lo ausente; nunca sobrescribe.
            commit = payload.get("source_commit")
            reason = payload.get("reason")
            entities = payload.get("entities")
            if not isinstance(commit, str) or not commit.strip():
                raise ValueError("seed_snapshot requiere 'source_commit' (string no vacío)")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("seed_snapshot requiere 'reason' (string no vacío)")
            # FW-274: por referencia, lo que importa es la base del replay, no lo añadido.
            origin = (f"base del replay declarada en {commit.strip()} (por referencia)" if entities is None
                      else f"semilla en línea desde {commit.strip()}")
            if entities is None:
                # L14 / D9c: por referencia. La base se carga desde git y se
                # verifica contra el hash que el evento fijó; nunca se finge.
                expected = payload.get("state_hash")
                if not isinstance(expected, str) or not expected.strip():
                    raise ValueError(
                        "seed_snapshot requiere 'entities' (en línea) o 'state_hash' (por referencia)"
                    )
                repo = self._workspace_root()
                actual = state_hash_at_commit(repo, commit.strip())
                if actual != expected.strip():
                    raise ValueError(
                        f"seed_snapshot: el hash del estado en {commit.strip()} no coincide "
                        f"(evento {expected.strip()[:12]}…, git {actual[:12]}…); no se siembra"
                    )
                past = _state_at_commit(repo, commit.strip())
                entities = {k: past[k] for k in _SEEDABLE if past.get(k)}
                if not entities:
                    return f"seed_snapshot: {origin}; 0 elemento(s) añadidos: el commit no tiene entidades sembrables"
            if not isinstance(entities, dict) or not entities:
                raise ValueError("seed_snapshot requiere 'entities' (objeto no vacío)")
            permitidas = set(_SEEDABLE)
            ajenas = sorted(set(entities) - permitidas)
            if ajenas:
                raise ValueError(f"seed_snapshot: entidades no sembrables {ajenas}; permitidas: {sorted(permitidas)}")
            st = self.state_data
            sembradas = 0
            for key, val in entities.items():
                if key in ("_bugs", "_clarifications", "_decisions"):
                    if not isinstance(val, list):
                        raise ValueError(f"seed_snapshot.{key} debe ser lista")
                    cur = st.setdefault(key, [])
                    have = {e.get("id") for e in cur if isinstance(e, dict)}
                    for e in val:
                        if isinstance(e, dict) and e.get("id") not in have:
                            cur.append(copy.deepcopy(e))
                            have.add(e.get("id"))
                            sembradas += 1
                elif key in ("module_statuses", "work_tiers", "module_specs"):
                    if not isinstance(val, dict):
                        raise ValueError(f"seed_snapshot.{key} debe ser objeto")
                    cur = st.setdefault(key, {})
                    for k, v in val.items():
                        if k not in cur:
                            cur[k] = copy.deepcopy(v)
                            sembradas += 1
                elif key == "modules":
                    if not isinstance(val, dict):
                        raise ValueError("seed_snapshot.modules debe ser objeto")
                    cur = st.setdefault("modules", {"completed": [], "in_progress": [], "pending": []})
                    # Solo AÑADE lo ausente: un módulo `dropped` (PA-FW-037) no está
                    # ausente; sembrarlo lo dejaría a la vez en un bucket vivo y en
                    # modules.dropped.
                    known = {m for b in ("completed", "in_progress", "pending", "dropped") for m in cur.get(b, [])}
                    for bucket in ("completed", "in_progress", "pending"):
                        for m in val.get(bucket, []):
                            if m not in known and not self._is_dropped_module(st, m):
                                cur.setdefault(bucket, []).append(m)
                                known.add(m)
                                sembradas += 1
                elif key in ("dependencies", "architecture_constraints"):
                    if not isinstance(val, list):
                        raise ValueError(f"seed_snapshot.{key} debe ser lista")
                    cur = st.setdefault(key, [])
                    for v in val:
                        if v not in cur:
                            cur.append(copy.deepcopy(v))
                            sembradas += 1
                elif key == "stack":
                    if not isinstance(val, dict):
                        raise ValueError("seed_snapshot.stack debe ser objeto")
                    cur = st.setdefault("stack", {})
                    for k, v in val.items():
                        if not cur.get(k):
                            cur[k] = v
                            sembradas += bool(v)  # una clave vacía en los dos lados no añade nada
            return (f"seed_snapshot: {origin}; {sembradas} elemento(s) añadidos al estado vivo"
                    + ("; ya estaban todos" if not sembradas else ""))

        elif action in RETIRED_REPLAY_ACTIONS and getattr(self, "_is_replay", False):
            # Acción retirada (p. ej. `drift_fix`): el evento está en una
            # bitácora ya escrita; el replay lo salta con aviso, no aborta.
            root = getattr(self, "_replay_workspace_root", None)
            proyecto = Path(root).name if root else "?"
            aviso = (f"acción retirada '{action}' ignorada en replay del proyecto "
                     f"'{proyecto}' (sin efecto sobre el estado)")
            sink = getattr(self, "_replay_warnings", None)
            if isinstance(sink, list):
                sink.append(aviso)
            else:
                print(f"AVISO: {aviso}", file=sys.stderr)
            return aviso

        else:
            # Última parada: extensiones del producto (ADR-052). Las built-in
            # nunca llegan aquí, así que el motor siempre gana ante colisión.
            extensiones = _load_extension_actions()
            if action in extensiones:
                resultado = extensiones[action](self, payload, now_iso)
                if not isinstance(resultado, str):
                    raise ValueError(
                        f"La extensión '{action}' debe devolver str (devolvió {type(resultado).__name__})"
                    )
                return resultado
            raise ValueError(
                f"Acción desconocida: {action}. Las acciones custom del producto "
                f"se registran en {EXTENSIONS_FILE_PATH.name} bajo .agent/extensions/ (ADR-052)."
            )

    _CYCLE_MODES = ("modulo", "fase", "paralelo")

    def _validate_cycle_mode(self, action: str, payload: dict) -> None:
        """L9 (bug de FW-332): `mode` opcional de `ciclo_*`, conjunto cerrado.

        Regla nueva del motor: en un replay (D10) la historia no se re-juzga.
        """
        if "mode" not in payload or getattr(self, "_is_replay", False):
            return
        mode = payload["mode"]
        if not isinstance(mode, str) or mode not in self._CYCLE_MODES:
            raise ValueError(f"{action}.mode debe ser uno de: " + "|".join(self._CYCLE_MODES))

    def _apply_maintenance_action(self, action: str, payload: dict, now_iso: str) -> str:
        """FW-225: modo mantenimiento. Los gates viven en `maintenance.py` y se
        ejecutan aquí, dentro del motor; durante un replay la historia se
        reproduce sin re-juzgarla (mismo criterio que `complete_module`)."""
        import maintenance as _mt

        state = self.state_data
        replay = getattr(self, "_is_replay", False)

        def _gate(problems: list[str]) -> None:
            if problems and not replay:
                raise ValueError(f"{action} bloqueado: " + "; ".join(problems))

        if action == "set_lifecycle":
            _gate(_mt.set_lifecycle_problems(state, payload))
            previous = state.get("lifecycle") or {}
            lifecycle = {
                "stage": payload["stage"],
                "set_at": now_iso,
                "set_by": "builder",
                "reason": self._validate_audit_text("reason", payload["reason"]),
            }
            patterns = payload.get("escalation_patterns", previous.get("escalation_patterns"))
            if patterns:
                lifecycle["escalation_patterns"] = list(patterns)
            state["lifecycle"] = lifecycle
            return f"Ciclo de vida fijado a '{payload['stage']}' por el builder."

        if action == "confirm_builder_use":
            _gate(_mt.confirm_builder_use_problems(state, payload))
            module = payload["module"]
            entry = state.setdefault("work_tiers", {}).setdefault(module, {})
            use = entry.get("builder_use") if isinstance(entry.get("builder_use"), dict) else {}
            evidences = list(use.get("evidence") or [])
            evidences.append({
                "text": self._validate_audit_text("evidence", payload["evidence"]),
                "confirmed_at": now_iso,
            })
            entry["builder_use"] = {
                "confirmed_at": use.get("confirmed_at") or now_iso,
                "confirmed_by": "builder",
                "evidence": evidences,
            }
            return f"Uso real de '{module}' confirmado por el builder ({len(evidences)} evidencia(s))."

        if action == "open_change":
            _gate(_mt.open_change_problems(state, payload))
            change_id = payload.get("id") if replay and payload.get("id") else _mt.next_change_id(state)
            payload["id"] = change_id
            summary = self._validate_audit_text("summary", payload["summary"])
            state.setdefault("changes", []).append({
                "id": change_id,
                "summary": summary,
                "tier": payload["tier"],
                "criteria": list(payload["criteria"]),
                "affected_modules": list(payload.get("affected_modules") or []),
                "status": "open",
                "opened_at": now_iso,
            })
            state.setdefault("work_tiers", {})[f"task:{change_id}"] = {
                "tier": payload["tier"], "rationale": summary,
                "classified_at": now_iso, "classified_by": "open_change",
            }
            return f"Cambio {change_id} abierto ({payload['tier']})."

        if action == "close_change":
            _gate(_mt.close_change_problems(state, payload, WORKSPACE_ROOT))
            change = _mt.find_change(state, payload.get("id"))
            changelog = self._validate_audit_text("changelog", payload["changelog"])
            change.update({
                "status": "closed",
                "closed_at": now_iso,
                "evidence": dict(payload["evidence"]),
                "tests": payload["tests"],
                "changelog": changelog,
            })
            state["last_completed_step"] = f"{change['id']} cerrado: {changelog}"
            return f"Cambio {change['id']} cerrado."

        # escalate_change
        _gate(_mt.escalate_change_problems(state, payload))
        change = _mt.find_change(state, payload.get("id"))
        module_id = payload["module_id"].strip()
        reason = self._validate_audit_text("reason", payload["reason"])
        change.update({
            "status": "escalated", "closed_at": now_iso,
            "escalated_to": module_id, "escalation_reason": reason,
        })
        modules = state.setdefault("modules", {"completed": [], "in_progress": [], "pending": []})
        modules.setdefault("in_progress", []).append(module_id)
        state.setdefault("module_statuses", {})[module_id] = "in_progress"
        state.setdefault("work_tiers", {})[module_id] = {
            "tier": "tier_2", "rationale": f"Escalado desde {change['id']}: {reason}",
            "classified_at": now_iso, "classified_by": "escalate_change",
            "requires_second_review": True,
        }
        return f"Cambio {change['id']} escalado al módulo '{module_id}' (tier_2, flujo completo)."

    def render_projections(self) -> None:
        import projections
        projections.render(self.state_data)
