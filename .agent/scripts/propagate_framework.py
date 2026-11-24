#!/usr/bin/env python3
"""Detecta y aplica la propagación del framework a los proyectos adoptantes.

Por qué existe (FW-169): la deriva plantilla→proyectos era **invisible**. El
único aviso de atraso del doctor (`validate_framework_version_consistency`)
compara `state.framework_version` contra `manifest.framework_version`, y ambos
viajan en la misma copia de `.agent/`: en un proyecto sin actualizar coinciden,
así que el warning nunca dispara. Un proyecto no puede saber que va atrasado
porque no conoce la versión de la plantilla. Sin un detector del lado de la
plantilla, la deriva solo se descubre a mano — y se acumula entre releases.

Además detecta **parches locales** (FW-040): ficheros de `.agent/scripts/` que
difieren de la plantilla en la versión que el proyecto declaró. Un upgrade que
los pisa es una regresión silenciosa cuya única señal sería un test del
producto, si existe. Con parches detectados, `--apply` se niega.

Uso:
    python3 .agent/scripts/propagate_framework.py                 # informe
    python3 .agent/scripts/propagate_framework.py --json
    python3 .agent/scripts/propagate_framework.py --apply --yes   # upgrade
    python3 .agent/scripts/propagate_framework.py --apply --yes --skip "Example Lab"

Códigos de salida: 0 = sin deriva · 1 = hay deriva o bloqueos · 2 = error de uso.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Rutas de `.agent/` que el upgrade sobrescribe. Espejo del paso 4 de
# `/actualizar-framework`; cualquier cambio ahí debe reflejarse aquí.
# Nunca se tocan: son del producto (paso 3, inventario PRESERVAR).
PRESERVE = ("skills/imported", "skills/domain", "extensions")

# `skills` se copia aparte (solo los `*.md` core, para no pisar imported/ y
# domain/); `extensions` es del producto.
NOT_OVERWRITTEN = ("skills", "extensions")


def overwrite_dirs() -> tuple[str, ...]:
    """Directorios de `.agent/` que el upgrade sobrescribe, **derivados del disco**.

    Antes era una tupla enumerada a mano, y eso significaba que un directorio
    nuevo del framework no llegaba a ningún adoptante: `.agent/prompts/` (4.30.0)
    se quedó fuera y los 9 upgrades a 4.31.0 murieron con `doctor FALLA tras el
    upgrade` — el rollback los salvó, pero la causa no era el proyecto sino la
    lista. Misma clase que FW-150, donde el bloque de adopción del README no
    listaba `GEMINI.md` y el contrato de clean-clone llevó 8 commits roto.

    Derivar del disco hace que un directorio nuevo **nazca propagado**: para
    quedar fuera hay que nombrarlo explícitamente en `NOT_OVERWRITTEN`."""
    base = ROOT / ".agent"
    if not base.is_dir():
        return ()
    return tuple(sorted(
        d.name for d in base.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name not in NOT_OVERWRITTEN
    ))

RELEASE_RE = re.compile(r"^release:\s*(\d+\.\d+\.\d+)\b")


def template_version() -> str:
    return json.loads(
        (ROOT / ".agent/workflows/manifest.json").read_text(encoding="utf-8")
    )["framework_version"]


def release_commit(version: str) -> str | None:
    """Commit de la plantilla que publicó `version`. No hay tags; las releases se
    identifican por el asunto `release: X.Y.Z -- ...`."""
    out = subprocess.run(
        ["git", "-C", str(ROOT), "log", "--format=%H %s", "--all"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        sha, _, subject = line.partition(" ")
        match = RELEASE_RE.match(subject)
        if match and match.group(1) == version:
            return sha
    return None


INDETERMINATE = "<sin release de referencia>"


def missing_release_marker(version: str) -> bool:
    """True si la plantilla no tiene el commit `release: <version> -- ...`.

    Sin ese commit, `local_patches` no tiene base para el merge de tres vías y
    todo proyecto que declare esta versión queda INDETERMINATE — bloqueado sin
    remedio hasta que se cree la marca."""
    return release_commit(version) is None


def _patch_already_upstream(base: Path, proyecto: Path, plantilla_actual: Path) -> bool:
    """True si el parche del proyecto (proyecto vs. base) ya está incorporado
    en la plantilla actual — es decir, aplicarlo sobre la plantilla no cambia
    nada.

    Merge de tres vías: `git merge-file -p <proyecto> <base> <plantilla>`
    combina el cambio del proyecto (proyecto sobre base) con el cambio de la
    plantilla (plantilla sobre base). Si el resultado no tiene conflicto y
    coincide con la plantilla, el parche ya vive ahí y perderlo no pierde nada
    real."""
    merged = subprocess.run(
        ["git", "merge-file", "-p", "--quiet", str(proyecto), str(base), str(plantilla_actual)],
        capture_output=True,
    )
    if merged.returncode != 0:
        # >0: conflicto real. <0: error de git merge-file. Ninguno de los dos
        # es "sin conflicto" — fail-closed, se trata como parche pendiente.
        return False
    return merged.stdout == plantilla_actual.read_bytes()


def local_patches(project: Path, version: str) -> list[str]:
    """Ficheros de `.agent/scripts/` que difieren de la plantilla en la versión
    que el proyecto declaró — es decir, parches locales que un upgrade pisaría.

    Un fichero que difiere de esa base no es automáticamente un parche real:
    puede que ya se haya subido a la plantilla actual entre esa release y hoy.
    Solo cuenta si perderlo perdería algo que la plantilla no tiene (FW-plan:
    "arreglar el comparador, no la propagación")."""
    sha = release_commit(version)
    if sha is None:
        # Sin commit de release no hay referencia comparable. Devolver [] se
        # leería como "sin parches", que es afirmar lo que no se sabe — el mismo
        # fail-open que este detector existe para evitar. Se declara
        # indeterminado y bloquea (FW-179).
        return [INDETERMINATE]
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(
            ["git", "-C", str(ROOT), "archive", sha, ".agent/scripts"],
            capture_output=True,
        )
        if archive.returncode != 0:
            return []
        # capture_output=True: sin esto, GNU tar (Linux/CI) filtra al stdout
        # heredado el warning "Ignoring unknown extended header keyword" del
        # pax_global_header que `git archive` añade — invisible en BSD tar
        # (macOS), pero corrompe el `--json` de main() con texto no-JSON
        # intercalado cuando local_patches() se invoca antes del dump final.
        subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout, capture_output=True, check=False)
        reference = Path(tmp) / ".agent/scripts"
        if not reference.is_dir():
            return []
        patched = []
        for ref_file in sorted(reference.rglob("*")):
            if not ref_file.is_file() or "__pycache__" in ref_file.parts:
                continue
            rel = ref_file.relative_to(reference)
            current = project / ".agent/scripts" / rel
            if not current.is_file() or current.read_bytes() == ref_file.read_bytes():
                continue
            plantilla_actual = ROOT / ".agent/scripts" / rel
            if plantilla_actual.is_file() and _patch_already_upstream(
                ref_file, current, plantilla_actual
            ):
                continue
            patched.append(str(rel))
        return patched


def git_state(project: Path) -> str:
    if not (project / ".git").exists():
        return "sin-git"
    out = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return "sin-git"
    n = len([line for line in out.stdout.splitlines() if line.strip()])
    return "limpio" if n == 0 else f"sucio({n})"


def current_branch(project: Path) -> str | None:
    if not (project / ".git").exists():
        return None
    res = subprocess.run(
        ["git", "-C", str(project), "branch", "--show-current"],
        capture_output=True, text=True,
    )
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return None


def default_branch(project: Path) -> str | None:
    if not (project / ".git").exists():
        return None
    res = subprocess.run(
        ["git", "-C", str(project), "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    if res.returncode == 0 and res.stdout.strip():
        ref = res.stdout.strip()
        return ref.split("/")[-1]
    for cand in ("main", "master"):
        probe = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--verify", f"refs/heads/{cand}"],
            capture_output=True, text=True,
        )
        if probe.returncode == 0:
            return cand
    return None


def git_show_version(project: Path, branch: str) -> str | None:
    if not (project / ".git").exists() or not branch:
        return None
    res = subprocess.run(
        ["git", "-C", str(project), "show", f"{branch}:_project/03_project_state.json"],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        try:
            return json.loads(res.stdout).get("framework_version")
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def discover(search_roots: list[Path]) -> list[Path]:
    found = []
    for root in search_roots:
        for state in root.glob("*/*/_project/03_project_state.json"):
            project = state.parent.parent
            if project != ROOT and (project / ".agent/workflows/manifest.json").exists():
                found.append(project)
        for state in root.glob("*/_project/03_project_state.json"):
            project = state.parent.parent
            if project != ROOT and (project / ".agent/workflows/manifest.json").exists():
                found.append(project)
    return sorted(set(found))


# S8 (5.3.1 L5.5): las migraciones de 5.2.x se retiraron. Desde una versión anterior al suelo
# se sube primero al suelo con la plantilla en su etiqueta y después a la versión nueva.
UPGRADE_FLOOR = "5.3.0"


def _version_key(value: object) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return None


def below_floor(declared: object, target: object) -> bool:
    """True si el proyecto declara una versión anterior al suelo y la plantilla ya lo exige."""
    have, want, floor = _version_key(declared), _version_key(target), _version_key(UPGRADE_FLOOR)
    return bool(have and want and have < floor <= want)


def inspect(project: Path, target: str, upstreamed: frozenset[str] = frozenset()) -> dict:
    def version_of(path: Path, key: str) -> str | None:
        try:
            return json.loads(path.read_text(encoding="utf-8")).get(key)
        except (OSError, json.JSONDecodeError):
            return None

    declared = version_of(project / "_project/03_project_state.json", "framework_version")
    manifest = version_of(project / ".agent/workflows/manifest.json", "framework_version")
    git = git_state(project)
    cur_branch = current_branch(project)
    def_branch = default_branch(project)
    def_version = git_show_version(project, def_branch) if def_branch else None
    default_behind = bool(def_version and def_version != target and cur_branch != def_branch)
    patches = local_patches(project, declared) if declared else []

    blockers = []
    if git != "limpio":
        blockers.append("árbol git sucio o ausente: /actualizar-framework (tier_2) exige limpio o backup")
    if declared != manifest:
        blockers.append(f"state={declared} ≠ manifest={manifest}: copia inconsistente, revisar antes de upgrade")
    if below_floor(declared, target):
        blockers.append(
            f"{declared} es anterior a {UPGRADE_FLOOR}: este upgrade solo sube desde {UPGRADE_FLOOR}. Ruta: sube "
            f"este proyecto primero a {UPGRADE_FLOOR} con /actualizar-framework, usando como plantilla nueva la "
            f"etiqueta v{UPGRADE_FLOOR} (`git -C <plantilla> fetch --tags` y `git -C <plantilla> worktree add "
            f"<carpeta> v{UPGRADE_FLOOR}`), y después a {target} con esta plantilla. No ejecutes "
            f"propagate_framework.py --apply desde esa carpeta: trataría como atrasados, y bajaría de versión, "
            f"los proyectos que ya están en {target}"
        )
    acknowledged = patches and project.name in upstreamed
    if patches == [INDETERMINATE] and not acknowledged:
        blockers.append(
            f"no hay commit 'release: {declared}' en la plantilla: imposible saber si "
            f"el proyecto lleva parches locales. Publicar ese release o, si consta que "
            f"no los lleva, declararlo con --patches-upstreamed {project.name}"
        )
    elif patches and not acknowledged:
        blockers.append(f"parches locales que el upgrade pisaría: {', '.join(patches)}")

    return {
        "project": project.name,
        "path": str(project),
        "declared": declared,
        "manifest": manifest,
        "target": target,
        "behind": declared != target,
        "git": git,
        "branch": cur_branch,
        "default_branch": def_branch,
        "default_version": def_version,
        "default_behind": default_behind,
        "local_patches": patches,
        "patches_upstreamed": bool(acknowledged),
        "blockers": blockers,
    }


# Prefijos de las declaraciones Codex que el doctor exige en el perfil. Viven en
# `_project/`, que el upgrade no copia: sin sembrarlas, todo proyecto Codex que
# salte de versión llega al paso 5 con evidencia incompleta.
CODEX_PROFILE_KEYS = (
    "- Soporte estructural Codex",
    "- Smoke runtime Codex",
    "- Superficies Codex requeridas",
    "- Smoke runtime Codex CLI",
    "- Smoke runtime Codex Desktop/app-server",
    "- Versión de Codex",
    "- Versión de Codex CLI",
    "- Versión de Codex Desktop/app-server",
    "- Modelo Codex smoke",
    "- Esfuerzo Codex smoke",
    "- Hooks Codex",
    "- MCP local `state_manager`",
    "- Diferencias Codex conocidas",
)
AGENTS_CODEX_NEEDLE = "ensure_codex_integration.py --from-profile --native-only"


class ProfileMigrationError(ValueError):
    """Perfil Codex ambiguo o inválido; no se permite escribir a medias."""


CODEX_PROFILE_LABELS = tuple(key[2:] for key in CODEX_PROFILE_KEYS)
CODEX_STATUS_LABELS = {
    "Soporte estructural Codex",
    "Smoke runtime Codex",
    "Smoke runtime Codex CLI",
    "Smoke runtime Codex Desktop/app-server",
}
CODEX_ENUMS = {
    **{label: {"pass", "fail", "unavailable"} for label in CODEX_STATUS_LABELS},
    "Superficies Codex requeridas": {"cli", "desktop", "cli,desktop"},
    "Hooks Codex": {"gestionados", "manuales", "no disponibles"},
    "MCP local `state_manager`": {"conectado", "fallback", "no disponible"},
    "Esfuerzo Codex smoke": {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "[no ejecutado]"},
}
_PROFILE_PLACEHOLDER_RE = re.compile(r"^\s*\[[^\]]*\]\s*$")
_PROFILE_EVIDENCE_PREFIX = "- Evidencia Codex anterior:"


def _profile_label_pattern(label: str) -> re.Pattern[str]:
    return re.compile(
        rf"^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?(?:\s*\([^:\n]*\))?\s*:",
        re.IGNORECASE,
    )


def _profile_line_index(lines: list[str], label: str) -> list[int]:
    pattern = _profile_label_pattern(label)
    return [index for index, line in enumerate(lines) if pattern.match(line)]


def _profile_value_at(line: str) -> str:
    return line.split(":", 1)[1].strip() if ":" in line else ""


def _is_profile_placeholder(value: str) -> bool:
    return not value or bool(_PROFILE_PLACEHOLDER_RE.fullmatch(value))


def _validate_profile_lines(lines: list[str]) -> None:
    duplicate_labels: list[str] = []
    for label in CODEX_PROFILE_LABELS:
        indices = _profile_line_index(lines, label)
        if len(indices) > 1:
            duplicate_labels.append(label)
    if duplicate_labels:
        raise ProfileMigrationError(
            "15_environment_profile.md contiene etiquetas Codex duplicadas: "
            + ", ".join(duplicate_labels)
        )

    invalid: list[str] = []
    for label, accepted in CODEX_ENUMS.items():
        indices = _profile_line_index(lines, label)
        if not indices:
            continue
        value = _profile_value_at(lines[indices[0]]).strip().lower()
        if _is_profile_placeholder(value):
            continue
        if label == "Superficies Codex requeridas":
            values = [part.strip() for part in value.split(",") if part.strip()]
            if not values or any(part not in {"cli", "desktop"} for part in values) or len(values) != len(set(values)):
                invalid.append(f"{label}={value!r}")
        elif value not in accepted:
            invalid.append(f"{label}={value!r}")
    if invalid:
        raise ProfileMigrationError(
            "15_environment_profile.md contiene valores Codex inválidos: "
            + "; ".join(invalid)
        )


def _validate_prior_evidence(lines: list[str]) -> None:
    evidence_lines = [line for line in lines if line.startswith(_PROFILE_EVIDENCE_PREFIX)]
    if len(evidence_lines) > 1:
        raise ProfileMigrationError(
            "15_environment_profile.md contiene más de una evidencia Codex anterior"
        )
    if not evidence_lines:
        return
    raw_evidence = evidence_lines[0][len(_PROFILE_EVIDENCE_PREFIX) :].strip()
    try:
        prior = json.loads(raw_evidence)
    except json.JSONDecodeError as exc:
        raise ProfileMigrationError(
            "Evidencia Codex anterior no contiene JSON válido"
        ) from exc
    if (
        not isinstance(prior, dict)
        or prior.get("schema_version") != 1
        or prior.get("runtime_smoke") not in {"pass", "fail", "unavailable"}
        or not isinstance(prior.get("version"), str)
    ):
        raise ProfileMigrationError(
            "Evidencia Codex anterior debe declarar schema_version=1, runtime_smoke y version"
        )


def _replace_profile_line_value(line: str, value: str) -> str:
    prefix, _, _ = line.partition(":")
    return f"{prefix}: {value}"


def _canonical_profile_line(label: str, value: str) -> str:
    canonical = _template_lines(
        "_project/15_environment_profile.md",
        lambda line, label=label: bool(_profile_label_pattern(label).match(line)),
    )
    return _replace_profile_line_value(canonical[0], value) if canonical else f"- {label}: {value}"


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace a profile atomically while retaining its original mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    original_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, original_mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _template_lines(rel: str, predicate) -> list[str]:
    """Líneas canónicas de la plantilla. Se leen en ejecución, no se codifican
    aquí, para que no se desincronicen del framework."""
    path = ROOT / rel
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if predicate(line)]


def _probe_codex_values(project: Path) -> dict[str, str]:
    """Resuelve los valores del perfil con las sondas reales del framework.

    Nunca se inventan: `codex_runtime_smoke.py --json` publica `structural_support`
    y `runtime_smoke`, y el resto se deduce de artefactos que existen en disco.
    Un valor no verificable queda en `unavailable`/`no disponible`; modelo y
    esfuerzo usan `[no ejecutado]` para que el doctor lo trate como WARN y no
    lo confunda con un esfuerzo verificado."""
    structural = runtime = "unavailable"
    smoke_args = [
        "python3", ".agent/scripts/codex_runtime_smoke.py",
        "--surface", "all", "--json",
    ]
    try:
        smoke = subprocess.run(
            smoke_args,
            cwd=project,
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        smoke = subprocess.CompletedProcess(smoke_args, 1, stdout="", stderr="")
    data: dict = {}
    try:
        parsed = json.loads(smoke.stdout)
        data = parsed if isinstance(parsed, dict) else {}
        structural = data.get("structural_support", "unavailable")
        runtime = data.get("runtime_smoke", "unavailable")
    except (json.JSONDecodeError, AttributeError):
        pass

    version = "no disponible"
    try:
        probe = subprocess.run(
            ["codex", "--version"], capture_output=True, text=True, timeout=15, check=False
        )
        if probe.returncode == 0 and probe.stdout.strip():
            version = probe.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        pass

    native = project / ".codex" / "config.toml"
    connected = native.exists() and "state_manager" in native.read_text(encoding="utf-8", errors="ignore")
    hooks = project / ".codex" / "hooks.json"
    surfaces = data.get("surfaces") if isinstance(data, dict) else {}
    surfaces = surfaces if isinstance(surfaces, dict) else {}
    cli = surfaces.get("cli") if isinstance(surfaces.get("cli"), dict) else {}
    desktop = surfaces.get("desktop") if isinstance(surfaces.get("desktop"), dict) else {}
    required = data.get("required_surfaces") if isinstance(data.get("required_surfaces"), list) else ["cli"]

    return {
        "Soporte estructural Codex": structural,
        "Smoke runtime Codex": runtime,
        "Superficies Codex requeridas": ",".join(str(item) for item in required),
        "Smoke runtime Codex CLI": str(cli.get("status", "unavailable")),
        "Smoke runtime Codex Desktop/app-server": str(desktop.get("status", "unavailable")),
        "Versión de Codex": version,
        "Versión de Codex CLI": str(cli.get("version") or version),
        "Versión de Codex Desktop/app-server": str(desktop.get("version") or "no disponible"),
        "Modelo Codex smoke": str(data.get("model") or "[no ejecutado]"),
        "Esfuerzo Codex smoke": str(data.get("reasoning_effort") or "[no ejecutado]"),
        "Hooks Codex": "gestionados" if hooks.exists() else "no disponibles",
        "MCP local `state_manager`": "conectado" if connected else "fallback",
        "Diferencias Codex conocidas": "ninguna",
    }


def migrate_codex_profile_text(text: str, values: dict[str, str]) -> tuple[str, int]:
    """Migrate legacy aggregate evidence without inheriting certification.

    The transformation is pure so callers can validate the complete result
    before the profile is replaced atomically.  Explicit, non-placeholder
    surface evidence is preserved; an informational probe never downgrades it.
    """
    lines = text.splitlines()
    _validate_profile_lines(lines)
    _validate_prior_evidence(lines)

    existing: dict[str, tuple[int, str]] = {}
    for label in CODEX_PROFILE_LABELS:
        indices = _profile_line_index(lines, label)
        if indices:
            index = indices[0]
            existing[label] = (index, _profile_value_at(lines[index]))

    def value(label: str) -> str | None:
        item = existing.get(label)
        if item is None:
            return None
        raw = item[1].strip()
        return None if _is_profile_placeholder(raw) else raw

    required_raw = value("Superficies Codex requeridas")
    if required_raw is None:
        required = ["cli"]
    else:
        required = [item.strip().lower() for item in required_raw.split(",") if item.strip()]
        if not required or any(item not in {"cli", "desktop"} for item in required) or len(required) != len(set(required)):
            raise ProfileMigrationError(
                "Superficies Codex requeridas debe ser cli, desktop o cli,desktop"
            )

    aggregate_old = value("Smoke runtime Codex")
    explicit_surface_evidence = {
        "cli": value("Smoke runtime Codex CLI"),
        "desktop": value("Smoke runtime Codex Desktop/app-server"),
    }
    legacy_aggregate = not any(item is not None for item in explicit_surface_evidence.values())

    legacy_aggregate_old = aggregate_old
    if legacy_aggregate and aggregate_old == "pass":
        version = value("Versión de Codex") or "no disponible"
        evidence = {
            "schema_version": 1,
            "runtime_smoke": "pass",
            "version": version,
        }
        evidence_line = _PROFILE_EVIDENCE_PREFIX + " " + json.dumps(
            evidence, ensure_ascii=False, separators=(",", ":")
        )
        if not any(line.startswith(_PROFILE_EVIDENCE_PREFIX) for line in lines):
            lines.append(evidence_line)
        # A legacy aggregate pass has no surface proof.  Recompute it from the
        # newly seeded surface statuses rather than silently certifying CLI.
        aggregate_old = None

    effective_values = dict(values)
    effective_values.setdefault("Superficies Codex requeridas", ",".join(required))
    for surface, label in (
        ("cli", "Smoke runtime Codex CLI"),
        ("desktop", "Smoke runtime Codex Desktop/app-server"),
    ):
        current = explicit_surface_evidence[surface]
        if current is not None:
            effective_values[label] = current
        else:
            effective_values.setdefault(label, "unavailable")

    surface_statuses = {
        surface: str(effective_values.get(label, "unavailable"))
        for surface, label in (
            ("cli", "Smoke runtime Codex CLI"),
            ("desktop", "Smoke runtime Codex Desktop/app-server"),
        )
    }
    computed_aggregate = (
        "fail"
        if any(surface_statuses[surface] == "fail" for surface in required)
        else "unavailable"
        if any(surface_statuses[surface] == "unavailable" for surface in required)
        else "pass"
    )
    if aggregate_old == "fail":
        computed_aggregate = "fail"
    effective_values["Smoke runtime Codex"] = computed_aggregate

    # Preserve all real values and fill only missing/placeholders.  Surface
    # evidence is special: legacy aggregate passes are intentionally replaced
    # with unavailable, while explicit pass/fail values remain authoritative.
    touched = 0
    for label in CODEX_PROFILE_LABELS:
        value_to_seed = effective_values.get(label)
        if value_to_seed is None:
            continue
        item = existing.get(label)
        if item is None:
            lines.append(_canonical_profile_line(label, value_to_seed))
            touched += 1
            continue
        index, current_raw = item
        if _is_profile_placeholder(current_raw) or (
            label == "Smoke runtime Codex" and legacy_aggregate and legacy_aggregate_old == "pass"
        ):
            replacement = _replace_profile_line_value(lines[index], value_to_seed)
            if replacement != lines[index]:
                lines[index] = replacement
                touched += 1

    migrated = "\n".join(lines) + "\n"
    # Probe output is external process data.  Validate the resulting profile
    # too, so a malformed JSON response cannot be persisted as a new doctor
    # contradiction.
    _validate_profile_lines(migrated.splitlines())
    _validate_prior_evidence(migrated.splitlines())
    return migrated, touched


def seed_codex_contract(project: Path) -> list[str]:
    """Seed Codex profile fields with a validated, atomic migration."""
    profile = project / "_project" / "15_environment_profile.md"
    if not profile.exists():
        return []
    text = profile.read_text(encoding="utf-8")
    # Validate before running even informational probes.  A corrupt profile is
    # a local contradiction; no subprocess should be needed to discover that
    # it cannot be migrated, and no partial write is ever allowed.
    lines = text.splitlines()
    _validate_profile_lines(lines)
    _validate_prior_evidence(lines)
    values = _probe_codex_values(project)
    migrated, touched = migrate_codex_profile_text(text, values)
    if migrated != text:
        _atomic_write_text(profile, migrated)
    return [f"perfil({touched})"] if touched else []


def seed_agents_contract(project: Path) -> list[str]:
    """`codex_integration` en `doctor_wiring.json` NO tiene guards: exige las
    anclas de activación y confianza de hooks en `AGENTS.md` a TODO proyecto en
    4.24.x, declare Codex o no. Por eso este sembrado es incondicional, a
    diferencia del perfil (FW-171)."""
    agents = project / "AGENTS.md"
    if not agents.exists() or agents.is_symlink():
        return []
    text = agents.read_text(encoding="utf-8")
    if AGENTS_CODEX_NEEDLE in text:
        return []
    canonical = _template_lines("AGENTS.md", lambda line: AGENTS_CODEX_NEEDLE in line)
    if not canonical:
        return []
    agents.write_text(text.rstrip("\n") + "\n" + "\n".join(canonical) + "\n", encoding="utf-8")
    return ["AGENTS.md"]


ROOT_DOCS = ("AGENTS.md", "CLAUDE.md")
_DIFF_MAX_LINES = 60


def _template_managed_body(rel: str) -> str | None:
    """Cuerpo del bloque GESTIONADO de la plantilla (ADR-073); None si no lo tiene."""
    import root_docs

    path = ROOT / rel
    if not path.exists():
        return None
    block = root_docs.find_block(path.read_text(encoding="utf-8"))
    return block.body if block else None


def managed_root_changes(project: Path) -> dict[str, dict]:
    """Qué cambiaría en el bloque gestionado de cada raíz del proyecto.

    Solo mira el bloque: lo de fuera es del proyecto y nunca se toca. Un raíz
    sin bloque (adoptante sin migrar) se informa como `legacy`; uno con
    marcadores malformados o que es un symlink, como `malformed`/`symlink`:
    en ningún caso se escribe."""
    import difflib
    import root_docs

    changes: dict[str, dict] = {}
    for rel in ROOT_DOCS:
        path = project / rel
        body = _template_managed_body(rel)
        if body is None or not path.exists():
            continue
        if path.is_symlink():
            changes[rel] = {"status": "symlink", "new_text": None, "diff": []}
            continue
        text, eol = root_docs.read_raw(path)
        try:
            block = root_docs.find_block(text)
        except root_docs.BlockError as exc:
            changes[rel] = {"status": "malformed", "new_text": None, "diff": [str(exc)]}
            continue
        if block is None:
            changes[rel] = {"status": "legacy", "new_text": None, "diff": []}
            continue
        new_text = root_docs.replace_block(text, body)
        if new_text == text:
            continue
        diff = list(difflib.unified_diff(
            text.splitlines(), new_text.splitlines(), f"{rel} (actual)", f"{rel} (plantilla)", lineterm="", n=1,
        ))
        changes[rel] = {"status": "update", "new_text": root_docs.restore_eol(new_text, eol), "diff": diff}
    return changes


def sync_managed_root_docs(project: Path) -> tuple[list[str], list[str]]:
    """Sustituye SOLO el bloque GESTIONADO de AGENTS.md y CLAUDE.md (ADR-073, D5).

    Reemplaza el sembrado de anclas (`seed_agents_contract`) en los proyectos
    migrados; un raíz sin bloque no se toca aquí (el sembrado de anclas sigue
    cubriéndolo hasta que el builder apruebe `migrate_root_docs.py`).
    Devuelve (ficheros actualizados, diff recortado para el informe)."""
    updated: list[str] = []
    shown: list[str] = []
    for rel, change in managed_root_changes(project).items():
        if change["status"] in ("symlink", "malformed"):
            shown.append(f"AVISO: {rel} es {change['status']}: su bloque GESTIONADO no se actualizó; revisar a mano")
        if change["status"] != "update":
            continue
        _atomic_write_text(project / rel, change["new_text"])
        updated.append(f"{rel}(bloque)")
        shown.append(f"{rel}: bloque GESTIONADO actualizado")
        shown.extend("  " + line for line in change["diff"][:_DIFF_MAX_LINES])
        if len(change["diff"]) > _DIFF_MAX_LINES:
            shown.append(f"  … {len(change['diff']) - _DIFF_MAX_LINES} líneas más")
    return updated, shown


def commit_upgrade(project: Path, report: dict) -> tuple[bool, str]:
    """Cierra el upgrade en un commit propio del proyecto (FW-196).

    Antes se aplicaba y NO se commiteaba, para que el builder revisara el diff.
    La intención era buena y la consecuencia no se cableó: el árbol queda sucio
    y el gate tier_2 de `/actualizar-framework` —correcto— bloquea el SIGUIENTE
    upgrade. Un fallo transitorio se volvía bloqueo persistente, y llevar 9
    adoptantes a 4.31.2 costó 17 commits y 3 corridas de `--apply`, dos de ellas
    fallidas solo por el residuo de la anterior.

    `git add -A` es seguro **por la precondición del gate**: un proyecto solo
    llega aquí con el árbol limpio, así que todo lo sucio es obra del upgrade.
    Se verifica después: si algo queda fuera del commit, se reporta en vez de
    afirmar un cierre que no ocurrió.

    Un fallo al commitear NO revierte el upgrade: el doctor del proyecto ya pasó,
    y lo que suele fallar aquí es el pre-commit del propio producto. Revertir por
    eso sería peor que dejarlo aplicado y decirlo."""
    mensaje = f"chore: actualizar framework {report['declared']} -> {report['target']}"
    add = subprocess.run(["git", "-C", str(project), "add", "-A"], capture_output=True, text=True)
    if add.returncode != 0:
        return False, f"git add falló: {add.stderr.strip()[:120]}"
    commit = subprocess.run(
        ["git", "-C", str(project), "commit", "-q", "-m", mensaje],
        capture_output=True, text=True,
    )
    if commit.returncode != 0:
        salida = [l.strip() for l in (commit.stdout + commit.stderr).splitlines() if l.strip()]
        # La ÚLTIMA línea de un pre-commit del framework es su consejo de FW-099,
        # no la causa: reportarla dejaba un motivo inútil ("registrala AHORA
        # como FW-NNN…") justo cuando hace falta saber qué falló. Se prefiere la
        # primera línea que se parezca a un diagnóstico. Misma clase que
        # `salida-truncada-usada-como-evidencia` del búfer de fricción.
        senales = ("FAIL:", "ERROR:", "Traceback", "failed", "FAILED", "fatal:", "error:")
        causa = next((l for l in salida if any(s in l for s in senales)), None)
        # FW-283: sin venv el hook cae al python3 del sistema y la suite falla por una
        # dependencia ausente; decir cómo arreglarlo en vez de dejar solo el síntoma.
        faltante = next((l for l in salida if "ModuleNotFoundError" in l), None)
        if faltante:
            return False, (
                f"{faltante[:110]} — el hook usó un Python sin las dependencias del proyecto; "
                "exporta FRAMEWORK_PYTHON=<ruta al python del venv> y repite el commit"
            )[:260]
        return False, (causa or (salida[-1] if salida else "commit falló sin mensaje"))[:200]
    resto = subprocess.run(
        ["git", "-C", str(project), "status", "--short"], capture_output=True, text=True,
    ).stdout.strip()
    if resto:
        return False, f"commiteado pero el árbol sigue sucio ({len(resto.splitlines())} ficheros)"
    return True, ""


DEFAULT_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def claude_project_slug(project_path: Path) -> str:
    """Calcula el slug del proyecto para ~/.claude/projects/<slug>/.

    Claude Code sustituye cualquier carácter no alfanumérico por '-' (incluyendo
    '/' y espacios, así como '_' y '.').
    """
    resolved = str(project_path.resolve())
    return re.sub(r"[^a-zA-Z0-9]", "-", resolved)


def _last_message_timestamp_from_transcript(path: Path, tail_bytes: int = 262144) -> float | None:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
            chunk = f.read()
        lines = chunk.decode("utf-8", errors="ignore").splitlines()
        if size > tail_bytes and lines:
            lines.pop(0)
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") in ("user", "assistant"):
                ts = record.get("timestamp")
                if ts is None:
                    continue
                if isinstance(ts, (int, float)):
                    val = float(ts)
                    return val / 1000.0 if val > 1e11 else val
                if isinstance(ts, str):
                    try:
                        clean_ts = ts.replace("Z", "+00:00")
                        dt = datetime.datetime.fromisoformat(clean_ts)
                        return dt.timestamp()
                    except (ValueError, TypeError):
                        continue
    except OSError:
        return None
    return None


_GYCD_COMMAND_RE = re.compile(r"<command-name>\s*/?gycd\s*</command-name>")
_GYCD_TEXT_RE = re.compile(r"(?m)^\s*/gycd\b")


def _message_text(record: dict) -> str:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def transcript_invoked_gycd(path: Path) -> bool:
    """FW-282: ¿algún mensaje del builder en la transcripción invoca ``/gycd``?

    Solo cuenta mensajes de tipo ``user``: una mención dentro de una respuesta o de un
    resultado de herramienta no es una invocación. Ilegible = False (conservador)."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if "gycd" not in line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(record, dict) or record.get("type") != "user":
                    continue
                text = _message_text(record)
                if _GYCD_COMMAND_RE.search(text) or _GYCD_TEXT_RE.search(text):
                    return True
    except OSError:
        return False
    return False


