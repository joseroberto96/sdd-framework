#!/usr/bin/env python3
"""Evidence gate for /cierre-modulo step 1.c, extracted as an executable check.

The deterministic part of the acceptance-evidence rule should not live only in
prose that an LLM may or may not honor at runtime. This script encodes it so
/cierre-modulo, pre-commit or CI can adjudicate mechanically (enforcement
hardening — the gate logic becomes a script, not a sentence).

Rule (mirrors `.agent/workflows/cierre-modulo.md` step 1.c EVIDENCE GATE):
- For each AC marked `[x]` in a module's section of
  `_project/08_module_acceptance.md`:
  - it MUST contain `evidencia:` followed by >= 20 chars of concrete content;
  - the content MUST NOT be a lazy token (`hecho`, `funciona`, `implementado`,
    `ok`, `done`, `listo`, `pendiente`) nor start with `pendiente`.
  - ANTI-FABRICATION (when run via the CLI, which passes the repo root): any repo
    file path cited as evidence (`dir/file.ext`, optionally `::node` or `:line`)
    MUST exist on disk. A path that does not exist is treated as fabricated
    evidence and blocks. URLs and narrative evidence are not path-checked.
- Unchecked (`[ ]`) ACs are out of scope here (coverage % is a separate check).
- Already-`completed` modules are guarded upstream by /cierre-modulo Step 0
  (early exit), so this script is only invoked for modules being closed and can
  assume the canonical `— evidencia:` format. Historical pre-format closures
  (e.g. BOTÓN P0/B1) are never re-evaluated.

Usage:
    python3 .agent/scripts/validate_acceptance.py "<module_id>"
Exit codes: 0 = gate OK, 1 = ACs without valid evidence, 2 = usage/file error.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "_project" / "08_module_acceptance.md"
STATE = ROOT / "_project" / "03_project_state.json"
MIN_EVIDENCE_CHARS = 20
REJECTED = {"hecho", "funciona", "implementado", "ok", "done", "listo", "pendiente"}
EVIDENCE_RE = re.compile(r"evidencia\s*:\s*(.*)$", re.IGNORECASE)
AC_RE = re.compile(r"^\s*[-*]\s*\[(?P<mark>[ xX])\]\s*(?P<body>.*)$")
H2_RE = re.compile(r"^##\s+(?P<title>.*\S)\s*$")
# A `module_statuses.<id>: <status>` citation inside evidence text. Module status
# is a stable fact, so a citation that contradicts the live state is stale
# narrative evidence. Time-relative claims such as modules.in_progress membership
# or current_phase are intentionally not matched here to avoid false positives
# over legitimate point-in-time evidence.
MODULE_STATUS_CITATION_RE = re.compile(
    r"module_statuses\.([A-Za-z0-9_]+)\s*[:=]\s*([a-z_]+)"
)
# A URL (scheme://...) must NOT be treated as a file path.
URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
# A Git SSH remote (`user@host:owner/repo[.git]`) must NOT be treated as a
# local file path. URL_RE only catches scheme-based URLs; SCP-like Git remotes
# otherwise expose `owner/repo.git` to PATH_RE and create false positives.
SSH_REMOTE_RE = re.compile(
    r"\b[A-Za-z0-9_.\-]+@[A-Za-z0-9_.\-]+:[A-Za-z0-9_./\-]+(?:\.git)?\b"
)
# A file path cited as evidence: at least one `dir/` segment + `name.ext`,
# optionally followed by a pytest `::node` id and/or a `:line`/`:line-line`.
# Requiring a `/` keeps narrative evidence and bare words from matching.
PATH_RE = re.compile(
    r"(?<![\w/])((?:[A-Za-z0-9_.\-]+/)+[A-Za-z0-9_.\-]+\.[A-Za-z0-9]{1,32}(?![A-Za-z0-9]))"
    r"(?:::[^\s`\"']+)?(?::\d+(?:-\d+)?)?"
)
# TL FW-015: PATH_RE matches ANY `dir/file.ext`-shaped substring in evidence
# prose — a zip-internal path (`xl/vbaProject.bin`), a trading symbol written
# with a slash (`XAU/USD.raw`) or an installed-package module that lives
# outside the repo (`dukascopy_python/__init__.py`, in a gitignored
# .venv/site-packages the search skips on purpose) all get treated as
# fabricated repo citations and block the gate. Restricting PATH_RE (or the
# check consuming it) to a fixed set of "known" top-level repo dirs was
# considered and rejected: two existing anti-fabrication tests
# (test_missing_artifacts_flags_nonexistent_cited_path cites "tests/x.py"
# against an EMPTY root; test_missing_artifacts_suffix_must_match_on_boundary
# cites "otros/x.py" against a root that only has "src/") deliberately cite a
# top-level segment that does not exist and must still be flagged — any
# "does the first segment already exist" heuristic silently un-flags both,
# defeating the anti-fabrication check itself. Implementing the FW's own
# second, additive suggestion instead: an explicit opt-in marker so evidence
# text can say "this is not a repo path" without paraphrasing around the
# false positive.
NOT_REPO_PATH_CORE = r"\(\s*no es (?:una?\s+)?ruta del repo\s*\)"
NOT_REPO_PATH_RE = re.compile(r"(?i)^" + NOT_REPO_PATH_CORE)
# Same marker, but matchable anywhere in the evidence text (not just right after
# a cited path) — used by the anchor check below.
NOT_REPO_PATH_ANYWHERE_RE = re.compile(r"(?i)" + NOT_REPO_PATH_CORE)
# A command result: `rc=<n>`, `exit <n>` or `exit code <n>`.
COMMAND_RESULT_RE = re.compile(r"(?i)\b(?:rc\s*=\s*\d+|exit\s+code\s+\d+|exit\s+\d+)\b")
# A pytest node id (`::`) or a bare test function name (`test_foo`).
TEST_NODE_RE = re.compile(r"::|(?<![\w])test_[A-Za-z0-9_]+")


def has_anchor(content: str) -> bool:
    """FW-127: evidence needs a verifiable anchor unless explicitly narrative.

    An anchor is: a repo path (the same PATH_RE missing_artifacts() uses, so
    the anti-fabrication check still applies on top), a command result, a test
    node id, a URL, or the existing narrative opt-in marker."""
    scrubbed = SSH_REMOTE_RE.sub(" ", URL_RE.sub(" ", content))
    if PATH_RE.search(scrubbed):
        return True
    if URL_RE.search(content):
        return True
    if COMMAND_RESULT_RE.search(content):
        return True
    if TEST_NODE_RE.search(content):
        return True
    if NOT_REPO_PATH_ANYWHERE_RE.search(content):
        return True
    return False


def evidence_problem(ac_body: str) -> str | None:
    """Return a problem string if a checked AC body lacks valid evidence, else None."""
    match = EVIDENCE_RE.search(ac_body)
    if not match:
        return "sin 'evidencia:' concreta"
    content = match.group(1).strip().strip("`\"' .")
    if len(content) < MIN_EVIDENCE_CHARS:
        return f"evidencia < {MIN_EVIDENCE_CHARS} caracteres"
    low = content.lower()
    if low in REJECTED or low.startswith("pendiente"):
        return f"evidencia perezosa rechazada: '{content[:30]}'"
    if not has_anchor(content):
        return (
            "evidencia sin ancla verificable: añade ruta, comando con rc, id de "
            "test, URL, o marca '(no es ruta del repo)' si es deliberadamente "
            "narrativa"
        )
    return None


# Directories that never hold cited source/test evidence; pruned while searching.
IGNORED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", ".nuxt", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
}


def _cited_path_exists(path: str, root: Path) -> bool:
    """A cited path exists if it resolves at the project root OR matches the path
    suffix of some real file. Evidence often cites paths relative to a source root
    (e.g. `services/x.py` for `src/app/services/x.py`), so a pure root-relative
    check produces false positives. Suffix matching keeps the catch on fully
    fabricated paths (a name/suffix that matches nothing) without flagging real
    files cited from a sub-root."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.exists()
    if (root / path).exists():
        return True
    parts = candidate.parts
    name = candidate.name
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        if name in filenames:
            rel_parts = (Path(dirpath) / name).relative_to(root).parts
            if len(rel_parts) >= len(parts) and rel_parts[-len(parts):] == parts:
                return True
    return False


