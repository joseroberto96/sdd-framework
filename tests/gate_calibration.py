#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registro de calibración de los 5 gates de enforcement (3.13.0).

Para cada gate, una **fixture known-good** (debe ALLOW) y una **known-bad**
(debe BLOCK), más el FW que cubre. El arnés en `tests/test_gate_calibration.py`
ejecuta ambas por gate; una divergencia rompe la calibración. El conjunto
canónico de los 5 está fijado por
`framework_doctor.validate_gate_calibration_coverage`: añadir un gate nuevo
sin calibrar es un build break, por diseño (mismo patrón que
`validate_focus_wiring`, `validate_decision_translation_core_wiring`, etc.):
toda capability lleva validator.

Disciplina de reuso: las fixtures llaman a las **funciones públicas** de cada
gate (`closure_gate`, `transition_problems`, `evidence_problem`,
`scan_potential_secrets`, subprocess del hook). NO reimplementan lógica de
gate ni reescriben suites enteras — los test files existentes
(`test_validate_transition.py`, `test_doctor_secret_scan.py`,
`test_framework_doctor.py::FrameworkClosureGateTests`,
`test_framework_doctor.py::FrameworkEvidenceGateTests`,
`test_pre_commit_hook.py`) siguen siendo la verdad; este registro es un
catálogo calibrado para el arnés.

Las known-good que históricamente falsearon (los "perros lobo" del gate) se
incluyen explícitamente como fixture good del gate que las sufrió:

- FW-037: validate_transition colapsa una secuencia legal multi-paso
  commiteada al final en un salto atómico `in_progress → completed` →
  falso `TRANSITION GATE: FAIL`. El known-good de validate_transition
  ejercita el camino `in_progress → completed` con state_log que lo
  respalda.
- FW-040/042/051: secret-scan se cuelga con ML/audio, marca SHAs de commit
  y prosa PEM, escanea mlx_models/.cache. El known-good de secret-scan
  ejercita cada uno de esos casos.
- FW-034: pre_commit usa `python3` del sistema, ignorando `.venv`. El
  known-good ejercita el camino con `.venv` presente.
- FW-038: pre_commit bloquea una adopción nueva sin tests (ImportError /
  exit 5 de unittest). El known-good ejercita `tests/` ausente y `tests/`
  vacío.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest.mock
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

# Importaciones perezosas dentro de cada fixture para no ejecutar el doctor
# ni tocar sys.path al import-time (el doctor importa este módulo a su vez).


# ---------------------------------------------------------------------------
# 1) validate_closure
# ---------------------------------------------------------------------------

def _closure_good() -> tuple[bool, str]:
    """Cierre válido: tier_2 con second_review, specs OK, 0 rejections, sin
    CLAR ABIERTA bloqueando, ejecutor no-limited. Reusa el patrón de
    FrameworkClosureGateTests.test_clean_state_allows_closure."""
    from validate_closure import closure_gate  # type: ignore  # noqa: E402
    state = {
        "work_tiers": {
            "M": {
                "tier": "tier_2",
                "requires_second_review": True,
                "rationale": "tier_2 con rollback y revisión de segundo paso",
                "classified_at": "2026-06-29T00:00:00Z",
                "classified_by": "manual",
                # FW-201: HARVEST GATE exige cosecha declarada.
                "harvest": {"classes": "none", "recorded_at": "2026-06-29T00:00:00Z"},
            }
        },
        "module_specs": {
            "M": {
                "data_model": "n/a",
                "api_contract": "n/a",
                "justification": "módulo interno sin entidad ni endpoint",
                "classified_at": "2026-06-29T00:00:00Z",
                "classified_by": "manual",
            }
        },
        "closure_rejections": [],
        "_clarifications": [],
        "_decisions": [{
            "id": "ADR-001", "title": "Tier 2", "context": "riesgo",
            "decision": "proceder", "module_id": "M",
        }],
        "workflow_receipts": {
            # FW-276 (5.2.2 L1): EXPERT GATE en el motor — tier_2 exige los
            # checkpoints domain_sweep y expert_audit sellados.
            "checkpoints": {
                kind: {"M": {"evidence": "barrido de dominio sin hallazgos bloqueantes",
                             "reference": "fixture-calibration"}}
                for kind in ("domain_sweep", "expert_audit")
            },
            "approvals": {
                "M": {
                    "status": "approved",
                    "kind": "explicit",
                    "module": "M",
                    "tier": "tier_2",
                    "approved_by": "builder",
                    "approval_evidence": "builder dijo adelante",
                    "risk_summary": "alto impacto con rollback",
                    "adr_refs": ["ADR-001"],
                    "basis": {"fixture": "calibration"},
                    "fingerprint": "fixture-approval",
                    "approved_at": "2026-06-29T00:00:00Z",
                }
            },
            "reviews": {
                "M": [
                    {
                        "status": "pass",
                        "result": "pass",
                        "module": "M",
                        "reviewer_id": "reviewer-2",
                        "implementer_id": "implementer-1",
                        "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
                        "diff_hash": "calibration",
                        "evidence": "fixture independiente",
                        "started_at": "2026-06-29T00:00:00Z",
                    }
                ]
            }
        },
    }
    problems = closure_gate(state, "M")
    return (len(problems) == 0, f"closure_gate(M) -> {problems}")