def check_open_claude_session(
    project_path: Path,
    claude_projects_dir: Path | None = None,
    max_age_seconds: float = 3600.0,
) -> tuple[bool, str]:
    """Comprueba si el proyecto tiene una sesión de Claude Code activa (< 60 min).

    FW-253: busca en ~/.claude/projects/<slug>/ si existe algún *.jsonl con mensajes
    de tipo user o assistant en los últimos max_age_seconds (por defecto 1 h). Si la
    transcripción no se puede leer o no contiene mensajes, recurre al mtime
    (comportamiento conservador).
    """
    base_dir = claude_projects_dir or DEFAULT_CLAUDE_PROJECTS_DIR
    if not base_dir.is_dir():
        return False, ""

    slug = claude_project_slug(project_path)
    slug_dir = base_dir / slug
    if not slug_dir.is_dir():
        alt_slug = str(project_path.resolve()).replace("/", "-").replace(" ", "-")
        if alt_slug != slug:
            alt_dir = base_dir / alt_slug
            if alt_dir.is_dir():
                slug_dir = alt_dir
            else:
                return False, ""
        else:
            return False, ""

    now = time.time()
    recent: list[tuple[float, Path]] = []
    try:
        for f in slug_dir.glob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                mtime_age = now - mtime
                if mtime_age >= max_age_seconds:
                    continue
                msg_ts = _last_message_timestamp_from_transcript(f)
                if msg_ts is not None:
                    effective_age = max(0.0, now - msg_ts)
                else:
                    effective_age = mtime_age
                if effective_age < max_age_seconds:
                    recent.append((effective_age, f))
            except OSError:
                continue
    except OSError:
        return False, ""

    if not recent:
        return False, ""

    recent.sort(key=lambda x: x[0])
    youngest_age, youngest_file = recent[0]
    minutos = max(1, int(youngest_age // 60))
    # FW-282: el propio /gycd escribe mensajes al cerrar y disparaba la guarda. H5 protege
    # el coste de caché; la corrección ya la guarda el requisito de árbol limpio.
    closed = transcript_invoked_gycd(youngest_file) and git_state(project_path) == "limpio"
    if closed:
        return False, ""
    sin_gycd = not transcript_invoked_gycd(youngest_file)
    detalle = (
        "sin /gycd en la sesión; ciérrala con /gycd o usa --allow-open-session"
        if sin_gycd else
        "con /gycd pero el árbol tiene cambios sin commitear; commitea o usa --allow-open-session"
    )
    motivo = (
        f"sesión de Claude Code activa detectada ({youngest_file.name}, "
        f"modificado hace {minutos} min en {slug_dir.name}); {detalle}"
    )
    return True, motivo


def apply_upgrade(
    report: dict,
    commit: bool = True,
    allow_open_session: bool = False,
    claude_projects_dir: Path | None = None,
) -> bool:
    """Mecaniza el paso 4 de `/actualizar-framework` con sus guardas.

    Backup primero; si el doctor del proyecto no queda en OK, se restaura y se
    reporta — el workflow prohíbe dejar un proyecto a medias."""
    project = Path(report["path"])
    # H5: no propagar sobre un proyecto con una sesión de Claude Code abierta (< 60 min)
    # porque cambiar las herramientas MCP invalida la caché completa de esa sesión.
    if not allow_open_session:
        has_open, reason = check_open_claude_session(project, claude_projects_dir=claude_projects_dir)
        if has_open:
            print(f"  ! {report['project']}: actualización rechazada — {reason}")
            return False
    # El backup vive FUERA del árbol: dentro ensuciaba el repo y, tras un fallo,
    # el propio guard de árbol limpio bloqueaba el reintento (FW-171).
    staging = Path(tempfile.mkdtemp(prefix=f"fw-upgrade-{project.name}-"))
    backup = staging / "agent"
    shutil.copytree(project / ".agent", backup)
    # Upgrade steps also regenerate surfaces outside `.agent/` and install the
    # repository hook.  Keep an exact, out-of-tree copy of every one so a
    # failed doctor, profile migration or state mutation restores the project
    # byte-for-byte, including pre-existing Codex/Claude customizations.
    external_backup = staging / "external"
    external_paths = (
        "_project",
        "AGENTS.md",
        "CLAUDE.md",
        ".claude",
        ".agents",
        ".opencode",
        ".codex",
        "GEMINI.md",
        ".git/hooks",
        "tests",
    )
    external_present: set[str] = set()
    for rel in external_paths:
        source = project / rel
        if not source.exists() and not source.is_symlink():
            continue
        external_present.add(rel)
        destination = external_backup / rel
        if source.is_dir() and not source.is_symlink():
            shutil.copytree(source, destination, symlinks=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)
    # El sembrado toca ficheros fuera de `.agent/`; sin copiarlos, un rollback
    # dejaría el proyecto a medias — lo que el paso 5 del workflow prohíbe.
    seeded_originals = {}
    for rel in ("_project/15_environment_profile.md", "AGENTS.md", "CLAUDE.md", ".claude/settings.json"):
        source = project / rel
        if source.exists():
            seeded_originals[rel] = source.read_bytes()  # bytes: CRLF y symlinks no se normalizan al revertir
    # FW-232: el rollback existe ANTES de tocar nada. Antes se definía después
    # del copiado, así que un fallo o un Ctrl+C durante la copia dejaba `.agent`
    # a medias, el árbol sucio y el backup huérfano en /tmp.
    rollback = _UpgradeRollback(
        project=project,
        staging=staging,
        backup=backup,
        external_backup=external_backup,
        external_paths=external_paths,
        external_present=external_present,
        seeded_originals=seeded_originals,
        # FW-204: si el seed de `.claude/settings.json` lo CREA de cero, el
        # rollback debe borrarlo (no está en seeded_originals para restaurar).
        claude_settings_existed=(project / ".claude" / "settings.json").exists(),
    )
    try:
        return _apply_after_backup(report, commit, rollback)
    except BaseException as exc:
        if not rollback.attempted:
            _fail(report, rollback, f"error inesperado durante el upgrade: {exc!r}")
        if not isinstance(exc, Exception):
            raise  # KeyboardInterrupt / SystemExit: restaurado, pero el operador manda
        return False


class _UpgradeRollback:
    """Devuelve el proyecto al estado previo al upgrade y verifica que lo logró.

    FW-232: el rollback anterior no devolvía nada, así que un rollback Codex
    fallido acababa impreso como "restaurado, sin residuo". Ahora `restore()`
    devuelve `(verificado, detalle)` y solo borra el backup si verificó; en otro
    caso el backup queda intacto en `staging` y el mensaje lo nombra.
    """

    def __init__(self, *, project, staging, backup, external_backup, external_paths,
                 external_present, seeded_originals, claude_settings_existed):
        self.project = project
        self.staging = staging
        self.backup = backup
        self.external_backup = external_backup
        self.external_paths = external_paths
        self.external_present = external_present
        self.seeded_originals = seeded_originals
        self.claude_settings_existed = claude_settings_existed
        self.test_originals: dict[str, bytes] = {}
        self.untracked_before_sync: list[Path] = []
        self.attempted = False

    def _restore_files(self) -> None:
        project = self.project
        if (project / ".agent").exists():
            shutil.rmtree(project / ".agent")
        shutil.copytree(self.backup, project / ".agent")
        for rel in reversed(self.external_paths):
            target = project / rel
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            if rel not in self.external_present:
                continue
            source = self.external_backup / rel
            if source.is_dir() and not source.is_symlink():
                shutil.copytree(source, target, symlinks=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=False)
        for rel, original in self.seeded_originals.items():
            target = project / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()
            target.write_bytes(original if isinstance(original, bytes) else original.encode("utf-8"))
        for rel, raw in self.test_originals.items():
            target = project / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        if not self.claude_settings_existed and (project / ".claude" / "settings.json").exists():
            (project / ".claude" / "settings.json").unlink()
        # Las superficies generadas ya fueron restauradas desde la copia exacta
        # de ``external_backup``; no se vuelve a ejecutar el sincronizador sobre
        # un árbol que puede contener personalizaciones legítimas.
        for created in self.untracked_before_sync:
            if created.is_dir():
                shutil.rmtree(created)
            elif created.exists():
                created.unlink()

    def _residue(self) -> str | None:
        """El upgrade solo arranca con el árbol limpio: cualquier resto es suyo."""
        status = subprocess.run(
            ["git", "-C", str(self.project), "status", "--porcelain"],
            capture_output=True, text=True,
        )
        if status.returncode != 0:
            return f"no se pudo verificar el árbol: {status.stderr.strip()[:120]}"
        lines = [line for line in status.stdout.splitlines() if line.strip()]
        if lines:
            return f"el árbol sigue sucio ({len(lines)} rutas, p. ej. {lines[0][3:]})"
        return None

    def restore(self) -> tuple[bool, str]:
        self.attempted = True
        problems: list[str] = []
        try:
            self._restore_files()
        except Exception as exc:  # noqa: BLE001 — se reporta, el backup se conserva
            problems.append(f"restauración de ficheros incompleta: {exc!r}")
        if not problems:
            residue = self._residue()
            if residue:
                problems.append(residue)
        if problems:
            return False, "; ".join(problems)
        shutil.rmtree(self.staging, ignore_errors=True)
        return True, ""


def _fail(report: dict, rollback: _UpgradeRollback, motivo: str,
          detalles: list[str] | tuple[str, ...] = ()) -> bool:
    """Revierte e informa la verdad: restaurado verificado o dónde está el backup."""
    restored, detalle = rollback.restore()
    if restored:
        print(f"  ✗ {report['project']}: {motivo} — restaurado, sin residuo")
    else:
        print(
            f"  ✗ {report['project']}: {motivo} — NO restaurado del todo ({detalle}). "
            f"Backup intacto en {rollback.staging}"
        )
    for linea in detalles:
        print(f"      {linea}")
    return False


def _aviso_subproceso(nombre: str, result: subprocess.CompletedProcess) -> str | None:
    """Un paso auxiliar fallido no aborta el upgrade (el doctor decide), pero se ve."""
    if result.returncode == 0:
        return None
    salida = [l.strip() for l in (result.stdout + result.stderr).splitlines() if l.strip()]
    return f"AVISO: {nombre} salió con {result.returncode}: {salida[-1] if salida else 'sin detalle'}"


def _detect_engine_imports_in_product_tests(project: Path) -> list[str]:
    """FW-254: detecta tests bajo tests/ del producto que importan el motor

    (state_core, workflow_receipts, state_queries o replay_state_log) y que no
    viajan con el upgrade (no son copias idénticas de ROOT/tests/).
    """
    engine_modules = {"state_core", "workflow_receipts", "state_queries", "replay_state_log"}
    framework_test_names = {f.name for f in (ROOT / "tests").glob("*.py")}
    tests_dir = project / "tests"
    if not tests_dir.is_dir():
        return []
    findings = []
    for py_path in sorted(tests_dir.rglob("*.py")):
        if py_path.parent == tests_dir and py_path.name in framework_test_names:
            continue
        try:
            content = py_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        imported = set()
        try:
            import ast
            tree = ast.parse(content, filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top in engine_modules:
                            imported.add(top)
                elif isinstance(node, ast.ImportFrom):
                    mod = (node.module or "").split(".")[-1]
                    if mod in engine_modules:
                        imported.add(mod)
        except SyntaxError:
            for mod in engine_modules:
                if re.search(rf"\b(?:import\s+|from\s+[\w\.]*){mod}\b", content):
                    imported.add(mod)
        if imported:
            rel = str(py_path.relative_to(project))
            findings.append(f"{rel} ({', '.join(sorted(imported))})")
    return findings


def _preserve_product_workflows(project: Path, backup_agent: Path) -> list[str]:
    """Preserva workflows propios del producto declarados en `manifest.json`.

    `overwrite_dirs()` recrea `.agent/workflows/` desde la plantilla. Si un
    proyecto adoptante declaró un workflow específico del dominio en su
    `manifest.json` (cuyo nombre y fichero `.agent/workflows/*.md` no existen en
    la plantilla, p. ej. `cuadrar-cuenta.md` en Research App), se restaura
    antes de ejecutar `sync_claude_commands.py` para que sus wrappers sigan
    generándose y los tests del producto no se rompan.
    """
    old_manifest_path = backup_agent / "workflows" / "manifest.json"
    new_manifest_path = project / ".agent" / "workflows" / "manifest.json"
    if not old_manifest_path.is_file() or not new_manifest_path.is_file():
        return []
    try:
        old_data = json.loads(old_manifest_path.read_text(encoding="utf-8"))
        new_data = json.loads(new_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    old_cmds = old_data.get("commands")
    new_cmds = new_data.get("commands")
    if not isinstance(old_cmds, list) or not isinstance(new_cmds, list):
        return []
    template_names = {c.get("name") for c in new_cmds if isinstance(c, dict) and c.get("name")}
    template_files = {p.name for p in (ROOT / ".agent" / "workflows").iterdir() if p.is_file()}
    preserved: list[str] = []
    for entry in old_cmds:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        source = entry.get("source")
        if not isinstance(name, str) or not name or name in template_names:
            continue
        if not isinstance(source, str) or not source.startswith(".agent/workflows/"):
            continue
        rel_in_workflows = Path(source).relative_to(".agent/workflows")
        if len(rel_in_workflows.parts) != 1 or rel_in_workflows.name in template_files:
            continue
        old_wf = backup_agent / "workflows" / rel_in_workflows.name
        if not old_wf.is_file() or old_wf.is_symlink():
            continue
        shutil.copy2(old_wf, project / ".agent" / "workflows" / rel_in_workflows.name)
        new_cmds.append(entry)
        template_names.add(name)
        preserved.append(rel_in_workflows.name)
    if preserved:
        new_manifest_path.write_text(
            json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return preserved


def _apply_after_backup(report: dict, commit: bool, rollback: _UpgradeRollback) -> bool:
    """Pasos 4-6 del upgrade, ya con backup. Toda salida con fallo pasa por `_fail`."""
    project = rollback.project
    avisos: list[str] = []
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    for name in overwrite_dirs():
        source, dest = ROOT / ".agent" / name, project / ".agent" / name
        if not source.is_dir():
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest, ignore=ignore)
    _preserve_product_workflows(project, rollback.backup)
    # Skills core sin `--delete`: preserva imported/ y domain/ del producto.
    # `mkdir` porque un proyecto puede no tener aún la carpeta: sin esto el
    # copiado reventaba a mitad del upgrade, con los directorios ya
    # sobrescritos, dejando el proyecto a medias.
    skills_dest = project / ".agent/skills"
    skills_dest.mkdir(parents=True, exist_ok=True)
    for skill in (ROOT / ".agent/skills").glob("*.md"):
        shutil.copy2(skill, skills_dest / skill.name)

    # FW-154: sincronizar SOLO los tests del framework que el proyecto ya tenga.
    # Un test del framework desactualizado prueba gates que ya no existen y pone
    # roja la suite del producto por el framework.
    # FW-197: el rollback prometía "sin residuo" y no restauraba estos ficheros.
    # `.agent/` se respalda entero, pero los tests del framework viven FUERA de
    # esa carpeta: un upgrade fallido dejaba `tests/` con contenido de la versión
    # nueva sobre un proyecto revertido. En `example-downloader` sobrevivieron
    # 99 líneas y su árbol sucio bloqueó el siguiente upgrade.
    synced_tests = 0
    for test in (ROOT / "tests").glob("*.py"):
        target = project / "tests" / test.name
        if target.is_file():
            rollback.test_originals[str(target.relative_to(project))] = target.read_bytes()
            shutil.copy2(test, target)
            synced_tests += 1

    # Regenerar wrappers (paso 4). Sin esto, un proyecto que declara Codex llega
    # al paso 5 en FAIL pidiendo .codex/config.toml y las declaraciones del
    # perfil, sin haber hecho nada mal — la regla de clase de FW-162.
    # Detección con el módulo canónico del framework, no con una regex propia.
    # Una regex casera ya discrepó dos veces de lo que el doctor considera un
    # proyecto Codex — y cada discrepancia es un upgrade que falla en el paso 5
    # sin que el proyecto haya hecho nada mal (FW-171). `Boton` declara
    # `- Herramienta: Codex` como ítem de lista; la regex exigía `**`.
    is_codex = False
    try:
        sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
        import codex_profile
        is_codex = codex_profile.detect_primary_harness(project).get("status") == "codex"
    except (ImportError, OSError, ValueError):
        is_codex = False
    sync_args = ["python3", ".agent/scripts/sync_claude_commands.py"]
    # `--codex-only` deliberadamente no toca OpenCode/Claude/docs raíz (ver
    # sync_codex_only). El doctor, en cambio, exige esas superficies frescas
    # sin excepción cuando ya existen (validate_opencode_skills,
    # validate_root_docs_freshness no miran is_codex). Un proyecto Codex que
    # YA tiene `.opencode/` heredado quedaría bloqueado para siempre: cada
    # upgrade saltaría su sincronización y el doctor siguiente la exigiría de
    # todos modos. Solo se salta OpenCode cuando el proyecto de verdad no lo
    # usa (sin `.opencode/` preexistente).
    if is_codex and not (project / ".opencode").exists():
        sync_args.append("--codex-only")
    # Directorios de wrappers que aún no existen: si el upgrade falla hay que
    # borrarlos, porque el sync no los quita al regenerar.
    rollback.untracked_before_sync = [
        project / rel
        for rel in (
            ".claude", ".claude/agents", ".claude/rules", ".agents", ".opencode", ".opencode/skills",
            ".agents/skills", ".codex", "GEMINI.md",
        )
        if not (project / rel).exists()
    ]
    # FW-232: estos pasos no abortan por sí mismos —el doctor de después es la
    # autoridad, y convertirlos en bloqueo añadiría un gate sin evidencia—, pero
    # su fallo ya no es invisible: viaja como aviso en el informe.
    avisos.append(_aviso_subproceso(
        "sync_claude_commands.py", subprocess.run(sync_args, cwd=project, capture_output=True, text=True),
    ))
    if is_codex:
        avisos.append(_aviso_subproceso("ensure_codex_integration.py", subprocess.run(
            ["python3", ".agent/scripts/ensure_codex_integration.py", "--from-profile", "--native-only"],
            cwd=project, capture_output=True, text=True,
        )))

    # Reinstalar el hook de git. Hasta 5.3.1 el hook instalado era una COPIA de
    # `.agent/scripts/pre_commit.sh`; desde L2.3 es un lanzador que ejecuta el
    # script del árbol, y este paso migra la copia antigua (TL-FW-320). El instalador
    # resuelve el destino con `git rev-parse --git-path` (FW-207), se niega a pisar
    # un hook manual y hace backup de uno gestionado por el framework, así que es
    # seguro invocarlo siempre (FW-181). Negarse a pisar un hook manual sale con
    # 1 y es legítimo: se avisa, no se revierte.
    if (project / ".git").exists():
        avisos.append(_aviso_subproceso("pre_commit.sh install", subprocess.run(
            ["bash", ".agent/scripts/pre_commit.sh", "install"],
            cwd=project, capture_output=True, text=True,
        )))

    # El sembrado va DESPUÉS del cableado: las sondas miden el proyecto ya
    # configurado. Al revés medirían un proyecto sin `.codex/config.toml` y
    # declararían `fail` un soporte que sí funciona.
    try:
        seeded = seed_codex_contract(project) if is_codex else []
        managed, managed_diff = sync_managed_root_docs(project)
        seeded += managed
        avisos.extend(managed_diff)
        if managed:  # el bloque trae los GENERADO de la plantilla: regenerarlos con el manifest del proyecto
            avisos.append(_aviso_subproceso("sync tras el bloque", subprocess.run(sync_args, cwd=project, capture_output=True, text=True)))
        seeded += seed_agents_contract(project)
        # FW-204: `.claude/settings.json` lleva el hook del Production Command Guard y
        # el doctor lo exige en `initialized`. `apply_upgrade` copia `.agent/`, así
        # que la copia del proyecto de `ensure_claude_settings.py` ya está disponible.
        cs = subprocess.run(
            ["python3", ".agent/scripts/ensure_claude_settings.py"],
            cwd=project, capture_output=True, text=True,
        )
        if cs.returncode == 0 and "sin cambios" not in cs.stdout:
            seeded.append(".claude/settings.json")
    except ProfileMigrationError as exc:
        return _fail(report, rollback, "perfil Codex inválido durante el upgrade", [str(exc)])
    except (OSError, UnicodeError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        return _fail(report, rollback, "sembrado/configuración falló durante el upgrade", [str(exc)])
    except Exception as exc:
        # A helper regression must not leak a partially seeded checkout.  Keep
        # the rollback boundary broad around this atomic step while preserving
        # KeyboardInterrupt/SystemExit for the operator.
        return _fail(report, rollback, "error inesperado durante el sembrado", [str(exc)])

    avisos = [aviso for aviso in avisos if aviso]

    doctor = subprocess.run(
        ["python3", ".agent/scripts/framework_doctor.py"],
        cwd=project, capture_output=True, text=True,
    )
    if doctor.returncode != 0:
        errores = [line.strip() for line in doctor.stdout.splitlines() if "ERROR" in line]
        return _fail(report, rollback, "doctor FALLA tras el upgrade", avisos + errores)

    # Paso 6: `_project/` no se toca salvo framework_version, y vía el motor.
    mutate = subprocess.run(
        ["python3", ".agent/scripts/state_manager.py", "mutate",
         "--action", "set_framework_version",
         "--payload", json.dumps({"version": report["target"]})],
        cwd=project, capture_output=True, text=True,
    )
    if mutate.returncode != 0:
        return _fail(
            report, rollback, "set_framework_version falló tras el doctor",
            avisos + [mutate.stderr.strip()[:160]],
        )

    shutil.rmtree(rollback.staging, ignore_errors=True)
    detalle = (f"doctor OK, {synced_tests} tests, wrappers "
               f"{'codex' if is_codex else 'claude'}"
               f"{', sembrado: ' + '+'.join(seeded) if seeded else ''}")
    if commit:
        cerrado, motivo = commit_upgrade(project, report)
        detalle += ", commiteado" if cerrado else f", SIN COMMITEAR ({motivo})"
    else:
        detalle += ", sin commitear (--no-commit)"
    engine_warnings = _detect_engine_imports_in_product_tests(project)
    if engine_warnings:
        avisos.append(
            f"AVISO: tests del producto que dependen del motor ({', '.join(engine_warnings)}): "
            f"estas copias no viajan con el upgrade; revisarlos"
        )
    print(f"  ✓ {report['project']}: {report['declared']} → {report['target']} ({detalle})")
    for aviso in avisos:
        print(f"      {aviso}")
    return True


def own_framework_mode() -> str | None:
    """``framework_mode`` del repo donde vive este script; ``None`` si no se puede leer."""
    try:
        state = json.loads((ROOT / "_project" / "03_project_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mode = state.get("framework_mode") if isinstance(state, dict) else None
    return mode if isinstance(mode, str) and mode else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--search-root", action="append", type=Path,
                        help="dónde buscar proyectos (repetible; por defecto, el padre de la plantilla)")
    parser.add_argument("--json", action="store_true", help="salida JSON en vez de tabla")
    parser.add_argument("--apply", action="store_true", help="aplicar el upgrade a los proyectos sin bloqueos")
    parser.add_argument("--yes", action="store_true", help="confirmación explícita, obligatoria con --apply")
    parser.add_argument(
        "--no-commit", action="store_true",
        help="aplicar sin cerrar el upgrade en un commit. Por defecto SÍ se commitea: "
             "un árbol sucio bloquea el siguiente upgrade por el gate tier_2 (FW-196). "
             "Usar solo para revisar el diff antes de cerrarlo — y cerrarlo después.",
    )
    parser.add_argument(
        "--skip", action="append", default=[], metavar="PROYECTO",
        help="excluye PROYECTO de la propagación aunque esté listo. Repetible. "
             "Para proyectos en los que se está trabajando ahora mismo: la exclusión "
             "queda declarada y visible en el informe, no depende de que el árbol "
             "esté sucio por casualidad.",
    )
    parser.add_argument(
        "--patches-upstreamed", action="append", default=[], metavar="PROYECTO",
        help="declara que los parches locales de PROYECTO ya están en la plantilla, "
             "así que el upgrade no pierde nada. Deja constancia en la salida. "
             "Repetible. No debilita el gate para los demás proyectos.",
    )
    parser.add_argument(
        "--allow-open-session", action="store_true",
        help="permite aplicar el upgrade aunque se detecte una sesión activa de Claude Code "
             "en el proyecto (< 60 min). Por defecto se rechaza para no invalidar la caché MCP (H5).",
    )
    parser.add_argument(
        "--claude-projects-dir", type=Path, default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    # FW-284: la copia del propagador de un adoptante busca proyectos solo junto a ella y
    # no ve los commits `release:` de la plantilla; su diagnóstico («BLOQUEADO») engaña.
    own_mode = own_framework_mode()
    if own_mode and own_mode != "template":
        print("propagate_framework: esto es la copia de un proyecto (framework_mode: "
              f"{own_mode}); ejecútalo desde la plantilla del framework", file=sys.stderr)
        return 2

    if args.apply and not args.yes:
        print("propagate_framework: --apply exige --yes (escribe en repos de producto)", file=sys.stderr)
        return 2

    target = template_version()
    roots = args.search_root or [ROOT.parent]
    upstreamed = frozenset(args.patches_upstreamed)
    omitidos = frozenset(args.skip)
    found = discover([r.resolve() for r in roots])
    nombres = {p.name for p in found}
    desconocidos = upstreamed - nombres
    if desconocidos:
        print(f"propagate_framework: --patches-upstreamed nombra proyectos no encontrados: "
              f"{', '.join(sorted(desconocidos))}", file=sys.stderr)
        return 2
    desconocidos = omitidos - nombres
    if desconocidos:
        print(f"propagate_framework: --skip nombra proyectos no encontrados: "
              f"{', '.join(sorted(desconocidos))}", file=sys.stderr)
        return 2
    reports = [inspect(p, target, upstreamed) for p in found]
    for r in reports:
        r["skipped"] = r["project"] in omitidos

    if args.json:
        print(json.dumps({"target": target, "projects": reports}, indent=2, ensure_ascii=False))
    else:
        print(f"Plantilla: {target}\n")
        print(f"{'proyecto':<26} {'versión':<9} {'git':<11} estado")
        print("-" * 76)
        for r in reports:
            if r["skipped"]:
                estado = "OMITIDO (--skip)"
            elif not r["behind"]:
                if r.get("default_behind"):
                    estado = "rama por defecto atrasada"
                else:
                    estado = "al día"
            elif r["blockers"]:
                estado = "BLOQUEADO"
            elif r["patches_upstreamed"]:
                estado = "listo (parches declarados upstream)"
            else:
                estado = "listo para upgrade"
            if r.get("branch") and r.get("default_branch") and r["branch"] != r["default_branch"]:
                def_ver = r.get("default_version") or "<sin versión>"
                estado += f" (rama {r['branch']}; {r['default_branch']} en {def_ver})"
            print(f"{r['project']:<26} {str(r['declared']):<9} {r['git']:<11} {estado}")
        for r in reports:
            if r["patches_upstreamed"]:
                print(f"\n{r['project']}: se ASUME que estos parches ya están en la plantilla "
                      f"(declarado con --patches-upstreamed):")
                for f in r["local_patches"]:
                    print(f"  - {f}")
            if r["blockers"]:
                print(f"\n{r['project']}:")
                for b in r["blockers"]:
                    print(f"  - {b}")
        for r in reports:
            if r["behind"] and not r["blockers"] and not r["skipped"]:
                for rel, change in managed_root_changes(Path(r["path"])).items():
                    if change["status"] in ("symlink", "malformed"):
                        print(f"\n{r['project']}: {rel} {change['status']}: el bloque GESTIONADO no se tocará; "
                              f"revisar a mano ({' '.join(change['diff'])[:120]})")
                    elif change["status"] == "legacy":
                        print(f"\n{r['project']}: {rel} sin bloque GESTIONADO; migrar con "
                              f"`python3 .agent/scripts/migrate_root_docs.py --project {r['path']}` (propuesta con diff)")
                    else:
                        print(f"\n{r['project']}: {rel} — el bloque GESTIONADO cambiaría:")
                        for line in change["diff"][:_DIFF_MAX_LINES]:
                            print("  " + line)
        # Fuera del else: --json rompería su propio contrato de "solo JSON en
        # stdout" — un clon superficial (actions/checkout con fetch-depth por
        # defecto, o cualquier --search-root sin el commit de release aún) lo
        # dispara siempre, y json.loads(stdout) de un consumidor revienta con
        # "Extra data" al encontrar este texto tras el objeto JSON.
        if missing_release_marker(target):
            print(
                f"\n⚠ La plantilla está en {target} y no existe el commit "
                f"'release: {target} -- ...'. Cualquier proyecto que adopte esta "
                f"versión quedará BLOQUEADO hasta crearla. Crear con: "
                f'git commit --allow-empty -m "release: {target} -- <descripción>".'
            )

    behind = [r for r in reports if r["behind"]]
    if args.apply:
        omitidos_atrasados = [r for r in behind if r["skipped"]]
        ready = [r for r in behind if not r["skipped"] and not r["blockers"]]
        bloqueados = len(behind) - len(ready) - len(omitidos_atrasados)
        print(f"\nAplicando a {len(ready)} de {len(behind)} proyectos atrasados...")
        ok = 0
        for r in ready:
            # FW-232: un fallo inesperado en un proyecto (antes o fuera de la
            # frontera de rollback) no puede cortar la propagación de los demás.
            try:
                if apply_upgrade(
                    r, commit=not args.no_commit,
                    allow_open_session=args.allow_open_session,
                    claude_projects_dir=args.claude_projects_dir,
                ):
                    ok += 1
            except Exception as exc:  # noqa: BLE001 — se reporta y se sigue
                print(f"  ✗ {r['project']}: error inesperado fuera del upgrade — {exc!r}")
        sucios = [r for r in ready
                  if subprocess.run(["git", "-C", r["path"], "status", "--short"],
                                    capture_output=True, text=True).stdout.strip()]
        resumen = f"\n{ok}/{len(ready)} actualizados; {bloqueados} bloqueados sin tocar"
        if sucios:
            resumen += (f"; {len(sucios)} con el árbol SUCIO "
                        f"({', '.join(sorted(r['project'] for r in sucios))}) — "
                        f"cerrarlos antes del próximo upgrade o el gate tier_2 los bloqueará")
        if omitidos_atrasados:
            resumen += (f"; {len(omitidos_atrasados)} omitidos por --skip "
                        f"({', '.join(sorted(r['project'] for r in omitidos_atrasados))})")
        print(resumen + ".")
        return 0 if ok == len(ready) else 1

    return 1 if behind else 0


if __name__ == "__main__":
    sys.exit(main())