# TL-FW-323: `retirado: <ruta>` cita una ruta que el módulo quitó; se comprueba
# al revés (retired_problems): no existe y figura borrada en el diff del módulo.
RETIRED_PREFIX_RE = re.compile(r"(?i)(?<![\w])retirad[oa]\s*:\s*[`'\"]?$")


def _scan_text(ac_body: str) -> str:
    """Una sola extracción para missing_artifacts y retired_problems: la evidencia
    sin URL ni remotos SSH. Si cada una limpiara a su manera, una ruta podía
    quedar «retirada» para una y no para la otra, y nadie la comprobaba."""
    match = EVIDENCE_RE.search(ac_body)
    content = match.group(1) if match else ac_body
    return SSH_REMOTE_RE.sub(" ", URL_RE.sub(" ", content))


def _retired_paths(content: str) -> list[str]:
    return [m.group(1) for m in PATH_RE.finditer(content) if RETIRED_PREFIX_RE.search(content[:m.start()])]


def _approval_base(root: Path, head: object) -> tuple[str | None, str]:
    """La base guardada en la aprobación, solo si es un commit de este repo."""
    import workflow_receipts as _wr

    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", head):
        return None, "la base de la aprobación no es un commit"
    sha = _wr._git_out(root, "rev-parse", "--verify", "-q", f"{head}^{{commit}}")
    return (sha, "aprobación") if sha else (None, "la base de la aprobación no es un commit de este repo")