def _closure_bad() -> tuple[bool, str]:
    """Cierre inválido: sin work_tier (paso 0 falla). Reusa el patrón de
    FrameworkClosureGateTests.test_missing_work_tier_blocks."""
    from validate_closure import closure_gate  # type: ignore  # noqa: E402
    state = {
        "work_tiers": {},
        "module_specs": {},
        "closure_rejections": [],
        "_clarifications": [],
    }
    problems = closure_gate(state, "M")
    return (len(problems) > 0, f"closure_gate(M) -> {problems}")


# ---------------------------------------------------------------------------
# 2) validate_transition
# ---------------------------------------------------------------------------

def _transition_good() -> tuple[bool, str]:
    """FW-037: camino legal multi-paso commiteado al final. Diff
    `in_progress → completed` pero el state_log respalda el camino vía
    motor. Reusa test_validate_transition.test_compound_path_backed_by_log_is_legal."""
    from validate_transition import transition_problems  # type: ignore  # noqa: E402
    prev = {"module_statuses": {"M": "in_progress"}}
    new = {"module_statuses": {"M": "completed"}}
    logged = {"M": "completed"}
    problems = transition_problems(prev, new, logged_statuses=logged)
    return (len(problems) == 0, f"transition_problems -> {problems}")


def _transition_bad() -> tuple[bool, str]:
    """Hand-edit: `in_progress → completed` sin respaldo del state_log
    (último status registrado = `in_progress`). Reusa el patrón de
    test_validate_transition.test_jump_not_backed_by_log_is_illegal."""
    from validate_transition import transition_problems  # type: ignore  # noqa: E402
    prev = {"module_statuses": {"M": "in_progress"}}
    new = {"module_statuses": {"M": "completed"}}
    logged = {"M": "in_progress"}
    problems = transition_problems(prev, new, logged_statuses=logged)
    return (len(problems) > 0, f"transition_problems -> {problems}")


# ---------------------------------------------------------------------------
# 3) validate_acceptance
# ---------------------------------------------------------------------------

def _acceptance_good() -> tuple[bool, str]:
    """AC checked con evidencia concreta (≥20 chars, no pereza). Reusa el
    patrón de FrameworkEvidenceGateTests.test_valid_evidence_passes."""
    from validate_acceptance import evidence_problem  # type: ignore  # noqa: E402
    body = "- [x] AC1 — salida limpia — evidencia: `python3 .agent/scripts/framework_doctor.py` retorna 0 y la línea final dice 'Framework doctor: OK'."
    problem = evidence_problem(body)
    return (problem is None, f"evidence_problem -> {problem!r}")


def _acceptance_bad() -> tuple[bool, str]:
    """AC checked con token perezoso. Reusa el patrón de
    FrameworkEvidenceGateTests.test_lazy_tokens_fail."""
    from validate_acceptance import evidence_problem  # type: ignore  # noqa: E402
    body = "- [x] AC1 — salida limpia — evidencia: hecho"
    problem = evidence_problem(body)
    return (problem is not None, f"evidence_problem -> {problem!r}")


# ---------------------------------------------------------------------------
# 4) secret-scan (scan_potential_secrets en framework_doctor)
# ---------------------------------------------------------------------------

