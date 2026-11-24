#!/usr/bin/env python3
"""Generate Claude, Codex and OpenCode command wrappers from the workflow manifest.

Framework v3 keeps workflow logic in `.agent/workflows/` and uses this
script to create thin wrappers for three slash-command consumers:

- `.claude/commands/<name>.md` for Claude Code (one file per command)
  Created only when the command has `claude_command: true`.

- `.opencode/commands/<name>.md` for OpenCode (one file per command)
  Same surface as Claude (`claude_command: true`) so the `/` menu matches
  the one users already know from Claude Code. OpenCode reads these natively
  from `.opencode/commands/`; `$ARGUMENTS` carries user input.

- `.agents/skills/source-command-<name>/SKILL.md` — written for Codex, but
  ALSO Antigravity's native project-scope skill discovery path (verified
  against Google's official Antigravity codelab, 2026-08-25: same
  `SKILL.md` + YAML frontmatter contract, auto-loaded when the user's
  request matches `description`). This directory is a SHARED surface, not
  a Codex-only one — treat `Codex wrapper`/`CODEX_SKILLS` naming below as
  historical, not as a claim of exclusive ownership. Written for ALL
  commands in the manifest EXCEPT those with `native_preferred: true`
  (which indicates a native environment command exists and the framework
  wrapper would create a confusing duplicate).

All three are regenerated from `.agent/workflows/manifest.json`.

The Claude/Codex surface asymmetry (Codex also exposes internal sub-workflows
such as update-state or verificar-ejecutable, which Claude hides) is intentional
— see ADR-019 in `.agent/docs/framework_arch_decisions.md`. OpenCode mirrors the
Claude surface on purpose. The script name is historical; it owns all wrappers.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / ".agent" / "workflows" / "manifest.json"
CLAUDE_COMMANDS = ROOT / ".claude" / "commands"
CLAUDE_AGENTS = ROOT / ".claude" / "agents"
CLAUDE_RULES = ROOT / ".claude" / "rules"
OPENCODE_COMMANDS = ROOT / ".opencode" / "commands"
OPENCODE_AGENTS = ROOT / ".opencode" / "agents"
OPENCODE_SKILLS = ROOT / ".opencode" / "skills"
CODEX_SKILLS = ROOT / ".agents" / "skills"
GENERATED = "<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->"
# Marcador legado (pre-4.3.1): reconocerlo evita huérfanos invisibles en upgrades.
GENERATED_LEGACY = "<!-- GENERATED: framework v3 wrapper; edit manifest/source, then run sync_claude_commands.py -->"

# Read-only closing subagents. Canonical contracts live in .agent/subagents/;
# these thin OpenCode wrappers expose them with deny-by-default permissions.
SUBAGENTS = [
    ("preflight-reviewer", "Preflight Reviewer", "Revisa planes antes de CODE y recomienda gates/workflows. Solo lectura."),
    ("reviewer", "Reviewer", "Valida ACs, contratos y dependencias declaradas del módulo. Solo lectura."),
    ("security", "Security", "Valida constraints, secrets, inputs, auth, logs y exposición de datos. Solo lectura."),
    ("area-frontend", "Area Frontend", "Revisa el módulo bajo foco 'frontend' con la lente de frontend_design + cross_platform_web. Solo lectura."),
    ("area-backend", "Area Backend", "Revisa el módulo bajo foco 'backend' con la lente de architecture_core + security_core. Solo lectura."),
    ("area-data", "Area Data", "Revisa el módulo bajo foco 'data' con la lente de architecture_core + security_core. Solo lectura."),
]


def normalize_slug(value: str) -> str:
    """Normalize command slugs to the portable canonical filesystem form.

    Confinamiento (triaje 2026-07-09): un name/alias del manifest se usa como
    componente de RUTA de escritura. Separadores, '..', absolutos o slugs
    vacíos permitirían escribir fuera del repo desde un manifest malicioso —
    se rechazan con error, no se sanean en silencio."""
    slug = unicodedata.normalize("NFC", value)
    if (
        not slug.strip()
        or "/" in slug or "\\" in slug
        or slug in (".", "..") or ".." in slug.split(".")
        or slug.startswith((".", "-"))
        or any(c in slug for c in ("\x00", "\n", "\r"))
    ):
        raise ValueError(
            f"Slug de comando inseguro en el manifest: {value!r} — sin separadores, "
            "'..', ni prefijo punto/guión (confinamiento de rutas)"
        )
    return slug


def codex_slug(value: str) -> str:
    """Slug ASCII determinista para la superficie Codex (FW-100; PA FW-041).

    macOS guarda nombres en NFD y las herramientas Codex transliteran los
    caracteres no-ASCII de forma impredecible (`añadir` → `a-adir`), creando
    wrappers huérfanos duplicados que bloquean el doctor. La superficie Codex
    emite SIEMPRE el slug ASCII (ñ→n, á→a, …); Claude y OpenCode conservan el
    nombre canónico, que manejan sin problema. El doctor usa esta misma
    función para calcular los wrappers esperados."""
    slug = normalize_slug(value)
    ascii_slug = (
        unicodedata.normalize("NFKD", slug).encode("ascii", "ignore").decode("ascii")
    )
    return ascii_slug or slug


def _assert_confined(path: Path, base: Path) -> Path:
    """El path resuelto debe quedar DENTRO de base (repo). Rechaza symlinks
    de escape y cualquier composición que salga de la raíz."""
    resolved = path.resolve()
    base_resolved = base.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise ValueError(f"Ruta fuera del repo: {path} → {resolved} (base {base_resolved})")
    return path


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def write_text_if_changed(target: Path, text: str) -> None:
    """Write generated content only when it changed.

    Some environments expose generated Codex skills as active tool metadata and
    can deny needless rewrites even when the file is user-owned. Skipping
    identical writes keeps sync idempotent and avoids touching protected files.
    """
    if target.exists():
        current = target.read_text(encoding="utf-8", errors="ignore")
        if current == text:
            return
    target.write_text(text, encoding="utf-8")


def _extract_workflow_body(source_path: Path) -> str:
    """Extrae el cuerpo del workflow canónico, quitando frontmatter YAML y H1 redundante."""
    text = source_path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) >= 3:
            text = parts[2].strip()
        else:
            text = text.strip()
    else:
        text = text.strip()
    # Si el workflow empieza por '# ', quitarlo para que '# /{name}' sea el H1 canónico del wrapper
    if text.startswith("# "):
        lines = text.splitlines(keepends=True)
        i = 1
        while i < len(lines) and lines[i].strip() == "":
            i += 1
        text = "".join(lines[i:]).strip()
    return text


def claude_wrapper_text(
    name: str,
    description: str,
    source: str,
    canonical_name: str | None = None,
    inline_body: bool = False,
    disable_model_invocation: bool = False,
    dynamic_injection: bool = False,
    dynamic_view: str | None = None,
) -> str:
    alias_note = ""
    if canonical_name and canonical_name != name:
        alias_note = f"\nAlias de `/{canonical_name}`.\n"
    # YAML frontmatter: quote and escape so descriptions with ':' or '"' stay valid
    # (bug real, FW-137: antes de 4.1.0-endurecido casi ninguna description tenía
    # ':', así que el YAML sin comillas coló; con la cláusula "Usar cuando:" en las
    # 39, TODAS lo activaban — mismo fix que ya llevaba opencode_wrapper_text).
    safe_desc = description.replace("\\", "\\\\").replace('"', '\\"')

    frontmatter_lines = [f'description: "{safe_desc}"']
    if disable_model_invocation:
        frontmatter_lines.append("disable-model-invocation: true")
    frontmatter = "\n".join(frontmatter_lines)

    dynamic_block = ""
    if dynamic_injection:
        view_arg = dynamic_view or "implementation"
        dynamic_block = f"""## Contexto de etapa (inyección dinámica)