def retired_problems(ac_body: str, root: Path, state: dict | None, module_id: str) -> list[str]:
    """Cada `retirado: <ruta>` debe no existir (exacta, relativa a la raíz) y
    aparecer como borrada, o como origen de un renombrado, en el diff del
    módulo contra la base de su aprobación (o la rama base si no la hay)."""
    paths = _retired_paths(_scan_text(ac_body))
    if not paths:
        return []
    import workflow_receipts as _wr

    approvals = ((state or {}).get("workflow_receipts") or {}).get("approvals") or {}
    approval = approvals.get(module_id) if isinstance(approvals, dict) else None
    approval = approval if isinstance(approval, dict) else {}
    head = approval.get("head_at_approval_origin") or approval.get("head_at_approval")
    # Sin base en la aprobación, la rama base; con una base que no verifica, FAIL
    # (caer a la rama base aflojaría la comprobación).
    base, reason = _approval_base(root, head) if head else _wr._resolve_base(root, None)
    changes = _wr._changes_vs_base(root, base) if base else None
    problems: list[str] = []
    for path in dict.fromkeys(paths):
        if (root / path).exists():
            problems.append(f"la ruta retirada sigue existiendo: {path}")
        elif changes is None:
            problems.append(f"no se pudo comprobar el borrado de {path}: {reason if not base else 'git diff falló'}")
        elif changes.get(path) != "D":
            problems.append(f"la ruta retirada no aparece borrada en el diff del módulo: {path}")
    return problems


