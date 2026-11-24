#!/usr/bin/env python3
"""L5 de la 5.3.1: ceremonia y coherencia de la norma.

Una clase por sublote. Los casos R1 a R18 son los de la fase 1 del lote (plan del
scratchpad, sección 4), reducidos a su forma.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import projections  # noqa: E402
import turn_close as tc  # noqa: E402
import turn_close_hook as hook  # noqa: E402

MANIFEST = json.loads((ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8"))
MODEL = "Modelo: standard · Esfuerzo: 🟡 medio (tier_1)"


def close(line: str, model: str | None = MODEL, before: str = "Resumen.") -> str:
    return "\n".join([before, "", line] + ([model] if model else []))


def errors(text: str) -> list[str]:
    return [d.code for d in tc.validate_turn_close(text, manifest=MANIFEST) if d.severity == tc.SEVERITY_ERROR]


def warnings(text: str) -> list[str]:
    return [d.code for d in tc.validate_turn_close(text, manifest=MANIFEST) if d.severity != tc.SEVERITY_ERROR]


def codes(text: str) -> list[str]:
    return [d.code for d in tc.validate_turn_close(text, manifest=MANIFEST)]


def words(n: int) -> str:
    return " ".join(["paso"] * n)


# Casos reales de PA con Codex (FW-360) y de TL con Claude, reducidos.
R1 = ("Siguiente: /gycd Guardar la guía y las precisiones de esta revisión, manteniendo ambas pausas y "
      "sin commit, push ni ejecución financiera.")
R2 = "Siguiente: /diseñar Preparar la readmisión de C8a sin aplicar cambios y sin intervenir el 8210"
R3 = ("Siguiente: /ciclo Importar los CSV de la sonda de Axi desde el 14 de octubre, una vez cerrado cada día del "
      "servidor (16:00 hora local), con scripts/axi_spread_probe_import.py --server-timezone America/New_York+420; "
      "informar días acumulados hacia diez por ventana, por separado para cada una de las tres ventanas y con su "
      "recuento de días válidos y descartados.")
R4 = "Siguiente: /ciclo Implementar el login en src/auth.py con sus tests"


class ProhibicionesTests(unittest.TestCase):
    """L5.1 (N1, FW-360): `Siguiente:` propone una acción y no lleva prohibiciones.

    Desde la 5.3.2 (FW-402) las dos reglas son avisos: se registran y no invalidan el cierre.
    """

    def test_sin_x_ni_y_es_prohibicion(self) -> None:
        self.assertIn(tc.PROHIBICIONES, warnings(close(R1)))
        self.assertEqual(errors(close(R1)), [])

    def test_dos_clausulas_sin_es_prohibicion(self) -> None:
        self.assertIn(tc.PROHIBICIONES, warnings(close(R2)))
        self.assertEqual(errors(close(R2)), [])

    def test_instruccion_larga_avisa(self) -> None:
        self.assertIn(tc.INSTRUCCION_LARGA, warnings(close(R3)))
        self.assertEqual(errors(close(R3)), [])

    def test_el_tope_es_de_cuarenta_palabras(self) -> None:
        self.assertEqual(tc.MAX_INSTRUCTION_WORDS, 40)
        self.assertEqual(codes(close("Siguiente: /ciclo " + words(40))), [])
        self.assertEqual(codes(close("Siguiente: /ciclo " + words(41))), [tc.INSTRUCCION_LARGA])
        self.assertEqual(errors(close("Siguiente: /ciclo " + words(41))), [])

    def test_el_tope_vale_para_decision_y_ninguno(self) -> None:
        for head in ("Siguiente: decisión — ", "Siguiente: ninguno — "):
            with self.subTest(head=head):
                self.assertEqual(codes(close(head + words(40), model=None)), [])
                self.assertEqual(codes(close(head + words(41), model=None)), [tc.INSTRUCCION_LARGA])
                self.assertEqual(errors(close(head + words(41), model=None)), [])

    def test_el_codigo_en_linea_cuenta_como_una_palabra(self) -> None:
        line = "Siguiente: /ciclo " + words(39) + " `python3 -m unittest tests.test_auth tests.test_login -v`"
        self.assertEqual(codes(close(line)), [])

    def test_cierre_normal_valido_y_sin_avisos(self) -> None:
        self.assertEqual(tc.validate_turn_close(close(R4), manifest=MANIFEST), [])

    def test_una_clausula_sin_suelta_vale(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo Corregir BUG-094 en una copia de C8a sin integrar todavía")), [])

    def test_en_una_decision_no_se_cuentan_prohibiciones(self) -> None:
        line = "Siguiente: decisión — responde A (recomendada: cerrar sin el punto 4 ni el 5) o B en este chat"
        self.assertEqual(codes(close(line, model=None)), [])

    def test_codigo_y_rutas_no_cuentan(self) -> None:
        line = 'Siguiente: /ciclo Ejecutar `pytest -k "sin_red ni_datos"` sobre tests/test_sin.py y sin/ni.md'
        self.assertEqual(codes(close(line)), [])

    def test_ni_antes_del_sin_no_cuenta(self) -> None:
        line = "Siguiente: /ciclo Revisar que ni el login ni el logout fallen, sin tocar la base"
        self.assertEqual(codes(close(line)), [])

    def test_el_arreglo_nombra_la_salida(self) -> None:
        (defect,) = [d for d in tc.validate_turn_close(close(R1), manifest=MANIFEST) if d.code == tc.PROHIBICIONES]
        self.assertIn("Siguiente: decisión —", defect.fix)
        (long,) = [d for d in tc.validate_turn_close(close(R3), manifest=MANIFEST) if d.code == tc.INSTRUCCION_LARGA]
        self.assertIn("40 palabras", long.fix)

    def test_la_proyeccion_avisa_de_un_puntero_largo(self) -> None:
        warning = projections._session_pointer_warning("Siguiente: /ciclo " + words(120), root=ROOT)
        self.assertIn(tc.INSTRUCCION_LARGA, warning)
        self.assertIn(tc.PROHIBICIONES, projections._session_pointer_warning(R1, root=ROOT))  # 5.3.2: sigue avisando
        self.assertEqual(projections._session_pointer_warning(R4, root=ROOT), "")

    def test_ninguna_plantilla_de_los_workflows_da_los_codigos_nuevos(self) -> None:
        for path in sorted((ROOT / ".agent" / "workflows").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for finding in tc.scan_templates(text, manifest=MANIFEST):
                with self.subTest(path=path.name, line=finding.line):
                    self.assertFalse({tc.PROHIBICIONES, tc.INSTRUCCION_LARGA} & {d.code for d in finding.defects})

    def test_gycd_no_toma_la_restriccion_pegada_por_peticion_del_builder(self) -> None:
        text = " ".join((ROOT / ".agent" / "workflows" / "gycd.md").read_text(encoding="utf-8").split())
        self.assertIn("no cuenta como petición y el commit de sesión se hace (FW-360)", text)
        self.assertIn("`next_action` es una acción de hasta 40 palabras y sin prohibiciones (FW-360)", text)

    def test_la_norma_dice_el_tope(self) -> None:
        core = " ".join((ROOT / ".agent" / "rules" / "runtime_core.md").read_text(encoding="utf-8").split())
        self.assertIn("una acción de hasta 40 palabras, sin prohibiciones", core)
        skill = " ".join((ROOT / ".agent" / "skills" / "decision_translation_core.md").read_text(encoding="utf-8").split())
        self.assertIn("`PROHIBICIONES`", skill)
        self.assertIn("`INSTRUCCION_LARGA`", skill)


class ProhibicionesHookTests(unittest.TestCase):
    """El hook `Stop` registra `PROHIBICIONES` sin bloquear (5.3.2, FW-402; en la 5.3.1 bloqueaba una vez)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l5_hook_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        home = self.tmp / "home"
        home.mkdir()
        self.root = self.tmp / "proyecto"
        (self.root / "_project").mkdir(parents=True)
        (self.root / ".agent" / "workflows").mkdir(parents=True)
        shutil.copy2(ROOT / ".agent" / "workflows" / "manifest.json", self.root / ".agent" / "workflows" / "manifest.json")
        (self.root / "_project" / "03_project_state.json").write_text(
            json.dumps({"framework_mode": "initialized", "module_statuses": {}}), encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, message: str) -> dict:
        event = {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False,
                 "last_assistant_message": message}
        return hook.evaluate(event, host="codex", root=self.root, gycd=False)

    def test_registra_el_aviso_sin_bloquear(self) -> None:
        self.assertEqual(self._run(close(R1)), {})
        self.assertEqual(self._run(close(R3, before="Resumen, otra vez.")), {})
        rows = [json.loads(line) for line in hook.telemetry_path(self.root).read_text(encoding="utf-8").splitlines()]
        checks = [(r["result"], r["valid"], r["warnings"]) for r in rows if r["event"] == "turn_close_check"]
        self.assertEqual(checks, [("pass", True, [tc.PROHIBICIONES]), ("pass", True, [tc.INSTRUCCION_LARGA])])