!`python3 "${{CLAUDE_PROJECT_DIR:-.}}/.agent/scripts/claude_dynamic_context.py" --view {view_arg}`

<!-- RECOVERY_GUIDE: Si la inyección dinámica previa no se ejecutó, produjo error o denegación de permisos, o ante sospecha de incoherencia, ejecuta la consulta explícita: state_manager.get_work_context(view='{view_arg}') o /reconciliar -->

"""

    if inline_body:
        source_path = _assert_confined(ROOT / source, ROOT)
        body = _extract_workflow_body(source_path)
        return f"""---
{frontmatter}
---

{GENERATED}

# /{name}
{alias_note}

Fuente canónica: `{source}`

{dynamic_block}{body}
"""

    return f"""---
{frontmatter}
---

{GENERATED}

# /{name}
{alias_note}

Fuente canónica: `{source}`

{dynamic_block}Ejecuta el artefacto canónico indicado arriba y sigue sus pasos. No dupliques lógica en este wrapper.
"""


def codex_wrapper_text(name: str, description: str, source: str, canonical_name: str | None = None) -> str:
    alias_phrase = ""
    if canonical_name and canonical_name != name:
        alias_phrase = f" (alias ASCII de `{canonical_name}`)"
    # JSON strings are valid YAML double-quoted scalars. Reusing json.dumps
    # prevents quotes, backslashes and control characters in manifest
    # descriptions from breaking Codex skill frontmatter.
    safe_desc = json.dumps(description, ensure_ascii=False)
    return f"""---
name: "source-command-{name}"
description: {safe_desc}
---

# source-command-{name}

Usar esta skill cuando el usuario pida ejecutar el comando fuente migrado `{name}`{alias_phrase}.

## Command Template

{GENERATED}

# /{name}


Fuente canónica: `{source}`

Ejecuta el artefacto canónico indicado arriba y sigue sus pasos. No dupliques lógica en este wrapper.
"""


def write_claude_wrapper(
    name: str,
    description: str,
    source: str,
    canonical_name: str | None = None,
    inline_body: bool = False,
    disable_model_invocation: bool = False,
    dynamic_injection: bool = False,
    dynamic_view: str | None = None,
) -> str:
    target = CLAUDE_COMMANDS / f"{name}.md"
    write_text_if_changed(
        target,
        claude_wrapper_text(
            name,
            description,
            source,
            canonical_name=canonical_name,
            inline_body=inline_body,
            disable_model_invocation=disable_model_invocation,
            dynamic_injection=dynamic_injection,
            dynamic_view=dynamic_view,
        ),
    )
    return target.name


def write_codex_wrapper(name: str, description: str, source: str, canonical_name: str | None = None) -> str:
    # FW-100: la superficie Codex escribe bajo el slug ASCII. Si difiere del
    # nombre canónico (p. ej. añadir-experto → anadir-experto) el wrapper se
    # documenta como alias ASCII del canónico.
    slug = codex_slug(name)
    if slug != name and canonical_name is None:
        canonical_name = name
    dir_path = CODEX_SKILLS / f"source-command-{slug}"
    dir_path.mkdir(parents=True, exist_ok=True)
    target = dir_path / "SKILL.md"
    write_text_if_changed(target, codex_wrapper_text(slug, description, source, canonical_name=canonical_name))
    return dir_path.name


def opencode_wrapper_text(name: str, description: str, source: str, canonical_name: str | None = None) -> str:
    alias_note = ""
    if canonical_name and canonical_name != name:
        alias_note = f"\nAlias de `/{canonical_name}`.\n"
    # YAML frontmatter: quote and escape so descriptions with ':' or '"' stay valid.
    safe_desc = description.replace("\\", "\\\\").replace('"', '\\"')
    return f"""---