def missing_artifacts(ac_body: str, root: Path, *, skip_retired: bool = False) -> list[str]:
    """Anti-fabrication: cited repo paths in an AC's evidence must exist on disk.

    Returns the cited file paths that resolve to no real file (by root-relative or
    path-suffix match). Only paths containing a `/` are considered, so narrative
    evidence ("verificación manual…"), commands and URLs are not flagged. A weak or
    careless model that invents a plausible `tests/x.py::test_y` to pass the length
    check is caught here regardless of which model produced it.

    TL FW-015: a citation immediately followed by the literal marker
    `(no es ruta del repo)` is exempt from the existence check — the opt-in
    escape hatch for `dir/file.ext`-shaped text that is not a repo path (a
    zip-internal path, a trading symbol, an installed-package module)."""
    content = _scan_text(ac_body)  # sin URL ni remotos SSH antes de buscar rutas
    missing: list[str] = []
    seen: set[str] = set()
    for m in PATH_RE.finditer(content):
        path = m.group(1)
        if path in seen:
            continue
        seen.add(path)
        if NOT_REPO_PATH_RE.match(content[m.end():].lstrip("`'\" \t")):
            continue
        if skip_retired and RETIRED_PREFIX_RE.search(content[:m.start()]):
            continue  # la comprueba retired_problems, al revés
        if not _cited_path_exists(path, root):
            missing.append(path)
    return missing


def module_section_lines(text: str, module_id: str) -> list[str] | None:
    """Lines of the H2 section whose title contains module_id as a whole token,
    heading included. None if no such section exists.

    FW-147: the client seal lives in the section body, not in the AC bullets, so
    the closure gate needs the raw section — not only its checkboxes. Extracting
    it once here keeps a single definition of "the module's section" for both
    readers (an AC parser and a seal check that disagreed on boundaries would be
    a hole, not a nuance)."""
    lines = text.splitlines()
    token = re.compile(r"\b" + re.escape(module_id) + r"\b")
    start = None
    for i, line in enumerate(lines):
        heading = H2_RE.match(line)
        if heading and token.search(heading.group("title")):
            start = i
            break
    if start is None:
        return None
    section = [lines[start]]
    for line in lines[start + 1:]:
        if H2_RE.match(line):
            break
        section.append(line)
    return section


def foreign_section_headers(
    text: str, module_id: str, registered_modules: list[str] | set[str] | tuple[str, ...]
) -> list[str]:
    """Líneas de cabecera H2 que son sección de otro módulo registrado (L6b, FW-337)."""
    own_token = re.compile(r"\b" + re.escape(module_id) + r"\b")
    accepted: set[str] = set()
    for other_id in registered_modules:
        if not isinstance(other_id, str) or not other_id.strip() or other_id == module_id:
            continue
        section = module_section_lines(text, other_id)
        if not section:
            continue
        header_line = section[0]
        heading = H2_RE.match(header_line)
        if not heading:
            continue
        title = heading.group("title").strip()
        if own_token.search(title):
            continue
        clean_title = title.replace(f"`{other_id}`", other_id)
        is_exact = clean_title == other_id
        is_paren = bool(re.match(r"^M[óo]dulo\b", clean_title)) and clean_title.endswith(f"({other_id})")
        is_sep = bool(
            re.fullmatch(rf"M[óo]dulo(?:\s+actual)?\s*[:\-–—]\s*{re.escape(other_id)}", clean_title)
        )
        if is_exact or is_paren or is_sep:
            accepted.add(header_line)
    if not accepted:
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if line in accepted and line not in seen:
            seen.add(line)
            ordered.append(line)
    return ordered


def module_ac_lines(text: str, module_id: str) -> list[tuple[bool, str]] | None:
    """AC entries (checked, body) under the H2 section whose title contains module_id
    as a whole token. None if no such section exists."""
    section = module_section_lines(text, module_id)
    if section is None:
        return None
    acs: list[tuple[bool, str]] = []
    for line in section[1:]:
        ac = AC_RE.match(line)
        if ac:
            acs.append((ac.group("mark").lower() == "x", ac.group("body")))
    return acs


def coverage(text: str, module_id: str) -> tuple[int, int] | None:
    """Return (checked, total) ACs for the module, or None if no section exists.

    Coverage is orthogonal to the evidence gate: a freshly opened module passes
    the evidence gate vacuously (0 checked ACs) yet is not closeable. The CLI
    surfaces this as a warning so OK is not misread as closeable.
    """
    acs = module_ac_lines(text, module_id)
    if acs is None:
        return None
    total = len(acs)
    checked = sum(1 for ch, _ in acs if ch)
    return (checked, total)


