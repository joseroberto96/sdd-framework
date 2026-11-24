"""Contrato portable para supervisar subagentes sin falsos terminales."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import tempfile
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCTOR_PATH = ROOT / ".agent" / "scripts" / "framework_doctor.py"
WIRING_PATH = ROOT / ".agent" / "scripts" / "doctor_wiring.json"
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
ADAPTERS_DIR = ROOT / ".agent" / "adapters"

# `README.md` y `_capability_matrix.md` son documentación de la carpeta, no adaptadores;
# `code_graph.md` adapta una herramienta (grafo de código), no un harness que despache
# subagentes. Todo lo demás es un harness y debe declarar el contrato de espera.
NO_SON_ADAPTADORES_DE_HARNESS = {"README.md", "_capability_matrix.md", "code_graph.md"}
ADAPTADORES_DE_HARNESS = {
    path.name
    for path in ADAPTERS_DIR.glob("*.md")
    if path.name not in NO_SON_ADAPTADORES_DE_HARNESS
}


def load_doctor():
    spec = importlib.util.spec_from_file_location("framework_doctor_wait_lifecycle", DOCTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SubagentWaitLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doctor = load_doctor()

    def setUp(self):
        self.original_root = self.doctor.ROOT
        self.original_spec = self.doctor._WIRING_SPEC_CACHE

    def tearDown(self):
        self.doctor.ROOT = self.original_root
        self.doctor._WIRING_SPEC_CACHE = self.original_spec

    def _feature(self):
        spec = json.loads(WIRING_PATH.read_text(encoding="utf-8"))
        return copy.deepcopy(spec["features"]["subagent_wait_lifecycle"])

    @staticmethod
    def _needles(feature, skip=None):
        skip = set(skip or ())
        values = []
        for file_spec in feature["files"]:
            for needle in file_spec.get("needles", []):
                values.extend(needle.get("all", []))
                values.extend(needle.get("any", []))
                if "needle" in needle:
                    values.append(needle["needle"])
        return [value for value in values if value not in skip]

    def _fixture(self, omit_files=(), omit_needles=()):
        feature = self._feature()
        root = Path(tempfile.mkdtemp())
        omitted = set(omit_files)
        regex_anchors = {
            ".agent/workflows/preflight-review.md": (
                "No se ejecuta `interrupt`\n"
                "ni `close_agent` automáticamente; timeout no cancela al subagente."
            ),
            ".agent/adapters/codex.md": (
                "El adaptador no debe llamar `interrupt` ni `close_agent` "
                "automáticamente como consecuencia de WAIT_TIMEOUT_NONTERMINAL."
            ),
        }
        for adapter in ADAPTADORES_DE_HARNESS - {"codex.md"}:
            regex_anchors[f".agent/adapters/{adapter}"] = (
                "El adaptador no debe llamar `interrupt` ni `close_agent` "
                "automáticamente por una espera agotada."
            )
        for file_spec in feature["files"]:
            path = file_spec["path"]
            if path in omitted:
                continue
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            needles = []
            for needle in file_spec.get("needles", []):
                needles.extend(needle.get("all", []))
                needles.extend(needle.get("any", []))
                if "needle" in needle:
                    needles.append(needle["needle"])
            target.write_text(
                "\n".join(value for value in needles if value not in set(omit_needles))
                + "\n"
                + regex_anchors.get(path, "")
                + "\n",
                encoding="utf-8",
            )
        self.doctor.ROOT = root
        self.doctor._WIRING_SPEC_CACHE = {"features": {"subagent_wait_lifecycle": feature}}
        return root

    def test_real_template_has_a_valid_nonterminal_wait_contract(self):
        errors = []
        self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
        self.assertEqual(errors, [], errors)

    def test_missing_portable_contract_is_rejected(self):
        self._fixture(omit_files={".agent/docs/extended_protocol.md"})
        errors = []
        self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
        self.assertTrue(any("extended_protocol.md" in error for error in errors), errors)

    def test_codex_adapter_without_nonterminal_marker_is_rejected(self):
        self._fixture(omit_needles={"WAIT_TIMEOUT_NONTERMINAL"})
        errors = []
        self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
        self.assertTrue(any("WAIT_TIMEOUT_NONTERMINAL" in error for error in errors), errors)

    def test_codex_small_task_policy_is_present_and_linked_to_wait(self):
        text = (ROOT / ".agent/adapters/codex.md").read_text(encoding="utf-8")
        self.assertIn("## CODEX_SMALL_TASK_DELEGATION", text)
        self.assertIn("## SUBAGENT_WAIT_LIFECYCLE", text)
        self.assertIn("encargos pequeños y autosuficientes", text)
        self.assertIn("En Codex no se delega por defecto", text)
        self.assertIn("delegación sin historial", text)
        self.assertIn('fork_turns: "none"', text)
        self.assertIn("herramienta disponible", text)
        self.assertIn("20 min sin progreso observable", text)
        self.assertIn("`NEEDS_ATTENTION`", text)
        self.assertIn("`close_agent`", text)
        self.assertIn("No inferir telemetría privada ni prometer ausencia de compactación", text)

    def test_codex_sections_survive_real_sync_check_path(self):
        result = subprocess.run(
            ["python3", ".agent/scripts/sync_claude_commands.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "Sync check: OK")
        text = (ROOT / ".agent/adapters/codex.md").read_text(encoding="utf-8")
        self.assertIn("## CODEX_SMALL_TASK_DELEGATION", text)
        self.assertIn("## SUBAGENT_WAIT_LIFECYCLE", text)

    def test_real_codex_sync_owns_wrappers_not_adapter_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "framework"
            (fixture / ".agent/scripts").mkdir(parents=True)
            (fixture / ".agent/adapters").mkdir(parents=True)
            (fixture / ".agents/skills").mkdir(parents=True)
            shutil.copy2(ROOT / ".agent/scripts/sync_claude_commands.py", fixture / ".agent/scripts/sync_claude_commands.py")
            shutil.copytree(ROOT / ".agent/workflows", fixture / ".agent/workflows")
            shutil.copytree(ROOT / ".agent/skills", fixture / ".agent/skills")
            shutil.copy2(ROOT / ".agent/adapters/codex.md", fixture / ".agent/adapters/codex.md")
            shutil.copytree(ROOT / ".agents/skills", fixture / ".agents/skills", dirs_exist_ok=True)
            source = fixture / ".agent/adapters/codex.md"
            before = source.read_bytes()
            result = subprocess.run(
                ["python3", "-B", ".agent/scripts/sync_claude_commands.py", "--codex-only"],
                cwd=fixture,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r"^Codex wrappers sincronizados \(solo Codex\): \d+$")
            self.assertEqual(source.read_bytes(), before)
            self.assertIn("CODEX_SMALL_TASK_DELEGATION", source.read_text(encoding="utf-8"))
            self.assertIn("SUBAGENT_WAIT_LIFECYCLE", source.read_text(encoding="utf-8"))

    def test_codex_policy_is_not_imposed_on_other_adapters_or_executor(self):
        for path in sorted(ADAPTERS_DIR.glob("*.md")):
            if path.name in {"codex.md", *NO_SON_ADAPTADORES_DE_HARNESS}:
                continue
            self.assertNotIn(
                "CODEX_SMALL_TASK_DELEGATION",
                path.read_text(encoding="utf-8"),
                path.name,
            )
        executor = (ROOT / ".agent/subagents/executor.md").read_text(encoding="utf-8")
        self.assertNotIn("CODEX_SMALL_TASK_DELEGATION", executor)

    def test_contract_without_pass_marker_is_rejected(self):
        self._fixture(omit_needles={"PASS"})
        errors = []
        self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
        self.assertTrue(any("PASS" in error for error in errors), errors)

    def test_contract_without_fail_marker_is_rejected(self):
        self._fixture(omit_needles={"FAIL"})
        errors = []
        self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
        self.assertTrue(any("FAIL" in error for error in errors), errors)

    def test_preflight_reviewer_without_independent_isolation_marker_is_rejected(self):
        self._fixture(omit_needles={"INDEPENDENT_SUBAGENT_NO_ISOLATION"})
        errors = []
        self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
        self.assertTrue(any("INDEPENDENT_SUBAGENT_NO_ISOLATION" in error for error in errors), errors)

    def test_preflight_reviewer_without_sequential_isolation_marker_is_rejected(self):
        self._fixture(omit_needles={"SECUENCIAL_NO_AISLADO"})
        errors = []
        self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
        self.assertTrue(any("SECUENCIAL_NO_AISLADO" in error for error in errors), errors)

    def test_formal_specification_is_required(self):
        spec_path = ".agent/docs/specs/2026-09-01-subagent-wait-lifecycle.md"
        self._fixture(omit_files={spec_path})
        errors = []
        self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
        self.assertTrue(any(spec_path in error for error in errors), errors)

    def test_formal_specification_uses_observation_and_result_states(self):
        text = (ROOT / ".agent/docs/specs/2026-09-01-subagent-wait-lifecycle.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`WAIT_TIMEOUT_NONTERMINAL` es no terminal", text)
        self.assertIn("Estados observacionales no terminales", text)
        self.assertIn("Estados terminales de resultado", text)
        for state in ("PASS", "FAIL", "NEEDS_ATTENTION", "UNAVAILABLE"):
            self.assertIn(f"`{state}`", text)
        for state in ("RUNNING", "COMPACTING"):
            self.assertIn(f"`{state}`", text)

    def test_workflow_without_contract_reference_is_rejected(self):
        self._fixture(omit_needles={"SUBAGENT_WAIT_LIFECYCLE"})
        errors = []
        self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
        self.assertTrue(any("SUBAGENT_WAIT_LIFECYCLE" in error for error in errors), errors)

    def test_automatic_cancel_instruction_is_rejected(self):
        cases = [
            (
                ".agent/workflows/preflight-review.md",
                "No se ejecuta `interrupt`\nni `close_agent` automáticamente",
                "Ejecutar `interrupt` y `close_agent` automáticamente",
                "preflight-review.md contiene una instrucción positiva de cancelación automática",
            ),
            (
                ".agent/adapters/codex.md",
                "no debe llamar `interrupt` ni `close_agent` automáticamente",
                "debe llamar `interrupt` y `close_agent` automáticamente",
                "codex.md contiene una instrucción positiva de cancelación automática",
            ),
        ]
        cases.extend(
            (
                f".agent/adapters/{adapter}",
                "no debe llamar `interrupt` ni `close_agent` automáticamente",
                "debe llamar `interrupt` y `close_agent` automáticamente",
                f"{adapter} contiene una instrucción positiva de cancelación automática",
            )
            for adapter in sorted(ADAPTADORES_DE_HARNESS - {"codex.md"})
        )
        for rel_path, old, new, expected in cases:
            with self.subTest(rel_path=rel_path):
                root = self._fixture()
                path = root / rel_path
                text = path.read_text(encoding="utf-8")
                self.assertIn(old, text)
                path.write_text(text + "\n" + new + "\n", encoding="utf-8")
                errors = []
                self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_automatic_cancel_contradiction_on_same_line_is_rejected(self):
        cases = [
            (
                ".agent/workflows/preflight-review.md",
                "No se ejecuta `interrupt` ni `close_agent` automáticamente; "
                "debe llamar `interrupt` y `close_agent` automáticamente",
                "preflight-review.md contiene una instrucción positiva de cancelación automática",
            ),
            (
                ".agent/adapters/codex.md",
                "El adaptador no debe llamar `interrupt` ni `close_agent` automáticamente; "
                "debe llamar `interrupt` y `close_agent` automáticamente",
                "codex.md contiene una instrucción positiva de cancelación automática",
            ),
        ]
        for rel_path, contradiction, expected in cases:
            with self.subTest(rel_path=rel_path):
                root = self._fixture()
                path = root / rel_path
                path.write_text(path.read_text(encoding="utf-8") + contradiction + "\n", encoding="utf-8")
                errors = []
                self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_todo_adaptador_de_harness_declara_el_contrato(self):
        """FW-190: un adaptador de harness nuevo nace obligado a declarar la espera."""
        self.assertIn("claude-code.md", ADAPTADORES_DE_HARNESS)
        for adapter in sorted(ADAPTADORES_DE_HARNESS):
            with self.subTest(adapter=adapter):
                text = (ADAPTERS_DIR / adapter).read_text(encoding="utf-8")
                self.assertIn("## SUBAGENT_WAIT_LIFECYCLE", text)
                self.assertIn("WAIT_TIMEOUT_NONTERMINAL", text)
                for state in ("PASS", "FAIL", "NEEDS_ATTENTION", "UNAVAILABLE"):
                    self.assertIn(state, text)

    def test_todo_adaptador_de_harness_esta_cableado_en_el_doctor(self):
        """La declaración no basta: el doctor tiene que verificarla en cada adaptador."""
        cableados = {
            file_spec["path"].rsplit("/", 1)[-1]
            for file_spec in self._feature()["files"]
            if file_spec["path"].startswith(".agent/adapters/")
        }
        self.assertEqual(ADAPTADORES_DE_HARNESS - cableados, set())

    def test_adapter_sin_seccion_de_espera_es_rechazado(self):
        for adapter in sorted(ADAPTADORES_DE_HARNESS):
            with self.subTest(adapter=adapter):
                path = f".agent/adapters/{adapter}"
                self._fixture(omit_files={path})
                errors = []
                self.doctor.validate_subagent_wait_lifecycle_wiring(errors)
                self.assertTrue(any(path in error for error in errors), errors)

    def test_generated_wrappers_remain_in_sync(self):
        result = subprocess.run(
            ["python3", ".agent/scripts/sync_claude_commands.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_codex_small_task_policy_is_documented_without_host_enforcement_claim(self):
        text = (ROOT / ".agent/adapters/codex.md").read_text(encoding="utf-8")
        self.assertIn("## CODEX_SMALL_TASK_DELEGATION", text)
        self.assertIn("## SUBAGENT_WAIT_LIFECYCLE", text)
        self.assertIn("En Codex no se delega por defecto", text)
        self.assertIn("timeout no terminal", text)
        self.assertIn("20 min sin progreso observable", text)
        self.assertIn("No inferir telemetría privada", text)
        spec = (ROOT / ".agent/docs/specs/progressive_context.md").read_text(encoding="utf-8")
        self.assertIn("no demuestran enforcement", spec)

    def test_codex_policy_is_not_imposed_on_other_adapters(self):
        for adapter in ADAPTADORES_DE_HARNESS - {"codex.md"}:
            text = (ADAPTERS_DIR / adapter).read_text(encoding="utf-8")
            self.assertNotIn("CODEX_SMALL_TASK_DELEGATION", text)


class CodexOptionalDelegationTests(unittest.TestCase):
    """ADR-072: la delegación en Codex es opcional y la espera tiene tope.

    Conserva el núcleo de ADR-057: un timeout nunca es PASS ni FAIL.
    """

    def _codex(self) -> str:
        return (ADAPTERS_DIR / "codex.md").read_text(encoding="utf-8")

    def test_adapter_has_no_positive_instruction_to_parallelize_by_default(self):
        text = re.sub(r"\s+", " ", self._codex()).lower()
        for retirada in (
            "ejecutar varios encargos independientes en paralelo",
            "dividir implementación y revisión en encargos pequeños",
            "no autorizan interrupción",
        ):
            self.assertNotIn(retirada, text)
        self.assertIn("en codex no se delega por defecto", text)
        self.assertIn("4 subagentes simultáneos", text)

    def test_adapter_declares_the_wait_cap_and_needs_attention(self):
        text = re.sub(r"\s+", " ", self._codex())
        self.assertIn("20 min sin progreso observable", text)
        self.assertIn("`NEEDS_ATTENTION`: jamás como PASS ni como FAIL", text)
        self.assertIn("decisión explícita", text)
        self.assertIn("`fork_turns: \"none\"`", text)
        self.assertIn("FW-243", text)

    def test_no_adapter_or_spec_allows_timeout_to_become_pass_or_fail(self):
        """Ninguna línea convierte un timeout en PASS/FAIL salvo para prohibirlo."""
        negaciones = ("no ", "nunca", "jamás", "prohib", "estado prohibido", "- `wait_timeout_nonterminal ->")
        paths = [ADAPTERS_DIR / n for n in ADAPTADORES_DE_HARNESS]
        paths.append(ROOT / ".agent/docs/specs/2026-09-01-subagent-wait-lifecycle.md")
        for path in paths:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                low = line.lower()
                if "timeout" in low and re.search(r"(->|→|=>|\bequivale a\b|\bse trata como\b)\s*`?(pass|fail)\b", low):
                    self.assertTrue(
                        any(n in low for n in negaciones),
                        f"{path.name}:{number}: {line}",
                    )
        spec = (ROOT / ".agent/docs/specs/2026-09-01-subagent-wait-lifecycle.md").read_text(encoding="utf-8")
        self.assertIn("- `WAIT_TIMEOUT_NONTERMINAL -> PASS`", spec)
        self.assertIn("- `WAIT_TIMEOUT_NONTERMINAL -> FAIL`", spec)
        self.assertIn("timeout prolongado (≥ tope, sin progreso observable) + decisión explícita -> interrupt -> NEEDS_ATTENTION", spec)

    def test_an_interrupted_review_never_satisfies_the_tier2_gate(self):
        for name in ("cierre-modulo.md", "preflight-review.md"):
            text = re.sub(r"\s+", " ", (ROOT / ".agent/workflows" / name).read_text(encoding="utf-8"))
            self.assertIn("`NEEDS_ATTENTION`", text, name)
            self.assertRegex(text, r"no satisface (?:la revisión independiente de un módulo tier_2|una revisión Tier 2)", name)

    def test_agents_md_points_codex_to_the_main_conversation(self):
        text = re.sub(r"\s+", " ", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIn("En Codex, trabajar en la conversación principal", text)
        self.assertNotIn("dividir encargos independientes pequeños", text)

    def test_legacy_switch_restores_the_522_text(self):
        legacy = (ROOT / ".agent/docs/specs/codex_delegation_legacy.md").read_text(encoding="utf-8")
        self.assertIn("varios encargos independientes en paralelo", legacy)
        self.assertIn("## CODEX_SMALL_TASK_DELEGATION", legacy)
        self.assertIn("`codex_delegation: legacy`", self._codex())

    def test_profile_reader_only_accepts_the_exact_legacy_value(self):
        import codex_profile

        casos = {
            "- codex_delegation: legacy\n": "legacy",
            "- **codex_delegation**: `legacy`\n": "legacy",
            "codex_delegation: default\n": "default",
            "- codex_delegation: default | legacy\n": "default",
            "- codex_delegation: legacy | default\n": "default",
            "- codex_delegation: legacy  # desactivado\n": "legacy",
            "- codex_delegation: default\n- codex_delegation: legacy\n": "legacy",
            "": "default",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    (root / "_project").mkdir()
                    (root / "_project" / "15_environment_profile.md").write_text(texto, encoding="utf-8")
                    self.assertEqual(codex_profile.codex_delegation_mode(root), esperado)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(codex_profile.codex_delegation_mode(Path(tmp)), "default")

    def test_startup_flags_legacy_only_when_active(self):
        import state_manager

        base = {"data": {"summary": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_project").mkdir()
            profile = root / "_project" / "15_environment_profile.md"
            original = state_manager.state_core.WORKSPACE_ROOT
            state_manager.state_core.WORKSPACE_ROOT = root
            self.addCleanup(setattr, state_manager.state_core, "WORKSPACE_ROOT", original)
            profile.write_text("- codex_delegation: legacy\n", encoding="utf-8")
            flagged = state_manager._flag_legacy_delegation(copy.deepcopy(base), view="startup")
            self.assertEqual(flagged["data"]["summary"]["codex_delegation"], "legacy")
            self.assertEqual(
                state_manager._flag_legacy_delegation(copy.deepcopy(base), view="implementation"), base
            )
            profile.write_text("- codex_delegation: default\n", encoding="utf-8")
            self.assertEqual(state_manager._flag_legacy_delegation(copy.deepcopy(base), view="startup"), base)


class DispatchPointWiringTests(unittest.TestCase):
    """FW-195: la norma debe llegar al agente EN el punto de despacho.

    La política vivía en `AGENTS.md` y en el adaptador, que se leen al arrancar,
    pero ningún workflow se la recordaba al despachar un subagente. En una
    sesión real eso produjo encargos monolíticos: dos de cuatro rondas de
    revisión se perdieron sin entregar veredicto.

    Estos tests miden **proximidad**, no presencia: una revisión independiente
    señaló que `assertIn("FW-195", texto)` pasaría igual con el bloque al final
    del fichero, que es justo lo que la norma dice que no sirve. El primer
    cableado cometió ese error —el recordatorio quedó 337 líneas después del
    despacho real de Tier 2— y esta batería existe para que no vuelva a colar.
    """

    # Cada workflow, con la marca que identifica su punto de despacho real.
    DISPATCHERS = {
        "ciclo.md": "Despachar ejecutores en worktrees",
        "cierre-modulo.md": "DETECTAR SOPORTE DE SUBAGENTES READ-ONLY",
        "preflight-review.md": "ANTES de degradar, VERIFICAR de verdad",
    }
    MAX_DISTANCIA = 20

    def _lines(self, name: str) -> list[str]:
        return (ROOT / ".agent" / "workflows" / name).read_text(
            encoding="utf-8").splitlines()

    def _distancia(self, name: str, marca: str) -> int:
        lines = self._lines(name)
        despacho = next(i for i, l in enumerate(lines) if marca in l)
        cercanos = [
            abs(i - despacho) for i, l in enumerate(lines) if "FW-195" in l
        ]
        self.assertTrue(cercanos, f"{name} no enuncia el tamaño del encargo")
        return min(cercanos)

    def test_the_size_rule_sits_next_to_the_real_dispatch_point(self):
        for name, marca in self.DISPATCHERS.items():
            with self.subTest(workflow=name):
                distancia = self._distancia(name, marca)
                self.assertLessEqual(
                    distancia, self.MAX_DISTANCIA,
                    f"{name}: el recordatorio está a {distancia} líneas del "
                    "despacho; a esa distancia el agente no lo lee al despachar",
                )

    def test_moving_the_rule_away_would_fail(self):
        """El test mide distancia: comprobado con el contenido real desplazado."""
        for name, marca in self.DISPATCHERS.items():
            with self.subTest(workflow=name):
                lines = self._lines(name)
                despacho = next(i for i, l in enumerate(lines) if marca in l)
                lejanas = [
                    i for i, l in enumerate(lines)
                    if "FW-195" in l and abs(i - despacho) > self.MAX_DISTANCIA
                ]
                cercanas = [
                    i for i, l in enumerate(lines)
                    if "FW-195" in l and abs(i - despacho) <= self.MAX_DISTANCIA
                ]
                self.assertTrue(
                    cercanas,
                    f"{name}: solo hay menciones lejanas ({lejanas}), que no cuentan",
                )

    def test_the_rule_states_its_substance_not_just_its_tag(self):
        """Una etiqueta suelta no enseña nada: el texto debe decir qué hacer."""
        for name, marca in self.DISPATCHERS.items():
            with self.subTest(workflow=name):
                lines = self._lines(name)
                despacho = next(i for i, l in enumerate(lines) if marca in l)
                # Se colapsan los blancos: el ajuste de línea del Markdown no
                # debe decidir si una frase "está" o no.
                ventana = re.sub(r"[\s>]+", " ", " ".join(lines[
                    max(0, despacho - self.MAX_DISTANCIA):
                    despacho + self.MAX_DISTANCIA + 15
                ])).lower()
                for exigido in ("encargos pequeños", "no se delega por defecto", "auditoría integral"):
                    self.assertIn(exigido, ventana, f"{name}: falta «{exigido}»")

    def test_every_dispatch_point_states_the_wait_contract(self):
        for name in self.DISPATCHERS:
            with self.subTest(workflow=name):
                self.assertIn(
                    "SUBAGENT_WAIT_LIFECYCLE",
                    "\n".join(self._lines(name)), name,
                )

    def test_dispatch_points_delegate_the_budget_to_the_active_adapter(self):
        """El presupuesto es del adaptador: el workflow no fija la cifra."""
        for name in self.DISPATCHERS:
            with self.subTest(workflow=name):
                text = "\n".join(self._lines(name))
                self.assertIn("adaptador activo", text, name)
                self.assertIn("CODEX_SMALL_TASK_DELEGATION", text, name)

    def test_the_codex_budget_is_still_confined_to_its_adapter(self):
        """Referenciar la sección no es declararla."""
        for path in sorted(ADAPTERS_DIR.glob("*.md")):
            if path.name in {"codex.md", *NO_SON_ADAPTADORES_DE_HARNESS}:
                continue
            self.assertNotIn(
                "## CODEX_SMALL_TASK_DELEGATION",
                path.read_text(encoding="utf-8"), path.name,
            )


if __name__ == "__main__":
    unittest.main()