description: "{safe_desc}"
---

{GENERATED}

# /{name}
{alias_note}

Fuente canónica: `{source}`

Ejecuta el artefacto canónico indicado arriba y sigue sus pasos. No dupliques lógica en este wrapper.

Argumentos del usuario (si los hay): $ARGUMENTS
"""


def write_opencode_wrapper(name: str, description: str, source: str, canonical_name: str | None = None) -> str:
    target = OPENCODE_COMMANDS / f"{name}.md"
    write_text_if_changed(target, opencode_wrapper_text(name, description, source, canonical_name=canonical_name))
    return target.name


def opencode_agent_text(slug: str, title: str, description: str) -> str:
    safe_desc = description.replace("\\", "\\\\").replace('"', '\\"')
    return f"""---
description: "{safe_desc}"
mode: subagent
permission:
  edit: deny
  bash: deny
  webfetch: deny
---

{GENERATED}

# Subagente {title} (solo lectura)

Eres el subagente **{title}** del framework, en modo ESTRICTAMENTE DE SOLO LECTURA:
CRÍTICO: NO utilices las herramientas de escritura (edit/bash/write) para mutar el workspace.
Tu labor es ÚNICAMENTE inspeccionar, leer (read/grep/glob) y emitir un reporte final.
No escribes archivos, no ejecutas comandos que modifiquen el workspace, no instalas
dependencias ni mutas `_project/`. Solo lees y reportas.

Contrato canónico (permisos detallados, qué revisar y formato exacto de salida):
`.agent/subagents/{slug}.md`. Léelo y síguelo. Devuelve exactamente el bloque de
resultado que ahí se especifica.
"""



def _extract_skill_metadata(path: Path) -> tuple[str, str, str]:
    """Extract title, description and body from a skill markdown file."""
    text = path.read_text(encoding="utf-8")
    title = path.stem
    description = ""
    body = text

    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) >= 3:
            frontmatter_text = parts[1]
            body = parts[2]
            try:
                # Basic parsing for description
                for line in frontmatter_text.split("\n"):
                    if line.startswith("description:"):
                        description = line.replace("description:", "", 1).strip()
                        if description.startswith('"') and description.endswith('"'):
                            description = description[1:-1]
                        elif description.startswith("'") and description.endswith("'"):
                            description = description[1:-1]
                        break
            except Exception:
                pass

    # fallback description
    if not description:
        description = f"Skill core {title}"

    return title, description, body.strip()

def opencode_skill_text(slug: str, title: str, description: str, body: str) -> str:
    safe_desc = description.replace("\\", "\\\\").replace('"', '\\"')
    return f"""---
name: "{slug}"
description: "{safe_desc}"
---

{GENERATED}

{body}
"""

def write_opencode_skill(slug: str, title: str, description: str, body: str) -> str:
    slug = normalize_slug(slug).replace('_', '-')
    dir_path = OPENCODE_SKILLS / slug
    _assert_confined(dir_path, OPENCODE_SKILLS)
    dir_path.mkdir(parents=True, exist_ok=True)
    target = dir_path / "SKILL.md"
    _assert_confined(target, OPENCODE_SKILLS)
    if target.is_symlink():
        raise ValueError(f"Ruta rechazada por symlink: {target}")
    write_text_if_changed(target, opencode_skill_text(slug, title, description, body))
    return f"{slug}/SKILL.md"


def write_opencode_agent(slug: str, title: str, description: str) -> str:
    OPENCODE_AGENTS.mkdir(parents=True, exist_ok=True)
    target = OPENCODE_AGENTS / f"{slug}.md"
    write_text_if_changed(target, opencode_agent_text(slug, title, description))
    return target.name


def claude_agent_text(slug: str, title: str, description: str) -> str:
    """Subagente de Claude Code (`.claude/agents/*.md`) — FW-206/D1.

    `tools:` restringido a lectura (`Read, Grep, Glob`): sin `Bash`/`Edit`/`Write`,
    parity con `edit/bash/webfetch: deny` de la superficie OpenCode y la lección
    FW-014 (un subagente Security corrió `uv sync` y destruyó el `.venv`). El
    contrato canónico (qué revisar, formato de salida) vive en `.agent/subagents/`.
    """
    safe_desc = description.replace("\\", "\\\\").replace('"', '\\"')
    return f"""---
name: {slug}
description: "{safe_desc}"
tools: Read, Grep, Glob
---

{GENERATED}

Eres el subagente **{title}** del framework, en modo ESTRICTAMENTE DE SOLO LECTURA.
Solo tienes herramientas de lectura (Read/Grep/Glob): no escribes archivos, no
ejecutas comandos que muten el workspace, no instalas dependencias, no tocas
`_project/`. Inspeccionas y devuelves un reporte final. Si el driver te pasa la
ruta de un fichero `.patch` (el diff de la ronda), léelo con Read: no ejecutes
`git diff` (FW-341).