def stale_status_citations(ac_body: str, state: dict) -> list[str]:
    """Flag `module_statuses.<id>: <status>` evidence contradicting live state."""
    statuses = state.get("module_statuses")
    if not isinstance(statuses, dict):
        return []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for match in MODULE_STATUS_CITATION_RE.finditer(ac_body):
        module_id, cited = match.group(1), match.group(2)
        if (module_id, cited) in seen:
            continue
        seen.add((module_id, cited))
        actual = statuses.get(module_id)
        if actual is not None and actual != cited:
            problems.append(
                f"evidencia obsoleta: cita module_statuses.{module_id}: {cited} "
                f"pero el estado real es {actual}"
            )
    return problems


def evidence_gate(
    text: str,
    module_id: str,
    root: Path | None = None,
    state: dict | None = None,
) -> list[str]:
    """List of problems for all checked ACs of the module (empty == gate passes).

    When `root` is given, also flags cited repo artifacts that do not exist under
    it (anti-fabrication). When `root` is None the gate is the pure format check,
    independent of any filesystem — used by unit tests with synthetic paths.

    When `state` is given, also flags stale `module_statuses` citations whose
    value contradicts the live 03_project_state.json state.
    """
    acs = module_ac_lines(text, module_id)
    if acs is None:
        return [f"No se encontró sección de módulo que contenga '{module_id}' en 08_module_acceptance.md"]
    problems: list[str] = []
    for checked, body in acs:
        if not checked:
            continue
        problem = evidence_problem(body)
        if problem:
            problems.append(f"[x] {body[:60].strip()}… → {problem}")
            continue
        if root is not None:
            for path in missing_artifacts(body, root, skip_retired=True):
                problems.append(
                    f"[x] {body[:60].strip()}… → artefacto citado inexistente: {path} (si se retiró, "
                    "escribe retirado: <ruta>; si no es una ruta del repo, añade (no es ruta del repo))"
                )
            for retired in retired_problems(body, root, state, module_id):
                problems.append(f"[x] {body[:60].strip()}… → {retired}")
        if state is not None:
            for stale in stale_status_citations(body, state):
                problems.append(f"[x] {body[:60].strip()}… → {stale}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].strip():
        print("uso: validate_acceptance.py <module_id>")
        return 2
    # Un validador que lee estado o proyecciones mientras hay una publicación
    # multiarchivo abierta emite un veredicto sobre una vista a medio escribir.
    # Recupera la transacción pendiente antes de mirar nada (FW-174).
    try:
        import state_core as _state_core
        _state_core.StateManager.ensure_transaction_clean()
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"TRANSACCIÓN PENDIENTE: no se puede leer el estado canónico: {exc}")
        return 2
    module_id = argv[1].strip()
    if not ACCEPTANCE.exists():
        print(f"FALTA: {ACCEPTANCE}")
        return 2
    text = ACCEPTANCE.read_text(encoding="utf-8", errors="ignore")
    state: dict | None = None
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = None
    problems = evidence_gate(text, module_id, root=ROOT, state=state)
    if problems:
        print(f"EVIDENCE GATE: FAIL ({module_id})")
        for problem in problems:
            print(f"- {problem}")
        return 1
    cov = coverage(text, module_id)
    if cov is None:
        print(f"EVIDENCE GATE: OK ({module_id})")
        return 0
    checked, total = cov
    print(f"EVIDENCE GATE: OK ({module_id})")
    if total == 0:
        print("- AVISO: el módulo no tiene ACs definidos en 08_module_acceptance.md (cobertura 0/0); no es cerrable aún")
    elif checked < total:
        print(
            f"- AVISO: cobertura de ACs {checked}/{total} marcados [x]; "
            f"el evidence gate solo valida los [x] presentes. El módulo NO es cerrable "
            f"hasta cobertura 100% (cierre-modulo paso 1.c)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