def _secret_scan_good() -> tuple[bool, str]:
    """FW-040/042/051: el escáner no debe gritar lobo en (a) cache
    gitignored, (b) SHA de commit documentado, (c) prosa tipo PEM. Reusa
    test_doctor_secret_scan.TestSecretScanFW042 (los 3 sub-casos en una
    sola ejecución del escáner)."""
    import framework_doctor as doctor  # type: ignore  # noqa: E402
    tmp = tempfile.mkdtemp()
    try:
        sandbox = Path(tmp)
        # Mini repo git para que el escáner use git ls-files (FW-042)
        subprocess.run(["git", "init"], cwd=sandbox, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=sandbox, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=sandbox, check=True, capture_output=True)
        (sandbox / ".gitignore").write_text("mlx_models/\n", encoding="utf-8")
        # (a) cache gitignored con 40-hex — el escáner respeta git ls-files
        (sandbox / "mlx_models").mkdir()
        (sandbox / "mlx_models" / "blob.metadata").write_text(
            ("ab" * 20) + "\n", encoding="utf-8")
        # (b) SHA de commit documentado — allowlist con keyword "publicaci"/"checkout"
        sha = "cbb3d5602a972a18eae24ef99" + "beb90bb90096244"
        (sandbox / "release.md").write_text(
            f"- Última publicación: `{sha}`\n- git checkout `{sha}`\n",
            encoding="utf-8")
        # (c) prosa tipo PEM — vive en .agent/docs/field_report.md (FW-051
        # exime el field report INTERNO del template: documentar fricción del
        # propio escáner citando BEGIN PRIVATE KEY no debe bloquear el commit
        # ni entrenar --no-verify).
        fr = sandbox / ".agent" / "docs"
        fr.mkdir(parents=True)
        (fr / "field_report.md").write_text(
            "El escáner cita BEGIN PRIVATE KEY como ejemplo de patrón que "
            "bloquearía el commit sin la exención.\n",
            encoding="utf-8")
        subprocess.run(
            ["git", "add", ".gitignore", "release.md", ".agent/docs/field_report.md"],
            cwd=sandbox, check=True, capture_output=True)
        old_root = doctor.ROOT
        doctor.ROOT = sandbox
        try:
            errors: list[str] = []
            doctor.scan_potential_secrets(errors)
        finally:
            doctor.ROOT = old_root
        return (errors == [], f"scan_potential_secrets -> {errors}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _scan_lines(lines: dict[str, str]) -> dict[str, list[str]]:
    """Escanea cada texto en su propio fichero de un repo temporal y devuelve
    los errores por texto (5.3.1 L2.1: el escáner es la unidad calibrada)."""
    import framework_doctor as doctor  # type: ignore  # noqa: E402
    tmp = tempfile.mkdtemp()
    try:
        sandbox = Path(tmp)
        subprocess.run(["git", "init"], cwd=sandbox, check=True, capture_output=True)
        out: dict[str, list[str]] = {}
        for name, text in lines.items():
            (sandbox / name).write_text(text + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=sandbox, check=True, capture_output=True)
        old_root = doctor.ROOT
        doctor.ROOT = sandbox
        try:
            errors: list[str] = []
            doctor.scan_potential_secrets(errors)
        finally:
            doctor.ROOT = old_root
        for name in lines:
            out[name] = [e for e in errors if f" {name}:" in e]
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _secret_scan_good_entity_ids() -> tuple[bool, str]:
    """FW-160 y 5.3.1 L2.1 — known-good: identificadores derivados de hash.

    `combo-<hex>`, `evt-<hex>`, campos `*_hash` y un hexadecimal suelto de 32
    caracteres no son secretos: con la regla invertida de 5.3.1 L2.1 solo se
    marca un formato de credencial conocido o un valor con una clave que nombra
    un secreto. La evidencia de un bug de auditoría necesita citarlos para ser
    reproducible.
    """
    permitidos = {
        "a.md": "combo-a1b2c3d4e5 reproduce el fallo de ranking",
        "b.md": "evt-0123456789ab registrado en la bitacora",
        "c.yml": "combo_hash: a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "d.md": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    }
    found = _scan_lines(permitidos)
    fallos = [permitidos[n] for n, errs in found.items() if errs]
    return (not fallos, f"deberían estar permitidos y no lo están: {fallos}")


def _secret_scan_bad_widening_does_not_open_the_door() -> tuple[bool, str]:
    """FW-160 y 5.3.1 L2.1 — la salvaguarda: lo peligroso SIGUE bloqueando.

    Una asignación entrecomillada, un valor de alta entropía con una clave que
    nombra un secreto y una credencial con formato bloquean, aunque el hex
    suelto haya dejado de hacerlo.
    """
    peligrosos = {
        "a.py": "api_key = " + '"A' + "k" * 24 + '9"',            # asignación (regla 1)
        "b.yml": "token: " + "a1b2c3d4e5f6" + "0718293a4b5c6d7e8f90",  # valor con clave
        "c.py": "GH = '" + "gh" + "p_" + "A" * 36 + "'",           # formato conocido
    }
    found = _scan_lines(peligrosos)
    colados = [peligrosos[n] for n, errs in found.items() if not errs]
    return (not colados, f"el escáner dejó pasar lo que no debía: {colados}")


def _secret_scan_bad() -> tuple[bool, str]:
    """FW-040 salvaguarda: un secreto REAL en archivo versionado SÍ debe
    detectarse. Reusa test_doctor_secret_scan.test_real_secrets_still_detected."""
    import framework_doctor as doctor  # type: ignore  # noqa: E402
    tmp = tempfile.mkdtemp()
    try:
        sandbox = Path(tmp)
        subprocess.run(["git", "init"], cwd=sandbox, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=sandbox, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=sandbox, check=True, capture_output=True)
        (sandbox / "config.py").write_text(
            "api_key" + " = " + '"' + "A" + "k" * 24 + '9"' + "\n",
            encoding="utf-8")
        subprocess.run(["git", "add", "config.py"], cwd=sandbox, check=True, capture_output=True)
        old_root = doctor.ROOT
        doctor.ROOT = sandbox
        try:
            errors: list[str] = []
            doctor.scan_potential_secrets(errors)
        finally:
            doctor.ROOT = old_root
        return (len(errors) > 0, f"scan_potential_secrets -> {errors}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5) pre_commit.sh
# ---------------------------------------------------------------------------

def _make_pre_commit_sandbox(*, tests_dir: str, venv: bool, doctor_exit: int) -> Path:
    """Construye un sandbox para correr pre_commit.sh con stubs. Reusa el
    patrón de test_pre_commit_hook.PreCommitHookTests._sandbox."""
    real_hook = ROOT / ".agent" / "scripts" / "pre_commit.sh"
    tmp = tempfile.mkdtemp()
    sb = Path(tmp)
    scripts = sb / ".agent" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(real_hook, scripts / "pre_commit.sh")

    def _exec(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IEXEC)

    _exec(scripts / "framework_doctor.py", f"import sys\nsys.exit({doctor_exit})\n")
    _exec(scripts / "validate_transition.py", "import sys\nsys.exit(0)\n")
    if tests_dir == "empty":
        (sb / "tests").mkdir()
        (sb / "tests" / "__init__.py").write_text("")
    elif tests_dir == "passing":
        (sb / "tests").mkdir()
        (sb / "tests" / "__init__.py").write_text("")
        (sb / "tests" / "test_ok.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n"
        )
    # tests_dir == "absent": no tests/ directory at all
    if venv:
        venvbin = sb / ".venv" / "bin"
        venvbin.mkdir(parents=True)
        _exec(
            venvbin / "python",
            '#!/usr/bin/env bash\nexec python3 "$@"\n',
        )
    return sb


def _run_pre_commit(sb: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", ".agent/scripts/pre_commit.sh"],
        cwd=sb, capture_output=True, text=True,
        env={**os.environ, "FRAMEWORK_PYTHON": ""},
    )


def _pre_commit_good_venv() -> tuple[bool, str]:
    """FW-034: el hook debe usar `.venv/bin/python` cuando exista, sin
    bloquear el commit cuando los tests pasan y `.venv` está presente.
    Equivale a test_pre_commit_hook.test_uses_venv_python_when_present +
    test_passing_tests_still_succeed."""
    sb = _make_pre_commit_sandbox(tests_dir="passing", venv=True, doctor_exit=0)
    try:
        res = _run_pre_commit(sb)
        return (res.returncode == 0, f"pre_commit(.venv, passing, doctor=0) -> rc={res.returncode}\n{res.stdout}\n{res.stderr}")
    finally:
        shutil.rmtree(sb, ignore_errors=True)


def _pre_commit_good_no_tests() -> tuple[bool, str]:
    """FW-038: el hook no debe bloquear una adopción nueva con `tests/`
    ausente o vacío (ImportError / exit 5 de unittest). Equivale a
    test_pre_commit_hook.test_missing_tests_dir_does_not_block_commit +
    test_empty_tests_dir_does_not_block_commit."""
    sb = _make_pre_commit_sandbox(tests_dir="absent", venv=False, doctor_exit=0)
    try:
        res = _run_pre_commit(sb)
        return (res.returncode == 0, f"pre_commit(no tests, doctor=0) -> rc={res.returncode}\n{res.stdout}\n{res.stderr}")
    finally:
        shutil.rmtree(sb, ignore_errors=True)


def _pre_commit_bad_doctor_fails() -> tuple[bool, str]:
    """Known-bad del hook: el doctor (stub) sale != 0 → el commit debe
    bloquearse. Reusa la lógica de que el hook corre doctor primero."""
    sb = _make_pre_commit_sandbox(tests_dir="passing", venv=False, doctor_exit=1)
    try:
        res = _run_pre_commit(sb)
        return (res.returncode != 0, f"pre_commit(doctor=1) -> rc={res.returncode}\n{res.stdout}\n{res.stderr}")
    finally:
        shutil.rmtree(sb, ignore_errors=True)


# ---------------------------------------------------------------------------
# Registro canónico
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6) validate_checkpoint (3.19.0, FW-074)
# ---------------------------------------------------------------------------

_CHECKPOINT_GOOD = """Decisión: dónde guardan sus datos los usuarios de tu app.
Qué significa para ti: si la app pierde la conexión, ¿el usuario puede seguir trabajando o se queda bloqueado?
Analogía: es como elegir entre una libreta que llevas encima (funciona sin internet) y un archivador en la oficina (siempre ordenado, pero hay que ir hasta allí).
Recomendación: la libreta (guardar en el dispositivo) — tus usuarios trabajan en el campo sin señal, y perder una anotación les cuesta dinero.
Opciones:
A) Guardar en el dispositivo — si eliges esto: funciona sin internet, pero sincronizar entre varios aparatos costará un módulo extra más adelante.
B) Guardar en la nube — si eliges esto: todo siempre sincronizado, pero sin señal la app no sirve y pagas un servicio mensual.
"""

_CHECKPOINT_BAD = "¿Postgres o SQLite?"


def _checkpoint_good() -> tuple[bool, str]:
    """Checkpoint con la plantilla completa: debe ALLOW."""
    from validate_checkpoint import checkpoint_problems  # type: ignore  # noqa: E402
    problems = checkpoint_problems(_CHECKPOINT_GOOD)
    return (not problems, "; ".join(problems))


def _checkpoint_bad() -> tuple[bool, str]:
    """Pregunta técnica cruda sin traducción: debe BLOCK."""
    from validate_checkpoint import checkpoint_problems  # type: ignore  # noqa: E402
    problems = checkpoint_problems(_CHECKPOINT_BAD)
    return (bool(problems), "aceptó una pregunta técnica cruda sin plantilla")


# ---------------------------------------------------------------------------
# 7) guard_destructive_command (FW-082, ADR-051)
# ---------------------------------------------------------------------------

def _guard_good() -> tuple[bool, str]:
    """Anti falso positivo (el villano histórico FW-034/037/040): un borrado
    cotidiano de dir efímero DEBE pasar sin marcador. `is_destructive`
    devuelve (blocked, reason); allowed == not blocked."""
    from guard_destructive_command import is_destructive  # type: ignore  # noqa: E402
    blocked, reason = is_destructive("rm -rf node_modules dist")
    return (not blocked, f"is_destructive -> blocked={blocked}, reason={reason!r}")


def _guard_bad() -> tuple[bool, str]:
    """El caso Lemkin: empujar el esquema a la BD DEBE bloquearse."""
    from guard_destructive_command import is_destructive  # type: ignore  # noqa: E402
    blocked, reason = is_destructive("prisma db push")
    return (blocked, f"is_destructive -> blocked={blocked}, reason={reason!r}")


def _guard_good_carrier_con_heredoc() -> tuple[bool, str]:
    """Known-good de FW-228: bloqueado de verdad durante la auditoría. El cuerpo
    de un heredoc que alimenta `git commit` es dato aunque mencione un push
    forzado y la línea encadene otros comandos."""
    from guard_destructive_command import is_destructive  # type: ignore  # noqa: E402
    blocked, reason = is_destructive(
        "git add a && git commit -q -F - <<'EOF'\nprohibir git push --force salvo lease\nEOF\n"
        "echo \"exit=$?\"; git log --oneline -1"
    )
    return (not blocked, f"is_destructive -> blocked={blocked}, reason={reason!r}")


def _guard_bad_separador_tras_carrier() -> tuple[bool, str]:
    """Known-bad de FW-228: un `;` pegado tras un carrier escondía el comando."""
    from guard_destructive_command import is_destructive  # type: ignore  # noqa: E402
    blocked, reason = is_destructive("git commit -m 'x'; git reset --hard")
    return (blocked, f"is_destructive -> blocked={blocked}, reason={reason!r}")


def _guard_bad_heredoc_ejecutado() -> tuple[bool, str]:
    """Known-bad de FW-228: un heredoc cuya salida acaba en un shell es código."""
    from guard_destructive_command import is_destructive  # type: ignore  # noqa: E402
    blocked, reason = is_destructive("sh -c \"$(cat <<'EOF'\nrm -rf ~\nEOF\n)\"")
    return (blocked, f"is_destructive -> blocked={blocked}, reason={reason!r}")


def _guard_bad_heredoc_expansivo_gigante() -> tuple[bool, str]:
    """Known-bad de FW-237: un heredoc expansivo de miles de líneas agotaba el
    tiempo del hook y el `rm` de detrás pasaba sin veredicto. Debe bloquear, y
    rápido: el umbral holgado solo detecta un término cuadrático."""
    import time

    from guard_destructive_command import is_destructive  # type: ignore  # noqa: E402
    cuerpo = "\n".join(f"linea {i} $(date)" for i in range(3000))
    inicio = time.monotonic()
    blocked, reason = is_destructive(f"cat <<EOF > out.txt\n{cuerpo}\nEOF\nrm -rf ~")
    transcurrido = time.monotonic() - inicio
    return (blocked and transcurrido < 2.0, f"is_destructive -> blocked={blocked} en {transcurrido:.1f}s, reason={reason!r}")


# --------------------------------------------------------------- validate_no_stubs
def _sandbox_source(contenido: str) -> tuple[Path, tempfile.TemporaryDirectory]:
    """Escribe un fichero fuente aislado (fuera de `tests/`, que el gate ignora)."""
    tmp = tempfile.TemporaryDirectory()
    src = Path(tmp.name) / "src"
    src.mkdir()
    fichero = src / "service.py"
    fichero.write_text(contenido, encoding="utf-8")
    return fichero, tmp


def _no_stubs_good() -> tuple[bool, str]:
    """Known-good: implementación real + una excepción declarada con `stub-ok:`.

    El perro lobo de este gate es el dato de ejemplo legítimo (una demo, un
    fixture embebido): tiene que poder declararse sin desactivar el gate entero.
    """
    import validate_no_stubs as vns
    fichero, tmp = _sandbox_source(
        "def call_llm(prompt):\n"
        "    try:\n"
        "        return client.generate(prompt)\n"
        "    except Exception as exc:\n"
        "        raise LLMUnavailable(str(exc)) from exc\n"
        "\n"
        "def demo():  # stub-ok: dato de la demo pública, fuera del flujo real\n"
        "    return {'email': 'demo@example.com'}\n"
    )
    try:
        hallazgos = vns.scan_file(fichero, fichero.parent)
        return (not hallazgos, f"hallazgos inesperados: {hallazgos}")
    finally:
        tmp.cleanup()


def _no_stubs_bad() -> tuple[bool, str]:
    """Known-bad: los dos patrones exactos de Example Service (FW-145).

    Dato cableado presentado como extracción real, y una captura ancha que
    convierte un LLM caído en una respuesta vacía con éxito aparente.
    """
    import validate_no_stubs as vns
    fichero, tmp = _sandbox_source(
        "def contacto():\n"
        "    return {'email': 'candidato@example.com'}\n"
        "\n"
        "def call_llm(prompt):\n"
        "    try:\n"
        "        return client.generate(prompt)\n"
        "    except Exception:\n"
        "        return {}\n"
    )
    try:
        hallazgos = vns.scan_file(fichero, fichero.parent)
        tiene_dato = any("dato fabricado" in h for h in hallazgos)
        tiene_failopen = any("fail-open" in h for h in hallazgos)
        return (
            tiene_dato and tiene_failopen,
            f"debía detectar dato fabricado y fail-open; obtuvo {hallazgos}",
        )
    finally:
        tmp.cleanup()


def _no_stubs_good_logs_and_reraises() -> tuple[bool, str]:
    """Known-good de FW-152 — el perro lobo del patrón nuevo.

    Registrar y RELANZAR es manejo correcto; degradar a datos reales cacheados
    también. Ninguno puede bloquear, o el gate entrena a saltárselo. Incluye
    además el fallback deliberado declarado con `stub-ok` sobre el propio
    `return` — donde un desarrollador lo escribiría de verdad.
    """
    import validate_no_stubs as vns
    fichero, tmp = _sandbox_source(
        "def a(p):\n"
        "    try:\n"
        "        return client.generate(p)\n"
        "    except Exception as exc:\n"
        "        logger.error('llm caido: %s', exc)\n"
        "        raise\n"
        "\n"
        "def b(p):\n"
        "    try:\n"
        "        return client.generate(p)\n"
        "    except Exception:\n"
        "        logger.warning('degradando a cache')\n"
        "        return {'ok': True, 'data': cached_rows}\n"
        "\n"
        "def c(p):\n"
        "    try:\n"
        "        return client.generate(p)\n"
        "    except Exception:\n"
        "        logger.warning('sin datos')\n"
        "        return {'items': [], 'total': 0}   # stub-ok: contrato publico exige lista vacia; el 503 lo emite la capa API\n"
    )
    try:
        hallazgos = vns.scan_file(fichero, fichero.parent)
        return (not hallazgos, f"hallazgos inesperados: {hallazgos}")
    finally:
        tmp.cleanup()


def _no_stubs_bad_logs_then_empty() -> tuple[bool, str]:
    """Known-bad de FW-152, copiado literalmente de Example Service `src/llm.py:64-72`.

    Es el fail-open que causó BUG-001…004 y que la primera versión del gate
    dejaba pasar: una línea de log bastaba para esquivarlo, y el literal vacío
    multilínea agotaba el tope de líneas del cuerpo antes de poder juzgarse.
    """
    import validate_no_stubs as vns
    fichero, tmp = _sandbox_source(
        "def extract(prompt):\n"
        "    try:\n"
        "        return json.loads(response_text)\n"
        "    except Exception as e:\n"
        "        logger.error(f'Error calling Ollama API: {e}')\n"
        "        # Return empty structure on failure\n"
        "        return {\n"
        "            'skills': [],\n"
        "            'experiences': [],\n"
        "            'education': []\n"
        "        }\n"
    )
    try:
        hallazgos = vns.scan_file(fichero, fichero.parent)
        return (
            any("fail-open" in h for h in hallazgos),
            f"debía detectar el fail-open que loguea y devuelve vacío; obtuvo {hallazgos}",
        )
    finally:
        tmp.cleanup()


def _no_stubs_good_patrones_legitimos() -> tuple[bool, str]:
    """Known-good de FW-161, medido contra el código real de los 5 proyectos.

    Tres patrones que la primera versión marcaba y no debía: `NotImplementedError`
    (falla ruidosamente, es lo contrario de un stub — 12 FP solo en Example Lab
    v2), el `except` que envuelve una limpieza sin valor de retorno, y un
    `raise` explícito dentro del manejador.
    """
    import validate_no_stubs as vns
    fichero, tmp = _sandbox_source(
        "class Base:\n"
        "    def mine(self, datasets):\n"
        "        raise NotImplementedError('interfaz legada, ver ADR-005')\n"
        "\n"
        "def cerrar(recorder):\n"
        "    try:\n"
        "        recorder.shutdown()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    try:
        hallazgos = vns.scan_file(fichero, fichero.parent)
        return (not hallazgos, f"hallazgos inesperados: {hallazgos}")
    finally:
        tmp.cleanup()


def _no_stubs_bad_worktree_con_carpeta_nueva() -> tuple[bool, str]:
    """Known-bad de FW-231: el cierre real (`stub_problems`) no puede quedar ciego.

    Proyecto dentro de `.claude/worktrees/` (ancestro con nombre excluido) y el
    fail-open en una carpeta nueva sin seguimiento, que `git status` colapsa.
    Antes de 4.39.2 cualquiera de las dos condiciones dejaba el gate en OK sin
    escanear un solo fichero.
    """
    import validate_no_stubs as vns
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / "repo" / ".claude" / "worktrees" / "mod-a"
    fichero = root / "src" / "nuevo_modulo" / "lector.py"
    fichero.parent.mkdir(parents=True)
    fichero.write_text(
        "def leer(fila):\n"
        "    try:\n"
        "        return json.loads(fila)\n"
        "    except Exception:\n"
        "        return {}\n",
        encoding="utf-8",
    )
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    try:
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, env=env, capture_output=True)
        razones = vns.stub_problems("M1", root)
        return (
            bool(razones) and "fail-open" in razones[0],
            f"el cierre debía bloquear el fail-open del worktree; obtuvo {razones}",
        )
    finally:
        tmp.cleanup()


def _no_stubs_bad_teardown_no_es_coartada() -> tuple[bool, str]:
    """La exención de teardown no puede tapar un fail-open de datos (FW-161).

    En cuanto el `try` obtiene un valor —asignación o `return`— vuelve a ser
    candidato: es el caso que el gate existe para cazar.
    """
    import validate_no_stubs as vns
    fichero, tmp = _sandbox_source(
        "def leer(fila):\n"
        "    try:\n"
        "        datos = json.loads(fila.snapshot)\n"
        "        return datos\n"
        "    except Exception:\n"
        "        return {}\n"
    )
    try:
        hallazgos = vns.scan_file(fichero, fichero.parent)
        return (
            any("fail-open" in h for h in hallazgos),
            f"un fail-open con asignación debe seguir bloqueando; obtuvo {hallazgos}",
        )
    finally:
        tmp.cleanup()


GATES: list[dict] = [
    {
        "name": "validate_closure",
        "fw": "ADR-013, ADR-020, ADR-024, FW-031",
        "good": _closure_good,
        "bad": _closure_bad,
    },
    {
        "name": "validate_transition",
        "fw": "FW-037, ADR-021, ADR-029",
        "good": _transition_good,
        "bad": _transition_bad,
    },
    {
        "name": "validate_acceptance",
        "fw": "ADR-017, ADR-025, FW-021",
        "good": _acceptance_good,
        "bad": _acceptance_bad,
    },
    {
        "name": "secret_scan",
        "fw": "FW-040, FW-042, FW-051, FW-160, ADR-030",
        "good": [_secret_scan_good, _secret_scan_good_entity_ids],
        # 2 bad: el secreto real en fichero versionado (FW-040) y la salvaguarda
        # del ensanche de FW-160, que FW-143 exigió antes de tocar este gate.
        "bad": [_secret_scan_bad, _secret_scan_bad_widening_does_not_open_the_door],
    },
    {
        "name": "pre_commit",
        "fw": "FW-034, FW-038, ADR-017",
        # 2 good (los dos "perros lobo" históricos): .venv presente y 0 tests
        "good": [_pre_commit_good_venv, _pre_commit_good_no_tests],
        "bad": _pre_commit_bad_doctor_fails,
    },
    {
        "name": "validate_checkpoint",
        "fw": "FW-074, ADR-046",
        "good": _checkpoint_good,
        "bad": _checkpoint_bad,
    },
    {
        "name": "validate_no_stubs",
        "fw": "FW-145, FW-146, FW-152, FW-161, FW-231",
        # 3 good: el opt-out declarado (FW-145), el manejador que loguea pero
        # relanza o degrada con datos reales (FW-152) y los patrones legítimos
        # medidos en los 5 proyectos (FW-161: NotImplementedError y teardown).
        # 4 bad: los tres por patrón y el cierre real sobre un worktree con
        # carpeta nueva sin seguimiento (FW-231).
        "good": [_no_stubs_good, _no_stubs_good_logs_and_reraises, _no_stubs_good_patrones_legitimos],
        "bad": [
            _no_stubs_bad,
            _no_stubs_bad_logs_then_empty,
            _no_stubs_bad_teardown_no_es_coartada,
            _no_stubs_bad_worktree_con_carpeta_nueva,
        ],
    },
    {
        "name": "guard_destructive_command",
        "fw": "FW-082, ADR-051, FW-228, FW-237",
        "good": [_guard_good, _guard_good_carrier_con_heredoc],
        "bad": [_guard_bad, _guard_bad_separador_tras_carrier, _guard_bad_heredoc_ejecutado,
                _guard_bad_heredoc_expansivo_gigante],
    },
]


def run_harness(gates: list[dict] | None = None) -> list[str]:
    """Ejecuta cada par good→assert ALLOW, bad→assert BLOCK.

    Devuelve una lista de mensajes de falla (vacía == calibración OK). El
    arnés es paramétrico: el meta-test en `tests/test_gate_calibration.py`
    le pasa un gate mockeado para probar que falla de verdad.
    """
    if gates is None:
        gates = GATES
    failures: list[str] = []
    for gate in gates:
        name = gate["name"]
        good_fns = gate["good"] if isinstance(gate["good"], list) else [gate["good"]]
        for idx, good in enumerate(good_fns, 1):
            try:
                allowed, detail = good()
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}.good#{idx} raised: {exc!r}")
                continue
            if not allowed:
                failures.append(f"{name}.good#{idx} debe ALLOW pero BLOQUEÓ: {detail}")
        # Simétrico con `good`: un gate puede tener varias fixtures known-bad.
        # Antes solo aceptaba una, así que calibrar un patrón nuevo obligaba a
        # elegir cuál de los dos casos peligrosos se probaba (FW-152).
        bad_fns = gate["bad"] if isinstance(gate["bad"], list) else [gate["bad"]]
        for idx, bad in enumerate(bad_fns, 1):
            try:
                blocked, detail = bad()
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}.bad#{idx} raised: {exc!r}")
                continue
            if not blocked:
                failures.append(f"{name}.bad#{idx} debe BLOCK pero ALLOW: {detail}")
    return failures


# API para el doctor: conjunto canónico de nombres.
CANONICAL_GATE_NAMES: frozenset[str] = frozenset(g["name"] for g in GATES)