Contrato canónico (permisos detallados, qué revisar y formato exacto de salida):
`.agent/subagents/{slug}.md`. Léelo y síguelo. Devuelve exactamente el bloque de
resultado que ahí se especifica.
"""


def write_claude_agent(slug: str, title: str, description: str) -> str:
    CLAUDE_AGENTS.mkdir(parents=True, exist_ok=True)
    target = CLAUDE_AGENTS / f"{slug}.md"
    # No pisar un `.claude/agents/<slug>.md` hecho a mano (sin marcador GENERATED),
    # igual que el sync respeta wrappers manuales.
    if target.exists():
        existing = target.read_text(encoding="utf-8", errors="ignore")
        if GENERATED not in existing and GENERATED_LEGACY not in existing:
            print(f"  aviso: .claude/agents/{slug}.md existe sin marcador GENERATED — no se sobrescribe")
            return target.name
    write_text_if_changed(target, claude_agent_text(slug, title, description))
    return target.name


def extract_file_obligations(source_path: Path) -> str:
    """Extrae las obligaciones de fichero delimitadas por CONTEXT_SOURCE: file_obligations.
    Si el marcador no existe, lanza un error para evitar generar reglas vacías o arbitrarias."""
    content = source_path.read_text(encoding="utf-8")
    match = re.search(
        r"<!--\s*CONTEXT_SOURCE:\s*file_obligations\s*-->\n(.*?)\n<!--\s*END_CONTEXT_SOURCE:\s*file_obligations\s*-->",
        content,
        re.DOTALL,
    )
    if not match:
        raise ValueError(
            f"Fuente {source_path.name} no contiene bloque '<!-- CONTEXT_SOURCE: file_obligations -->'"
        )
    return match.group(1).strip()


def claude_rule_text(
    name: str,
    description: str,
    paths: list[str],
    body: str,
    source_rel: str,
) -> str:
    paths_yaml = "\n".join(f'  - "{p}"' for p in paths)
    return f"""---
paths:
{paths_yaml}
---

{GENERATED}
<!-- REGLA POR RUTA (Claude Code) — fuente canónica: {source_rel} -->
<!-- Obligaciones técnicas de archivo. Las obligaciones de etapa residen en la fuente canónica. -->

# Reglas de archivo: {name}

{body}
"""


def write_claude_rule(
    name: str,
    description: str,
    paths: list[str],
    source_rel: str,
) -> str:
    rules_dir = CLAUDE_RULES if CLAUDE_RULES.parent.parent == ROOT else ROOT / ".claude" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    source_path = _assert_confined(ROOT / source_rel, ROOT)
    if not source_path.exists():
        raise SystemExit(f"Fuente inexistente para regla {name}: {source_rel}")
    body = extract_file_obligations(source_path)
    target = rules_dir / f"{name}.md"
    if target.exists():
        existing = target.read_text(encoding="utf-8", errors="ignore")
        if GENERATED not in existing and GENERATED_LEGACY not in existing:
            print(f"  aviso: .claude/rules/{name}.md existe sin marcador GENERATED — no se sobrescribe")
            return target.name
    write_text_if_changed(target, claude_rule_text(name, description, paths, body, source_rel))
    return target.name



# ---------------------------------------------------------------------------
# Docs generados desde manifest (3.18.0, FW-070): los bloques entre marcadores
# GENERADO en CLAUDE.md y AGENTS.md se derivan del manifest — la fuente única.
# El doctor verifica frescura (validate_root_docs_freshness); editar a mano
# dentro de un bloque es drift y se detecta.
# ---------------------------------------------------------------------------
GENERATED_BLOCK_RE_TMPL = r"<!-- BEGIN GENERADO: {tag}[^>]*-->\n(.*?)\n<!-- FIN GENERADO: {tag} -->"


def _generated_block(tag: str, body: str) -> str:
    return (
        f"<!-- BEGIN GENERADO: {tag} — sync_claude_commands.py; no editar a mano -->\n"
        f"{body}\n"
        f"<!-- FIN GENERADO: {tag} -->"
    )


def render_claude_internal_body(manifest: dict) -> str:
    """Maquinaria interna = comandos del manifest sin builder_facing (4.0.0,
    FW-071). El agente los invoca; el builder no necesita conocerlos."""
    names = [
        (f"`/{c['name']}`" if c.get("claude_command", False) else f"`{c['name']}`")
        for c in manifest["commands"]
        if not c.get("builder_facing", False)
    ]
    return " · ".join(names)


def render_agents_table_body(manifest: dict) -> str:
    """Índice compacto de comandos para la carga inicial de ``AGENTS.md``.

    El manifest sigue siendo la única fuente de descripciones, triggers y
    rutas. Copiar la tabla completa aquí hacía que cada sesión pagara el coste
    de todos los workflows, incluso cuando solo necesitaba ``/arrancar``.
    ``CLAUDE.md`` conserva la superficie pública legible; los detalles de cada
    comando se abren desde el manifest/workflow cuando la etapa los activa.
    """
    builder = [
        f"`/{c['name']}`" for c in manifest["commands"]
        if c.get("builder_facing", False)
    ]
    internal = [
        f"`/{c['name']}`" if c.get("claude_command", False) else f"`{c['name']}`"
        for c in manifest["commands"]
        if not c.get("builder_facing", False)
    ]
    return "\n".join(
        [
            "Índice compacto; no cargar workflows futuros ni sus descripciones al arrancar.",
            "- Builder: " + " · ".join(builder),
            "- Internos: " + " · ".join(internal),
            "- Fuente canónica de etapa, trigger y ruta: `.agent/workflows/manifest.json`.",
            "- Abrir solo el `source` de la etapa actual y los documentos que devuelva `get_work_context`.",
        ]
    )


def replace_generated_block(text: str, tag: str, body: str) -> str:
    pattern = GENERATED_BLOCK_RE_TMPL.format(tag=re.escape(tag))
    if not re.search(pattern, text, re.DOTALL):
        raise SystemExit(f"Bloque GENERADO '{tag}' no encontrado; restaurar los marcadores")
    return re.sub(pattern, lambda _m: _generated_block(tag, body), text, count=1, flags=re.DOTALL)


GEMINI_POINTER = """# GEMINI.md — puntero