class PalabrasPegadasTests(unittest.TestCase):
    """L5.2 (N3, FW-362): aviso, nunca rechazo, de palabras pegadas a cifras o siglas fuera del código."""

    def _pegadas(self, text: str) -> list[tc.Defect]:
        return [d for d in tc.validate_turn_close(text, manifest=MANIFEST) if d.code == tc.PALABRAS_PEGADAS]

    def test_es_aviso(self) -> None:
        self.assertIn(tc.PALABRAS_PEGADAS, tc.WARNING_CODES)
        self.assertNotIn(tc.PALABRAS_PEGADAS, tc.ERROR_CODES)

    def test_los_casos_reales_avisan_sin_rechazar(self) -> None:
        text = close(R4, before="Corregí antesCODE el fallo sin8210 por8bugs y en1proyecto; approvalrc0.")
        (defect,) = self._pegadas(text)
        self.assertEqual(defect.line, 1)
        self.assertIn("antesCODE", defect.fix)
        self.assertEqual(errors(text), [])
        for word in ("antesCODE", "sin8210", "por8bugs", "en1proyecto", "approvalrc0", "intervenir8210", "prioridad25"):
            with self.subTest(word=word):
                self.assertTrue(self._pegadas(close(R4, before=f"Hecho {word} hoy.")))

    def test_sin_falsos_positivos_en_codigo_rutas_y_siglas(self) -> None:
        before = ("Usé `python3 x.py` y `sin8210` en código, la ruta src/auth2fa.py, auth2fa.py, la URL "
                  "https://ejemplo.com/v2api, el commit 68ecaee, macOS, utf8, sha256, base64, "
                  "python3, 10min, 300ms, x86, arm64, i18n, oauth2, BUG-071, tier_1 y L5.2.\n\n"
                  "```\nantesCODE por8bugs\n```\n\n~~~\nsin8210\n~~~")
        self.assertEqual(self._pegadas(close(R4, before=before)), [])

    def test_solo_en_modo_live(self) -> None:
        line = "Siguiente: /ciclo Revisar el fallo sin8210 en src/x.py"
        report = tc.validate_turn_close(line, manifest=MANIFEST, mode="template")
        self.assertNotIn(tc.PALABRAS_PEGADAS, [d.code for d in report])

    def test_texto_enorme_sigue_siendo_lineal(self) -> None:
        import time

        huge = "x\n\nSiguiente: /" + "a" * 200_000 + " [" + "b" * 200_000 + "]"
        start = time.monotonic()
        tc.validate_turn_close(huge, manifest=MANIFEST)
        glued = " ".join(["sin8210"] * 50_000) + "\n\n" + R4 + "\n" + MODEL
        tc.validate_turn_close(glued, manifest=MANIFEST)
        self.assertLess(time.monotonic() - start, 2.0)

    def test_una_palabra_enorme_no_vuelve_cuadratico_el_escaneo(self) -> None:
        # Hallazgo 1 de la revisión: la regex retrocedía dentro de una palabra larga sin encajar.
        import time

        start = time.monotonic()
        for word in ("ab" + "1" * 30_000 + "-", "aa" + "A" * 30_000 + "-"):
            tc.validate_turn_close(close(R4, before="Resumen " + word), manifest=MANIFEST)
        self.assertLess(time.monotonic() - start, 0.5)

    def test_el_adaptador_de_codex_lo_dice(self) -> None:
        text = (ROOT / ".agent" / "adapters" / "codex.md").read_text(encoding="utf-8")
        self.assertIn("Prosa llana (FW-362)", text)
        self.assertIn("`PALABRAS_PEGADAS`", text)


