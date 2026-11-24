#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cosecha multi-proyecto 2026-09-14 (FW-226).

Cada test fija un fallo reproducido en un proyecto adoptante real y verificado
contra el código de la plantilla antes de corregirlo:

- STUB GATE: `placeholder` como atributo HTML es UI, no dato fabricado
  (Example Tool FW-223), y tocar un fichero no hace al módulo responsable de lo que ya
  había en él (búfer TL `stub-gate-marca-codigo-preexistente-por-tocar-el-fichero`).
- Contexto progresivo: el formato de scope que prescribe `/enriquecer-ticket`
  no es ambiguo (Example Tool FW-224).
- `add_work_tier` rechaza claves desconocidas (Example Tool FW-T09, TL FW-113).
- Guard destructivo: ítems de `for … in` son dato; `rm -rf` sobre una variable
  asignada a un temporal del sistema es efímero (Example Tool FW-222, búfer plantilla).
- Escáner de secretos: huellas en la proyección de sesión (TL FW-204).
- CI de producto: un `scripts/ci_smoke.sh` propio no es el CI de la plantilla
  (TL FW-206).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import context_sources  # noqa: E402
import guard_destructive_command as guard  # noqa: E402
import state_core  # noqa: E402
import validate_no_stubs as stubs  # noqa: E402


def _git(root: Path, *args: str) -> None:
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(["git", "-C", str(root), *args], check=True, env=env, capture_output=True)


class StubGatePlaceholderTests(unittest.TestCase):
    def _scan(self, name: str, body: str) -> list[str]:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / name
            path.write_text(body, encoding="utf-8")
            return stubs.scan_file(path, Path(d))

    def test_atributo_html_placeholder_no_es_dato_fabricado(self):
        self.assertEqual([], self._scan("form.js", 'html = `<input class="campo" placeholder="Buscar variedad">`;\n'))

    def test_usos_de_placeholder_como_nombre_no_disparan(self):
        body = (
            "el.setAttribute('placeholder', 'Buscar');\n"
            "el.placeholder = 'Nombre';\n"
            "const cls = 'placeholder-gray-400 placeholder:text-sm';\n"
            "const css = 'input::placeholder';\n"
        )
        self.assertEqual([], self._scan("ui.js", body))

    def test_valor_placeholder_sigue_marcandose(self):
        self.assertTrue(self._scan("svc.js", "const token = 'placeholder';\n"))

    def test_fichero_tocado_solo_se_escanea_en_lineas_cambiadas(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "app.js"
            src.write_text("const contacto = 'ana@example.com';\n", encoding="utf-8")
            _git(root, "init", "-q")
            _git(root, "add", "-A")
            _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
            src.write_text(
                "const contacto = 'ana@example.com';\nconst total = sumar(a, b);\n", encoding="utf-8"
            )
            lines = stubs.added_lines(src, root)
            self.assertEqual({2}, lines)
            self.assertEqual([], stubs.scan_file(src, root, only_lines=lines))
            src.write_text(
                "const contacto = 'ana@example.com';\nconst otro = 'luis@example.com';\n", encoding="utf-8"
            )
            self.assertTrue(stubs.scan_file(src, root, only_lines=stubs.added_lines(src, root)))

    def test_fichero_no_rastreado_se_escanea_entero(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _git(root, "init", "-q")
            src = root / "nuevo.js"
            src.write_text("const x = 'a@example.com';\n", encoding="utf-8")
            self.assertIsNone(stubs.added_lines(src, root))


class ScopeSectionNestingTests(unittest.TestCase):
    def test_h1_de_alcance_con_h2_de_modulo_es_una_sola_seccion(self):
        content = (
            "# ALCANCE ACTIVO — config_paso2: botón y banner\n\n"
            "## Módulo: config_paso2 — ticket enriquecido\n\n"
            "- tarea 1\n"
        )
        block, selector, actual = context_sources._module_section(content, "config_paso2", "scope")
        self.assertIn("tarea 1", block)
        self.assertEqual("config_paso2", actual)

    def test_dos_secciones_hermanas_siguen_siendo_ambiguas(self):
        content = "## M1 — primera\nuno\n\n## M1 — segunda\ndos\n"
        with self.assertRaises(context_sources.QueryError) as error:
            context_sources._module_section(content, "M1", "acceptance")
        self.assertEqual("SOURCE_AMBIGUOUS", error.exception.code)


class AddWorkTierStrictKeysTests(unittest.TestCase):
    def test_clave_desconocida_se_rechaza_nombrando_las_validas(self):
        with self.assertRaises(ValueError) as error:
            state_core._reject_unknown_payload_keys(
                "add_work_tier", {"id": "m1", "tier": "tier_1", "riesgo": "alto"}
            )
        self.assertIn("riesgo", str(error.exception))
        self.assertIn("rationale", str(error.exception))

    def test_claves_documentadas_pasan(self):
        state_core._reject_unknown_payload_keys("add_work_tier", {
            "id": "m1", "tier": "tier_2", "rationale": "auth", "classified_by": "cambio-seguro",
            "requires_second_review": True, "handles_real_data": True,
        })


class GuardFalsePositiveTests(unittest.TestCase):
    def test_items_de_for_son_dato(self):
        blocked, _ = guard.is_destructive("for c in 'grep -n \"db push\" AGENTS.md'; do echo $c; done")
        self.assertFalse(blocked)

    def test_for_que_ejecuta_la_variable_sigue_bloqueando(self):
        blocked, _ = guard.is_destructive("for c in 'npm run db:push'; do $c; done")
        self.assertTrue(blocked)
        blocked, _ = guard.is_destructive("for c in 'DROP TABLE users'; do eval \"$c\"; done")
        self.assertTrue(blocked)

    def test_rm_sobre_variable_asignada_a_temporal_es_efimero(self):
        cmd = "SCRATCH=/private/tmp/claude-501/scratchpad/preview; rm -rf \"$SCRATCH\""
        self.assertEqual((False, ""), guard.is_destructive(cmd))

    def test_rm_sobre_variable_asignada_fuera_de_temporales_bloquea(self):
        blocked, _ = guard.is_destructive("DIR=/Users/pepe/proyecto; rm -rf \"$DIR\"")
        self.assertTrue(blocked)

    def test_rm_sobre_variable_desconocida_bloquea(self):
        blocked, _ = guard.is_destructive("rm -rf \"$SCRATCH\"")
        self.assertTrue(blocked)

    def test_escape_por_dotdot_desde_temporal_bloquea(self):
        blocked, _ = guard.is_destructive("rm -rf /private/tmp/../../Users")
        self.assertTrue(blocked)


class SecretScanFingerprintTests(unittest.TestCase):
    def test_huella_hex_en_session_brief_no_es_secreto(self):
        import hashlib

        import framework_doctor as doctor

        # La huella se genera en ejecución: un literal hex de 64 caracteres
        # dispara —con razón— el escáner de secretos sobre este mismo fichero.
        huella = hashlib.sha256(b"fixture de huella de plan").hexdigest()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "_project").mkdir()
            (root / "_project" / "00_session_brief.json").write_text(
                json.dumps({"plan_fingerprint": huella}), encoding="utf-8"
            )
            original = doctor.ROOT
            doctor.ROOT = root
            try:
                errors: list[str] = []
                doctor.scan_potential_secrets(errors)
            finally:
                doctor.ROOT = original
            self.assertEqual([], [e for e in errors if "00_session_brief" in e])


class ProductCiShapeTests(unittest.TestCase):
    def _errors(self, run_line: str) -> list[str]:
        import framework_doctor as doctor

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "ci.yml").write_text(f"jobs:\n  t:\n    steps:\n      - run: {run_line}\n", encoding="utf-8")
            original = doctor.ROOT
            doctor.ROOT = root
            try:
                errors: list[str] = []
                doctor.validate_product_ci_shape("initialized", errors)
            finally:
                doctor.ROOT = original
            return errors

    def test_ci_smoke_de_producto_es_legitimo(self):
        self.assertEqual([], self._errors("bash scripts/ci_smoke.sh"))

    def test_ci_smoke_de_plantilla_sigue_marcandose(self):
        self.assertTrue(self._errors("bash .agent/scripts/ci_smoke.sh"))


