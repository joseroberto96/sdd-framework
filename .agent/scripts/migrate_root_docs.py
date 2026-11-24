#!/usr/bin/env python3
"""Migración inicial de AGENTS.md y CLAUDE.md de un adoptante (ADR-073, D4–D5).

Modo propuesta por defecto: calcula el resultado, escribe el diff a un archivo y
NO toca el proyecto. Solo ``--apply`` escribe, y solo ``AGENTS.md``/``CLAUDE.md``.

Qué hace, para un adoptante que aún tiene la raíz anterior a 5.2.3:

- Mueve lo propio del proyecto (el texto de su ``CLAUDE.md`` anterior al
  protocolo del framework: Stack, Convenciones, Arquitectura, Restricciones y
  reglas de dominio) a la zona del proyecto de ``AGENTS.md``, sin reescribirlo.
- Inserta el bloque ``GESTIONADO: framework`` de la plantilla en ``AGENTS.md`` y
  en ``CLAUDE.md`` (que queda en ``@AGENTS.md`` más las especificidades de
  Claude Code) y descarta el texto antiguo del framework.
- Retira de la zona del proyecto las órdenes muertas de una lista cerrada
  (``root_docs.DEAD_PHRASES``) y las lista en el informe.
- Lo que ``CLAUDE.md`` tuviera después del protocolo del framework se conserva,
  fuera del bloque.

Si no puede separar con seguridad lo propio de lo del framework (sin sección del
protocolo o sin su bloque GENERADO final, marcadores GESTIONADO malformados,
symlinks, zona propia de CLAUDE.md con AGENTS.md ya migrado), no propone nada y
lo dice: nunca adivina. Es idempotente: una segunda pasada no cambia nada.
Se ejecuta desde la plantilla, no desde el proyecto.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import root_docs  # noqa: E402

FRAMEWORK_HEADING_RE = re.compile(r"(?m)^## Framework v4\b")
END_GENERATED_RE = re.compile(r"<!-- FIN GENERADO: maquinaria-interna -->[ \t]*\n?")
IMPORT_LINE = "@AGENTS.md"


class MigrationError(Exception):
    """El proyecto no se puede migrar con seguridad."""


def template_bodies() -> tuple[str, str]:
    """Cuerpos GESTIONADOS de la plantilla para AGENTS.md y CLAUDE.md."""
    bodies = []
    for rel in ("AGENTS.md", "CLAUDE.md"):
        path = ROOT / rel
        try:
            block = root_docs.find_block(path.read_text(encoding="utf-8")) if path.exists() else None
        except root_docs.BlockError as exc:
            raise MigrationError(f"la plantilla tiene {rel} malformado: {exc}") from exc
        if block is None:
            raise MigrationError(f"la plantilla no tiene el bloque GESTIONADO en {rel}; ejecuta desde la plantilla 5.2.3+")
        bodies.append(block.body)
    return bodies[0], bodies[1]


def _outside_block(text: str) -> tuple[str, str]:
    """(antes, después) del bloque gestionado de un texto que lo tiene."""
    block = root_docs.find_block(text)
    assert block is not None
    return text[: block.start], text[block.end :]


def split_legacy_claude(text: str) -> tuple[str, str]:
    """(zona del proyecto, cola propia) de un CLAUDE.md anterior a 5.2.3.

    La zona del proyecto es lo anterior a ``## Framework v4`` (sin la regla
    ``---`` que lo separaba); la cola es lo posterior al bloque GENERADO final."""
    heading = FRAMEWORK_HEADING_RE.search(text)
    if heading is None:
        raise MigrationError("CLAUDE.md no tiene la sección «## Framework v4»: no se puede separar lo propio de lo del framework")
    zone = re.sub(r"\n-{3,}[ \t]*\Z", "", text[: heading.start()].rstrip()).rstrip() + "\n"
    ends = list(END_GENERATED_RE.finditer(text, heading.end()))
    if not ends:
        raise MigrationError(
            "CLAUDE.md tiene «## Framework v4» pero no el bloque GENERADO que cierra el protocolo: "
            "no se puede saber dónde acaba lo del framework"
        )
    return zone, text[ends[-1].end():].strip("\n")


def compute(project: Path) -> dict:
    """Calcula el resultado de la migración sin escribir nada."""
    agents_path, claude_path = project / "AGENTS.md", project / "CLAUDE.md"
    for path in (agents_path, claude_path):
        if path.is_symlink():
            raise MigrationError(
                f"{path.name} es un symlink: escribir a través de él puede pisar el otro archivo o salir del proyecto; "
                "reemplázalo por un archivo real (CLAUDE.md con `@AGENTS.md`) y reintenta"
            )
    old_agents, agents_eol = root_docs.read_raw(agents_path) if agents_path.exists() else ("", "\n")
    old_claude, claude_eol = root_docs.read_raw(claude_path) if claude_path.exists() else ("", "\n")
    body_agents, body_claude = template_bodies()
    removed: list[str] = []
    notes: list[str] = []

    try:
        agents_block = root_docs.find_block(old_agents)
        claude_block = root_docs.find_block(old_claude)
    except root_docs.BlockError as exc:
        raise MigrationError(str(exc)) from exc
    if agents_block is not None and claude_block is None and old_claude.strip():
        zone = split_legacy_claude(old_claude)[0] if FRAMEWORK_HEADING_RE.search(old_claude) else old_claude
        if zone.strip():
            raise MigrationError(
                "AGENTS.md ya está migrado pero CLAUDE.md conserva contenido propio anterior al protocolo del framework; "
                "muévelo a mano a la zona del proyecto de AGENTS.md y reintenta"
            )

    # --- AGENTS.md ---
    if agents_block is not None:
        before, after = _outside_block(old_agents)
        before, gone = root_docs.strip_dead_lines(before)
        after, gone2 = root_docs.strip_dead_lines(after)
        removed += gone + gone2
        new_agents = before + root_docs.render_block(body_agents) + after
    else:
        if not old_claude.strip():
            raise MigrationError("no hay CLAUDE.md del que sacar lo propio del proyecto")
        if claude_block is not None:
            zone_source = _outside_block(old_claude)[0]
        else:
            zone_source, _ = split_legacy_claude(old_claude)
        zone, gone = root_docs.strip_dead_lines(zone_source)
        removed += gone
        if not zone.strip():
            raise MigrationError("la zona del proyecto de CLAUDE.md quedó vacía")
        new_agents = zone.rstrip("\n") + "\n\n" + root_docs.render_block(body_agents) + "\n"
        notes.append("AGENTS.md: se descarta el texto anterior del framework (el diff lo muestra)")

    # --- CLAUDE.md ---
    if claude_block is not None:
        before, after = _outside_block(old_claude)
        if agents_block is None:
            # La zona propia ya se movió a AGENTS.md: se quita de aquí para no duplicarla.
            before = ""
        before, gone = root_docs.strip_dead_lines(before)
        after, gone2 = root_docs.strip_dead_lines(after)
        removed += gone + gone2
        if IMPORT_LINE not in before and IMPORT_LINE not in after:
            before = IMPORT_LINE + "\n\n" + before.lstrip("\n")
        new_claude = before + root_docs.render_block(body_claude) + after
    else:
        _, tail = split_legacy_claude(old_claude) if old_claude.strip() else ("", "")
        tail, gone = root_docs.strip_dead_lines(tail)
        removed += gone
        new_claude = IMPORT_LINE + "\n\n" + root_docs.render_block(body_claude) + "\n"
        if tail.strip():
            new_claude += "\n" + tail.strip("\n") + "\n"
            notes.append("CLAUDE.md: se conserva, fuera del bloque, lo que había tras el protocolo del framework")

    results = {}
    for rel, old, new in (("AGENTS.md", old_agents, new_agents), ("CLAUDE.md", old_claude, new_claude)):
        diff = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(), f"{rel} (actual)", f"{rel} (propuesto)", lineterm="", n=2,
        ))
        eol = agents_eol if rel == "AGENTS.md" else claude_eol
        results[rel] = {"old": old, "new": new, "eol": eol, "changed": old != new, "diff": diff}
    return {"files": results, "removed_lines": removed, "notes": notes}


def _git_unsafe(project: Path) -> str | None:
    """Motivo por el que NO se puede escribir con red de seguridad, o None.

    Se exige un repo git sin cambios en AGENTS.md/CLAUDE.md: es lo que permite
    deshacer. Si git falla, se asume que no es seguro."""
    if not (project / ".git").exists():
        return "el proyecto no es un repo git: sin él no hay forma de deshacer (commitea o crea el repo antes de --apply)"
    out = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain", "--", "AGENTS.md", "CLAUDE.md"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return f"git status falló ({out.stderr.strip()[:80]})"
    if out.stdout.strip():
        return "AGENTS.md/CLAUDE.md tienen cambios sin commitear; commitea o descarta antes de --apply"
    return None


def _write_atomic(path: Path, text: str) -> None:
    """Escribe a un temporal en el mismo directorio y reemplaza; conserva el modo."""
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def write_diff(project: Path, result: dict, out: Path | None) -> Path:
    if out is None:
        handle, name = tempfile.mkstemp(prefix=f"migrate_root_docs-{project.name.replace(' ', '_')}-", suffix=".diff")
        os.close(handle)
        target = Path(name)
    else:
        target = out
    chunks = []
    for rel, data in result["files"].items():
        chunks.append("\n".join(data["diff"]) if data["changed"] else f"# {rel}: sin cambios")
    if result["removed_lines"]:
        chunks.append("# Líneas retiradas de la zona del proyecto (lista cerrada):\n" + "\n".join(
            f"#   - {line}" for line in result["removed_lines"]
        ))
    target.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=Path, required=True, help="ruta del proyecto adoptante")
    parser.add_argument("--apply", action="store_true", help="escribir AGENTS.md y CLAUDE.md (por defecto, solo propuesta)")
    parser.add_argument("--out", type=Path, help="archivo donde escribir el diff (por defecto, el directorio temporal)")
    parser.add_argument("--json", action="store_true", help="resumen en JSON")
    args = parser.parse_args(argv)
    project = args.project.resolve()

    state_path = ROOT / "_project" / "03_project_state.json"
    try:
        mode = json.loads(state_path.read_text(encoding="utf-8")).get("framework_mode")
    except (OSError, ValueError):
        mode = None
    if mode != "template":
        print("migrate_root_docs: ejecútalo desde la plantilla del framework (framework_mode: template)", file=sys.stderr)
        return 2
    if project == ROOT or not (project / "_project").is_dir():
        print(f"migrate_root_docs: {project} no parece un proyecto adoptante", file=sys.stderr)
        return 2

    try:
        result = compute(project)
    except MigrationError as exc:
        print(f"migrate_root_docs: {project.name}: NO SE PROPONE NADA — {exc}", file=sys.stderr)
        return 1

    changed = [rel for rel, data in result["files"].items() if data["changed"]]
    diff_path = write_diff(project, result, args.out)
    summary = {
        "project": project.name, "changed": changed, "diff": str(diff_path),
        "removed_lines": result["removed_lines"], "notes": result["notes"], "applied": False,
    }
    if args.apply and changed:
        unsafe = _git_unsafe(project)
        if unsafe:
            print(f"migrate_root_docs: {unsafe}", file=sys.stderr)
            return 2
        for rel in changed:
            data = result["files"][rel]
            _write_atomic(project / rel, root_docs.restore_eol(data["new"], data["eol"]))
        summary["applied"] = True

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        if not changed:
            print(f"{project.name}: sin cambios (ya migrado)")
        else:
            verb = "APLICADO" if summary["applied"] else "PROPUESTA (no se escribió nada; usa --apply tras revisar el diff)"
            print(f"{project.name}: {verb}: {', '.join(changed)}")
            print(f"  diff: {diff_path}")
            for note in result["notes"]:
                print(f"  - {note}")
            for line in result["removed_lines"]:
                print(f"  - retirada (lista cerrada): {line[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