FREEZE = ("Durante la congelación, una fricción del framework se registra y se sigue con la salida declarada del "
          "gate; desde un proyecto no se diseña ni se aplica un arreglo del framework.")


class CongelacionTests(unittest.TestCase):
    """L5.3: una línea en el núcleo sobre la congelación (texto exacto de §3 L5 del plan)."""

    def test_el_nucleo_lleva_la_frase_exacta_y_cabe_en_6_kib(self) -> None:
        raw = (ROOT / ".agent" / "rules" / "runtime_core.md").read_bytes()
        self.assertLessEqual(len(raw), 6144)
        self.assertTrue(FREEZE in " ".join(raw.decode("utf-8").split()), "falta la frase de la congelación")


# Órdenes del arnés (Claude Code, Codex) y el marcador `/comando`: no son del manifest.
HARNESS_COMMANDS = {"comando", "hooks", "model", "compact", "clear", "effort", "mcp", "permissions", "memory"}


class CoherenciaTests(unittest.TestCase):
    """L5.4: coherencia sin reescribir la norma (FW-313, FW-321, FW-322, FW-323, FW-324, FW-327)."""

    def test_vocabulario_retirado_cero_en_las_superficies_activas(self) -> None:
        import turn_close_audit

        report = turn_close_audit.audit_vocabulary(ROOT)
        hits = {t: v["ficheros"] for t, v in report["terminos"].items() if v["total"]}
        self.assertEqual(hits, {})
        self.assertEqual(turn_close_audit.RETIRED_TERMS, tuple(tc.RETIRED_VOCABULARY))

    def test_el_registro_dice_que_usar_y_no_retira_rojo_verde(self) -> None:
        for term, use in tc.RETIRED_VOCABULARY.items():
            with self.subTest(term=term):
                self.assertTrue(use.strip())
        self.assertNotIn("rojo-verde-refactor", tc.RETIRED_VOCABULARY)  # D4: FW-327 completo es de enero

    def test_el_nucleo_rutea_tdd_por_tier(self) -> None:
        core = (ROOT / ".agent" / "rules" / "runtime_core.md").read_text(encoding="utf-8")
        self.assertNotIn("Medium/High", core)
        self.assertIn("- Implementación tier_1/tier_2 con lógica que probar → `/tdd`", core)

    def test_las_referencias_del_manifest_existen(self) -> None:
        names = {c["name"] for c in MANIFEST["commands"]}
        for command in MANIFEST["commands"]:
            with self.subTest(command=command["name"]):
                self.assertTrue((ROOT / command["source"]).is_file(), command["source"])
                context = command.get("context") or {}
                for rel in context.get("required_now", []):
                    self.assertTrue((ROOT / rel).exists(), rel)
                for item in context.get("activate_when", []):
                    self.assertIn(item["command"], names)
                for name in re.findall(r"(?<![\w/.-])/([a-zñ][a-zñ0-9-]*)(?![\w/.-])", command["description"]):
                    self.assertIn(name, names, command["description"])

    def test_las_referencias_de_los_workflows_existen(self) -> None:
        names = {c["name"] for c in MANIFEST["commands"]}
        allowed_paths = {".agent/extensions/state_actions.py", ".agent/areas"}  # la crea el proyecto; foco.md la prohíbe
        surfaces = sorted((ROOT / ".agent" / "workflows").glob("*.md")) + [ROOT / ".agent" / "docs" / "extended_protocol.md"]
        missing = []
        for path in surfaces:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for name in re.findall(r"`/([a-zñ][a-zñ0-9-]*)(?:[ `])", line):
                    if name not in names and name not in HARNESS_COMMANDS:
                        missing.append(f"{path.name}:{number} /{name}")
                for ref in re.findall(r"`(\.agent/[^`\s*<>\[\]{}]+)`", line):
                    ref = ref.split("#", 1)[0].split(":", 1)[0].rstrip("/.,;)")
                    if ref not in allowed_paths and not (ROOT / ref).exists():
                        missing.append(f"{path.name}:{number} {ref}")
        self.assertEqual(missing, [])

    def test_no_quedan_referencias_muertas_de_fw_322(self) -> None:
        ext = (ROOT / ".agent" / "docs" / "extended_protocol.md").read_text(encoding="utf-8")
        foco = (ROOT / ".agent" / "workflows" / "foco.md").read_text(encoding="utf-8")
        for dead in ("declare-interface", "new_module_guard", "caveman.md"):
            self.assertNotIn(dead, ext)
        self.assertNotIn("/revisar", foco)

    def _doctor_on(self, agents: str) -> list[str]:
        import framework_doctor
        import root_docs

        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "AGENTS.md").write_text(agents.format(block=root_docs.render_block("contenido gestionado\n")),
                                             encoding="utf-8")
            original, framework_doctor.ROOT = framework_doctor.ROOT, proj
            try:
                errors: list[str] = []
                warns: list[str] = []
                framework_doctor.validate_root_docs_contract("initialized", errors, warns)
            finally:
                framework_doctor.ROOT = original
        return warns

    def test_el_doctor_avisa_del_vocabulario_retirado_fuera_del_bloque(self) -> None:
        warns = self._doctor_on("# Proyecto\n\nSiguiente comando: /ciclo\n\n{block}\n")
        hit = [w for w in warns if "vocabulario retirado" in w]
        self.assertEqual(len(hit), 1, warns)
        self.assertIn("«Siguiente comando:»", hit[0])
        self.assertIn("FW-313", hit[0])

    def test_sin_bloque_no_aconseja_tocar_las_anclas_de_la_migracion(self) -> None:
        # Hallazgo 3: una raíz heredada ya avisa de migrar, y migrate_root_docs.py necesita «## Framework v4».
        warns = self._doctor_on("# Proyecto\n\n## Framework v4\nSiguiente comando: /ciclo\n")
        self.assertEqual([w for w in warns if "vocabulario retirado" in w], [])
        self.assertTrue(any("no tiene el bloque GESTIONADO" in w for w in warns), warns)

    def test_dentro_del_bloque_no_avisa(self) -> None:
        import root_docs

        block = root_docs.render_block("Siguiente comando: /ciclo\n")
        warns = self._doctor_on("# Proyecto\n\n" + block.replace("{", "{{").replace("}", "}}") + "\n")
        self.assertEqual([w for w in warns if "vocabulario retirado" in w], [])