class ContentHashV2Tests(unittest.TestCase):
    """Recibos que caducaban por los propios pasos del cierre (búfer TL, 21
    instancias; PA FW-126/131; Example Service FW-132)."""

    def setUp(self):
        import workflow_receipts as wr

        self.wr = wr
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "_project").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("def total():\n    return 1\n", encoding="utf-8")
        self.acc = self.root / "_project" / "08_module_acceptance.md"
        self.acc.write_text("## M1\n\n- [ ] AC1 el total es correcto — evidencia: pendiente\n", encoding="utf-8")
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def _hash(self):
        return self.wr.workspace_diff_hash(self.root)

    def test_commitear_el_diff_revisado_no_caduca_el_hash(self):
        (self.root / "src" / "app.py").write_text("def total():\n    return 2\n", encoding="utf-8")
        before = self._hash()
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "fix")
        self.assertEqual(before, self._hash())

    def test_marcar_ac_y_anotar_evidencia_no_caduca(self):
        before = self._hash()
        self.acc.write_text(
            "## M1\n\nEstado: ✅ Verificado con cliente\n\n"
            "- [x] AC1 el total es correcto — evidencia: `src/app.py` → rc=0\n",
            encoding="utf-8",
        )
        self.assertEqual(before, self._hash())

    def test_cambiar_el_texto_del_ac_si_caduca(self):
        before = self._hash()
        self.acc.write_text("## M1\n\n- [ ] AC1 el total redondea a dos decimales\n", encoding="utf-8")
        self.assertNotEqual(before, self._hash())

    def test_bitacoras_narrativas_no_cuentan(self):
        before = self._hash()
        (self.root / "_project" / "05_test_history.md").write_text("- suite verde\n", encoding="utf-8")
        (self.root / "_feedback").mkdir()
        (self.root / "_feedback" / "field_report.md").write_text("# FW\n", encoding="utf-8")
        self.assertEqual(before, self._hash())

    def test_cambio_de_codigo_caduca(self):
        before = self._hash()
        (self.root / "src" / "app.py").write_text("def total():\n    return 3\n", encoding="utf-8")
        self.assertNotEqual(before, self._hash())

    def test_recibo_legado_se_compara_contra_el_hash_legado(self):
        (self.root / "src" / "app.py").write_text("def total():\n    return 5\n", encoding="utf-8")
        legacy = self.wr._compute_workspace_diff_hash(self.root)
        current = self._hash()
        self.assertTrue(current.startswith("v3:"))
        self.assertTrue(current.v2.startswith("v2:"))
        self.assertTrue(self.wr.diff_hash_matches(legacy, current))
        self.assertTrue(self.wr.diff_hash_matches(str(current), current))
        (self.root / "src" / "app.py").write_text("def total():\n    return 6\n", encoding="utf-8")
        self.assertFalse(self.wr.diff_hash_matches(legacy, self._hash()))


if __name__ == "__main__":
    unittest.main()