Antigravity lee `AGENTS.md` o `GEMINI.md` de la raíz (verificado 2026-08-25). Este
fichero es solo un puntero fino: la constitución real del workspace y el
protocolo del framework viven en [`AGENTS.md`](AGENTS.md) y [`CLAUDE.md`](CLAUDE.md)
(el nombre es histórico; aplica a cualquier agente). El índice de comandos
apunta al manifest para evitar duplicar descripciones y cargar contexto de más.

No dupliques contenido aquí — un tercer sitio con la tabla de comandos es un
tercer punto de drift. Si `AGENTS.md` cambia, este puntero no necesita tocarse.

Adaptador específico de Antigravity (superficie nativa, MCP, subagentes,
degradaciones): `.agent/adapters/antigravity.md`.
"""


def regenerate_root_docs(manifest: dict) -> int:
    """Regenera los bloques GENERADO de CLAUDE.md y AGENTS.md. Devuelve cuántos
    archivos cambiaron."""
    # La huella del bloque GESTIONADO (`root_docs.py`) ignora el interior de los
    # sub-bloques GENERADO, así que regenerarlos aquí no la invalida.
    changed = 0
    claude_path = ROOT / "CLAUDE.md"
    if claude_path.exists():
        claude_text = claude_path.read_text(encoding="utf-8")
        new_claude = replace_generated_block(
            claude_text, "maquinaria-interna", render_claude_internal_body(manifest)
        )
        if new_claude != claude_text:
            claude_path.write_text(new_claude, encoding="utf-8")
            changed += 1
    # FW-162: `GEMINI.md` es un puntero fino sin contenido de producto, pero el
    # doctor lo exige en la raíz desde 4.19.x. Los proyectos adoptados antes no
    # lo tienen, y el upgrade solo copia `.agent/` — así que llegaban al paso 5
    # en FAIL sin haber hecho nada mal.
    #
    # Se GENERA en vez de copiarse: dentro de un proyecto adoptado no existe
    # ninguna plantilla desde la que copiar (la primera versión de este fix
    # buscaba el fichero en la raíz del propio proyecto, que es justo donde
    # falta, y no hacía nada). Si ya existe, no se toca: puede llevar notas.
    gemini_path = ROOT / "GEMINI.md"
    if not gemini_path.exists():
        gemini_path.write_text(GEMINI_POINTER, encoding="utf-8")
        changed += 1

    agents_path = ROOT / "AGENTS.md"
    if agents_path.exists():
        agents_text = agents_path.read_text(encoding="utf-8")
        new_agents = replace_generated_block(
            agents_text, "tabla-comandos", render_agents_table_body(manifest)
        )
        if new_agents != agents_text:
            agents_path.write_text(new_agents, encoding="utf-8")
            changed += 1
    return changed


def sync_codex_only(manifest: dict) -> int:
    """Genera únicamente la superficie Codex, sin tocar Claude/OpenCode/docs."""
    CODEX_SKILLS.mkdir(parents=True, exist_ok=True)
    expected_codex: set[str] = set()

    for raw_command in manifest["commands"]:
        command = dict(raw_command)
        name = normalize_slug(command["name"])
        source = _assert_confined(ROOT / command["source"], ROOT)
        if not source.exists():
            raise SystemExit(f"Fuente inexistente para /{name}: {command['source']}")
        if not command.get("builder_facing", False):
            command["description"] = f"(interno — lo invoca el agente) {command['description']}"
        if command.get("native_preferred", False):
            continue
        expected_codex.add(write_codex_wrapper(name, command["description"], command["source"]))
        for alias in command.get("aliases", []):
            alias = normalize_slug(alias)
            expected_codex.add(
                write_codex_wrapper(alias, command["description"], command["source"], canonical_name=name)
            )

    expected_codex_nfc = {normalize_slug(item) for item in expected_codex}
    for dir_path in CODEX_SKILLS.iterdir():
        if not dir_path.is_dir() or not dir_path.name.startswith("source-command-"):
            continue
        if normalize_slug(dir_path.name) not in expected_codex_nfc:
            skill_file = dir_path / "SKILL.md"
            if skill_file.exists():
                text = skill_file.read_text(encoding="utf-8")
                if GENERATED in text or GENERATED_LEGACY in text:
                    skill_file.unlink()
                    try:
                        dir_path.rmdir()
                    except OSError:
                        pass
    return len(expected_codex)


def _expected_generated_files(manifest: dict, include_claude: bool = True) -> dict[Path, str]:
    """Render generated wrappers in memory for the non-mutating ``--check`` gate."""
    expected: dict[Path, str] = {}
    for raw_command in manifest["commands"]:
        command = dict(raw_command)
        name = normalize_slug(command["name"])
        source = _assert_confined(ROOT / command["source"], ROOT)
        if not source.exists():
            raise SystemExit(f"Fuente inexistente para /{name}: {command['source']}")
        if not command.get("builder_facing", False):
            command["description"] = f"(interno — lo invoca el agente) {command['description']}"

        aliases = [normalize_slug(alias) for alias in command.get("aliases", [])]
        inline_body = command.get("claude_inline_body", False)
        disable_model = command.get("disable_model_invocation", False)
        dynamic_injection = command.get("claude_dynamic_injection", False)
        dynamic_view = command.get("claude_dynamic_context_view") or command.get("context", {}).get("entry_view")
        if dynamic_injection and name == "arrancar":
            raise ValueError(
                "Comando /arrancar no puede tener claude_dynamic_injection: true (reservado para hook de continuidad Lote 3)"
            )
        if include_claude and command.get("claude_command", False):
            for wrapper_name, canonical_name in [(name, None), *[(alias, name) for alias in aliases]]:
                expected[CLAUDE_COMMANDS / f"{wrapper_name}.md"] = claude_wrapper_text(
                    wrapper_name,
                    command["description"],
                    command["source"],
                    canonical_name=canonical_name,
                    inline_body=inline_body,
                    disable_model_invocation=disable_model,
                    dynamic_injection=dynamic_injection,
                    dynamic_view=dynamic_view,
                )
                expected[OPENCODE_COMMANDS / f"{wrapper_name}.md"] = opencode_wrapper_text(
                    wrapper_name, command["description"], command["source"], canonical_name=canonical_name
                )

        if not command.get("native_preferred", False):
            for wrapper_name, canonical_name in [(name, None), *[(alias, name) for alias in aliases]]:
                slug = codex_slug(wrapper_name)
                if slug != wrapper_name and canonical_name is None:
                    canonical_name = wrapper_name
                expected[CODEX_SKILLS / f"source-command-{slug}" / "SKILL.md"] = codex_wrapper_text(
                    slug, command["description"], command["source"], canonical_name=canonical_name
                )

    if include_claude:
        for slug, title, desc in SUBAGENTS:
            expected[OPENCODE_AGENTS / f"{slug}.md"] = opencode_agent_text(slug, title, desc)
            # `.claude/agents/*.md` generado sin marcador → se trata como manual y
            # no se lista aquí (el sync tampoco lo pisa). Solo entra al contrato de
            # `--check` el que lleva marcador o aún no existe.
            claude_agent_path = CLAUDE_AGENTS / f"{slug}.md"
            if not claude_agent_path.exists() or GENERATED in claude_agent_path.read_text(encoding="utf-8", errors="ignore"):
                expected[claude_agent_path] = claude_agent_text(slug, title, desc)

        # Add core skills
        skills_dir = ROOT / ".agent" / "skills"
        if skills_dir.exists():
            for path in skills_dir.glob("*.md"):
                if path.name == "README.md":
                    continue
                title, desc, body = _extract_skill_metadata(path)
                slug = normalize_slug(path.stem).replace('_', '-')
                expected[OPENCODE_SKILLS / slug / "SKILL.md"] = opencode_skill_text(slug, title, desc, body)

            # imported skills
            imported_dir = skills_dir / "imported"
            if imported_dir.exists():
                for path in imported_dir.glob("*.md"):
                    if path.name == "README.md":
                        continue
                    title, desc, body = _extract_skill_metadata(path)
                    slug = normalize_slug(path.stem).replace('_', '-')
                    expected[OPENCODE_SKILLS / slug / "SKILL.md"] = opencode_skill_text(slug, title, desc, body)

        # Claude rules conditioned by path (Lote 5C)
        rules_dir = CLAUDE_RULES if CLAUDE_RULES.parent.parent == ROOT else ROOT / ".claude" / "rules"
        for rule in manifest.get("claude_rules", []):
            rule_name = normalize_slug(rule["name"])
            source_rel = rule["source"]
            source_path = _assert_confined(ROOT / source_rel, ROOT)
            if not source_path.exists():
                raise SystemExit(f"Fuente inexistente para regla {rule_name}: {source_rel}")
            rule_file = rules_dir / f"{rule_name}.md"
            if not rule_file.exists() or GENERATED in rule_file.read_text(encoding="utf-8", errors="ignore"):
                body = extract_file_obligations(source_path)
                expected[rule_file] = claude_rule_text(
                    rule_name,
                    rule.get("description", ""),
                    rule.get("paths", []),
                    body,
                    source_rel,
                )
    return expected


def _check_generated_files(expected: dict[Path, str], label: str) -> list[str]:
    issues: list[str] = []
    for path, content in expected.items():
        if not path.exists():
            issues.append(f"{label}: falta {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8", errors="ignore") != content:
            issues.append(f"{label}: desactualizado {path.relative_to(ROOT)}")
    expected_paths = set(expected)
    roots = {path.parent for path in expected_paths}
    if any(path.is_relative_to(CODEX_SKILLS) for path in expected_paths):
        roots.add(CODEX_SKILLS)
    if any(path.is_relative_to(OPENCODE_SKILLS) for path in expected_paths):
        roots.add(OPENCODE_SKILLS)
    for directory in roots:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path in expected_paths or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if GENERATED in text or GENERATED_LEGACY in text:
                issues.append(f"{label}: huérfano generado {path.relative_to(ROOT)}")
    return issues


def _check_root_docs(manifest: dict) -> list[str]:
    issues: list[str] = []
    claude_path = ROOT / "CLAUDE.md"
    if claude_path.exists():
        current = claude_path.read_text(encoding="utf-8")
        try:
            expected = replace_generated_block(
                current, "maquinaria-interna", render_claude_internal_body(manifest)
            )
        except SystemExit as exc:
            issues.append(f"Docs raíz: {exc}")
        else:
            if expected != current:
                issues.append("Docs raíz: bloque maquinaria-interna desactualizado en CLAUDE.md")

    agents_path = ROOT / "AGENTS.md"
    if agents_path.exists():
        current = agents_path.read_text(encoding="utf-8")
        try:
            expected = replace_generated_block(
                current, "tabla-comandos", render_agents_table_body(manifest)
            )
        except SystemExit as exc:
            issues.append(f"Docs raíz: {exc}")
        else:
            if expected != current:
                issues.append("Docs raíz: bloque tabla-comandos desactualizado en AGENTS.md")

    if not (ROOT / "GEMINI.md").exists():
        issues.append("Docs raíz: falta GEMINI.md generado")
    return issues


def check_sync(manifest: dict, codex_only: bool = False) -> list[str]:
    """Return sync drift without creating, deleting, or rewriting any file."""
    expected = _expected_generated_files(manifest, include_claude=not codex_only)
    issues = _check_generated_files(expected, "Codex" if codex_only else "Wrappers")
    if not codex_only:
        issues.extend(_check_root_docs(manifest))
    return issues


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Genera wrappers Claude, Codex y OpenCode")
    parser.add_argument(
        "--codex-only",
        action="store_true",
        help="actualizar únicamente .agents/skills/source-command-*",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="comprobar drift sin crear, eliminar ni modificar archivos",
    )
    # Las pruebas y otros scripts del framework llaman `main()` directamente;
    # en ese caso no deben heredarse los argumentos del proceso anfitrión
    # (por ejemplo, `unittest discover`). El entry point explícito sí pasa CLI.
    args = parser.parse_args([] if argv is None else argv)
    manifest = load_manifest()
    if args.codex_only:
        if args.check:
            issues = check_sync(manifest, codex_only=True)
            if issues:
                print("\n".join(issues))
                raise SystemExit(1)
            print("Codex sync check: OK")
            return
        count = sync_codex_only(manifest)
        print(f"Codex wrappers sincronizados (solo Codex): {count}")
        return

    if args.check:
        issues = check_sync(manifest)
        if issues:
            print("\n".join(issues))
            raise SystemExit(1)
        print("Sync check: OK")
        return

    CLAUDE_COMMANDS.mkdir(parents=True, exist_ok=True)
    OPENCODE_COMMANDS.mkdir(parents=True, exist_ok=True)
    CODEX_SKILLS.mkdir(parents=True, exist_ok=True)

    expected_claude: set[str] = set()
    expected_claude_agents: set[str] = set()
    expected_opencode: set[str] = set()
    expected_opencode_agents: set[str] = set()
    expected_codex: set[str] = set()

    for command in manifest["commands"]:
        name = normalize_slug(command["name"])
        source = _assert_confined(ROOT / command["source"], ROOT)
        if not source.exists():
            raise SystemExit(f"Fuente inexistente para /{name}: {command['source']}")
        # 4.0.0 (FW-071): superficie de dos planos — los comandos no
        # builder-facing siguen invocables (por el agente y el usuario
        # avanzado) pero se marcan como maquinaria interna.
        if not command.get("builder_facing", False):
            command = dict(command, description=f"(interno — lo invoca el agente) {command['description']}")

        # ----- Claude + OpenCode wrappers: same surface (claude_command) -----
        inline_body = command.get("claude_inline_body", False)
        disable_model = command.get("disable_model_invocation", False)
        dynamic_injection = command.get("claude_dynamic_injection", False)
        dynamic_view = command.get("claude_dynamic_context_view") or command.get("context", {}).get("entry_view")
        if dynamic_injection and name == "arrancar":
            raise ValueError(
                "Comando /arrancar no puede tener claude_dynamic_injection: true (reservado para hook de continuidad Lote 3)"
            )
        if command.get("claude_command", False):
            expected_claude.add(
                write_claude_wrapper(
                    name,
                    command["description"],
                    command["source"],
                    inline_body=inline_body,
                    disable_model_invocation=disable_model,
                    dynamic_injection=dynamic_injection,
                    dynamic_view=dynamic_view,
                )
            )
            expected_opencode.add(write_opencode_wrapper(name, command["description"], command["source"]))
            for alias in command.get("aliases", []):
                alias = normalize_slug(alias)
                expected_claude.add(
                    write_claude_wrapper(
                        alias,
                        command["description"],
                        command["source"],
                        canonical_name=name,
                        inline_body=inline_body,
                        disable_model_invocation=disable_model,
                        dynamic_injection=dynamic_injection,
                        dynamic_view=dynamic_view,
                    )
                )
                expected_opencode.add(write_opencode_wrapper(alias, command["description"], command["source"], canonical_name=name))

        # ----- Codex wrapper: for ALL commands EXCEPT native_preferred -----
        if not command.get("native_preferred", False):
            expected_codex.add(write_codex_wrapper(name, command["description"], command["source"]))
            for alias in command.get("aliases", []):
                alias = normalize_slug(alias)
                expected_codex.add(write_codex_wrapper(alias, command["description"], command["source"], canonical_name=name))

    # ----- Read-only subagents (canonical contracts in .agent/subagents/) -----
    for slug, title, desc in SUBAGENTS:
        expected_opencode_agents.add(write_opencode_agent(slug, title, desc))
        expected_claude_agents.add(write_claude_agent(slug, title, desc))

    expected_opencode_skills: set[str] = set()
    skills_dir = ROOT / ".agent" / "skills"
    if skills_dir.exists():
        for path in skills_dir.glob("*.md"):
            if path.name == "README.md":
                continue
            title, desc, body = _extract_skill_metadata(path)
            expected_opencode_skills.add(write_opencode_skill(path.stem, title, desc, body))

        imported_dir = skills_dir / "imported"
        if imported_dir.exists():
            for path in imported_dir.glob("*.md"):
                if path.name == "README.md":
                    continue
                title, desc, body = _extract_skill_metadata(path)
                expected_opencode_skills.add(write_opencode_skill(path.stem, title, desc, body))

    # Clean up orphan claude wrappers (only those we generated)
    expected_claude_nfc = {normalize_slug(item) for item in expected_claude}
    for path in CLAUDE_COMMANDS.glob("*.md"):
        if normalize_slug(path.name) not in expected_claude_nfc:
            text = path.read_text(encoding="utf-8")
            if (GENERATED in text or GENERATED_LEGACY in text):
                path.unlink()

    # Clean up orphan opencode wrappers (only those we generated)
    expected_opencode_nfc = {normalize_slug(item) for item in expected_opencode}
    for path in OPENCODE_COMMANDS.glob("*.md"):
        if normalize_slug(path.name) not in expected_opencode_nfc:
            text = path.read_text(encoding="utf-8")
            if (GENERATED in text or GENERATED_LEGACY in text):
                path.unlink()

    # Clean up orphan opencode subagents (only those we generated)
    if OPENCODE_AGENTS.exists():
        for path in OPENCODE_AGENTS.glob("*.md"):
            if path.name not in expected_opencode_agents:
                text = path.read_text(encoding="utf-8")
                if (GENERATED in text or GENERATED_LEGACY in text):
                    path.unlink()

    # Clean up orphan claude subagents (only those we generated)
    if CLAUDE_AGENTS.exists():
        for path in CLAUDE_AGENTS.glob("*.md"):
            if path.name not in expected_claude_agents:
                text = path.read_text(encoding="utf-8")
                if (GENERATED in text or GENERATED_LEGACY in text):
                    path.unlink()

    # Clean up orphan opencode skills (only those we generated)
    if OPENCODE_SKILLS.exists():
        for dir_path in OPENCODE_SKILLS.iterdir():
            if not dir_path.is_dir():
                continue
            skill_path = dir_path / "SKILL.md"
            if f"{dir_path.name}/SKILL.md" not in expected_opencode_skills:
                if skill_path.exists():
                    text = skill_path.read_text(encoding="utf-8")
                    if (GENERATED in text or GENERATED_LEGACY in text):
                        skill_path.unlink()
                        try:
                            dir_path.rmdir()
                        except OSError:
                            pass

    # Clean up orphan codex wrappers (only those we generated)
    expected_codex_nfc = {normalize_slug(item) for item in expected_codex}
    for dir_path in CODEX_SKILLS.iterdir():
        if not dir_path.is_dir():
            continue
        if not dir_path.name.startswith("source-command-"):
            continue
        if normalize_slug(dir_path.name) not in expected_codex_nfc:
            skill_file = dir_path / "SKILL.md"
            if skill_file.exists():
                text = skill_file.read_text(encoding="utf-8")
                if (GENERATED in text or GENERATED_LEGACY in text):
                    skill_file.unlink()
                    try:
                        dir_path.rmdir()
                    except OSError:
                        pass

    # ----- Claude rules conditioned by path (Lote 5C) -----
    rules_dir = CLAUDE_RULES if CLAUDE_RULES.parent.parent == ROOT else ROOT / ".claude" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    expected_claude_rules: set[str] = set()
    for rule in manifest.get("claude_rules", []):
        rule_name = normalize_slug(rule["name"])
        source_rel = rule["source"]
        expected_claude_rules.add(
            write_claude_rule(
                rule_name,
                rule.get("description", ""),
                rule.get("paths", []),
                source_rel,
            )
        )

    # Clean up orphan claude rules (only those we generated)
    expected_rules_nfc = {normalize_slug(item) for item in expected_claude_rules}
    if rules_dir.exists():
        for path in rules_dir.glob("*.md"):
            if normalize_slug(path.name) not in expected_rules_nfc:
                text = path.read_text(encoding="utf-8")
                if (GENERATED in text or GENERATED_LEGACY in text):
                    path.unlink()

    docs_changed = regenerate_root_docs(manifest)

    print(f"Claude wrappers sincronizados:   {len(expected_claude)}")
    print(f"Claude subagentes:               {len(expected_claude_agents)}")
    print(f"Claude reglas por ruta:          {len(expected_claude_rules)}")
    print(f"OpenCode wrappers sincronizados: {len(expected_opencode)}")
    print(f"OpenCode subagentes:             {len(expected_opencode_agents)}")
    print(f"OpenCode skills sincronizadas:   {len(expected_opencode_skills)}")
    print(f"Codex wrappers sincronizados:    {len(expected_codex)}")
    print(f"Docs raíz regenerados:           {docs_changed}")


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