class SueloUpgradeTests(unittest.TestCase):
    """L5.5 (S8): /actualizar-framework solo sube desde 5.3.x y se retiran las migraciones de 5.2.x."""

    def setUp(self) -> None:
        import propagate_framework

        self.prop = propagate_framework
        self.tmp = Path(tempfile.mkdtemp(prefix="l5_suelo_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name, value in (("git_state", "limpio"), ("current_branch", None), ("default_branch", None)):
            patcher = mock.patch.object(propagate_framework, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(propagate_framework, "local_patches", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _project(self, version: str) -> Path:
        proj = self.tmp / f"p{version}"
        (proj / "_project").mkdir(parents=True)
        (proj / ".agent" / "workflows").mkdir(parents=True)
        for rel in ("_project/03_project_state.json", ".agent/workflows/manifest.json"):
            (proj / rel).write_text(json.dumps({"framework_version": version}), encoding="utf-8")
        return proj

    def _floor(self, version: str, target: str = "5.3.1") -> list[str]:
        report = self.prop.inspect(self._project(version), target)
        return [b for b in report["blockers"] if "v5.3.0" in b]

    def test_desde_5_2_se_rechaza_con_la_ruta(self) -> None:
        (blocker,) = self._floor("5.2.2")
        self.assertIn("worktree add", blocker)
        self.assertIn("/actualizar-framework", blocker)
        self.assertIn("No ejecutes propagate_framework.py --apply desde esa carpeta", blocker)  # hallazgo 4
        self.assertIn("5.3.1", blocker)

    def test_desde_5_3_no_hay_suelo(self) -> None:
        self.assertEqual(self._floor("5.3.0"), [])

    def test_un_objetivo_anterior_al_suelo_no_lo_aplica(self) -> None:
        self.assertEqual(self._floor("4.24.0", target="4.25.0"), [])
        self.assertFalse(self.prop.below_floor("x.y", "5.3.1"))

    def test_main_no_actualiza_un_proyecto_por_debajo_del_suelo(self) -> None:
        import contextlib
        import io

        proj = self._project("5.2.2")
        out = io.StringIO()
        with mock.patch.object(self.prop, "discover", return_value=[proj]), \
                mock.patch.object(self.prop, "template_version", return_value="5.3.1"), \
                mock.patch.object(self.prop, "missing_release_marker", return_value=False), \
                mock.patch.object(self.prop, "managed_root_changes", return_value={}), \
                mock.patch.object(self.prop, "apply_upgrade") as apply_upgrade, \
                mock.patch.object(self.prop.sys, "argv", ["propagate_framework.py", "--apply", "--yes"]), \
                contextlib.redirect_stdout(out):
            self.prop.main()
        apply_upgrade.assert_not_called()
        self.assertIn("v5.3.0", out.getvalue())
        self.assertIn("worktree add", out.getvalue())

    def test_las_migraciones_de_5_2_se_retiran_y_el_workflow_da_la_ruta(self) -> None:
        scripts = ROOT / ".agent" / "scripts"
        for gone in ("migrate_codex_budget_retirement.py", "migrate_approval_snapshots.py"):
            self.assertFalse((scripts / gone).exists(), gone)
        for kept in ("codex_budget_legacy.py", "migrate_root_docs.py", "migrate_test_identities.py"):
            self.assertTrue((scripts / kept).exists(), kept)
        workflow = (ROOT / ".agent" / "workflows" / "actualizar-framework.md").read_text(encoding="utf-8")
        self.assertNotIn("migrate_codex_budget_retirement", workflow)
        self.assertNotIn("3.bis", workflow)
        self.assertIn("v5.3.0", workflow)
        reconciliar = (ROOT / ".agent" / "workflows" / "reconciliar.md").read_text(encoding="utf-8")
        self.assertNotIn("migrate_codex_budget_retirement", reconciliar)
        self.assertIn("v5.3.0", reconciliar)
        propagate = (scripts / "propagate_framework.py").read_text(encoding="utf-8")
        self.assertNotIn("retirement", propagate)

    def test_el_estado_antiguo_nombra_la_ruta(self) -> None:
        text = (ROOT / ".agent" / "scripts" / "state_core.py").read_text(encoding="utf-8")
        start = text.index("LEGACY_MIGRATION_REQUIRED: session.codex_execution")
        self.assertIn("etiqueta v5.3.0, que trae el migrador", text[start:start + 300])


if __name__ == "__main__":
    unittest.main()
