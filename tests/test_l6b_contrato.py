"""Contrato de L6b (FW-337): secciones ajenas, pruebas cubiertas y dos huecos menores.

Punto 4 (TL:FW-310). La lista de cabeceras de otros módulos se decide al sellar
y viaja dentro del recibo, en `foreign_sections`. Al validar solo cuentan el
recibo y los ficheros: lo que hay bajo una cabecera sellada como ajena no entra
en la huella de scope ni de aceptación; todo lo demás cuenta para todos.

Punto 6 (TL:FW-308). Un cambio de prueba queda cubierto para un módulo si hay
una cadena de cambios declarados y sellados por otros módulos ya cerrados que
va de la identidad de su mapa de aprobación a la identidad actual.

Menores. Un recibo manipulado caduca aunque a la vez haya un cambio aditivo, y
el refresco valida la ADR que cita `documents_decision`.

Estos tests los escribe el revisor. El ejecutor no los modifica.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import test_workflow_receipts as twr
import validate_acceptance as va
import workflow_receipts as wr

T0 = "2026-07-15T12:00:00+00:00"


def ts(minutes: int) -> str:
    return (datetime.fromisoformat(T0) + timedelta(minutes=minutes)).isoformat()


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True,
    )


SCOPE = (
    "# Scope\n"
    "Regla común: no tocar la tabla users\n"
    "\n"
    "## M1\n"
    "Scope de M1\n"
    "\n"
    "## M2\n"
    "Scope de M2 original\n"
    "\n"
    "## Notas\n"
    "Nota común original\n"
)

ACCEPTANCE = (
    "# Aceptación\n"
    "\n"
    "## Módulo 1 — Uno (`M1`)\n"
    "- [ ] AC de M1\n"
    "\n"
    "## Módulo 2 — Dos (`M2`)\n"
    "- [ ] AC de M2 original\n"
    "\n"
    "## === FASE 1 ===\n"
    "Cierre de fase original\n"
)

H_SCOPE_M2 = "## M2"
H_ACC_M2 = "## Módulo 2 — Dos (`M2`)"


class L6bBase(unittest.TestCase):
    def setUp(self) -> None:
        twr.WorkflowReceiptTests.setUp(self)
        self.addCleanup(twr.WorkflowReceiptTests.tearDown, self)
        self.project = self.root / "_project"
        self.scope = self.project / "04_active_scope.md"
        self.acceptance = self.project / "08_module_acceptance.md"

    def approve(self, module: str = "M1", now_iso: str = T0, **kwargs) -> dict:
        return wr.record_approval(
            self.state, self.root, module,
            approved_by="builder", evidence="Sí, adelante", risk_summary="riesgo",
            adr_refs=["ADR-001"] if module == "M1" else [], now_iso=now_iso, **kwargs,
        )

    def receipt(self, module: str = "M1") -> dict:
        return self.state["workflow_receipts"]["approvals"][module]

    def problems(self, module: str = "M1") -> list[str]:
        return list(wr.approval_problems(self.root, self.state, module))

    def register(self, *modules: str, bucket: str = "pending") -> None:
        registered = self.state.setdefault("modules", {}).setdefault(bucket, [])
        for module in modules:
            if module not in registered:
                registered.append(module)

    def replace(self, path: Path, old: str, new: str) -> None:
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, "el fixture no contiene el texto a sustituir")
        path.write_text(text.replace(old, new), encoding="utf-8")

    def assert_stale(self, dimension: str, module: str = "M1") -> None:
        problems = self.problems(module)
        self.assertTrue(
            any("stale" in p and dimension in p for p in problems),
            f"se esperaba stale en {dimension}: {problems}",
        )


class SeccionesBase(L6bBase):
    def setUp(self) -> None:
        super().setUp()
        self.scope.write_text(SCOPE, encoding="utf-8")
        self.acceptance.write_text(ACCEPTANCE, encoding="utf-8")
        self.register("M1", bucket="in_progress")
        self.register("M2")


class SeccionesAjenasSelladasTests(SeccionesBase):
    """Qué se sella como ajeno y qué deja de caducar por ello."""

    def test_el_recibo_sella_las_cabeceras_ajenas(self) -> None:
        receipt = self.approve()
        self.assertEqual(
            receipt.get("foreign_sections"),
            {"active_scope": [H_SCOPE_M2], "acceptance": [H_ACC_M2]},
        )

    def test_sin_otros_modulos_registrados_no_se_sella_ninguna(self) -> None:
        self.state["modules"] = {"in_progress": ["M1"]}
        receipt = self.approve()
        self.assertEqual(
            receipt.get("foreign_sections"), {"active_scope": [], "acceptance": []}
        )
        v2 = wr.approval_basis(self.root, self.state, "M1", basis_version=2)
        self.assertEqual(receipt["basis"]["active_scope"], v2["active_scope"])
        self.assertEqual(receipt["basis"]["acceptance"], v2["acceptance"])

    def test_reescribir_el_scope_de_otro_modulo_no_caduca(self) -> None:
        self.approve()
        self.replace(self.scope, "Scope de M2 original", "Scope de M2 precisado tras su revisión")
        self.assertEqual(self.problems(), [])

    def test_reescribir_los_ac_de_otro_modulo_no_caduca(self) -> None:
        self.approve()
        self.replace(self.acceptance, "- [ ] AC de M2 original", "- [ ] AC de M2 precisado")
        self.assertEqual(self.problems(), [])

    def test_quitar_lineas_de_la_seccion_de_otro_modulo_no_caduca(self) -> None:
        self.approve()
        self.replace(self.scope, "Scope de M2 original\n", "")
        self.assertEqual(self.problems(), [])

    def test_reescribir_mi_scope_sigue_caducando(self) -> None:
        self.approve()
        self.replace(self.scope, "Scope de M1", "Scope de M1 ampliado a producción")
        self.assert_stale("active_scope")

    def test_reescribir_mis_ac_sigue_caducando(self) -> None:
        self.approve()
        self.replace(self.acceptance, "- [ ] AC de M1", "- [ ] AC de M1 rebajado")
        self.assert_stale("acceptance")

    def test_reescribir_el_preambulo_comun_sigue_caducando(self) -> None:
        self.approve()
        self.replace(self.scope, "no tocar la tabla users", "se puede borrar la tabla users")
        self.assert_stale("active_scope")

    def test_reescribir_un_bloque_comun_sigue_caducando(self) -> None:
        self.approve()
        self.replace(self.scope, "Nota común original", "Nota común reescrita")
        self.assert_stale("active_scope")
        self.acceptance.write_text(ACCEPTANCE.replace("Cierre de fase original", "Otro cierre"))
        self.assert_stale("acceptance")

    def test_cambio_propio_aditivo_con_reescritura_ajena_no_caduca(self) -> None:
        self.approve()
        self.replace(self.scope, "Scope de M1\n", "Scope de M1\nDetalle añadido al implementar\n")
        self.replace(self.scope, "Scope de M2 original", "Scope de M2 reescrito")
        self.assertEqual(self.problems(), [])

    def test_cambio_propio_no_aditivo_con_reescritura_ajena_caduca(self) -> None:
        self.approve()
        self.replace(self.scope, "Scope de M1", "Otro scope para M1")
        self.replace(self.scope, "Scope de M2 original", "Scope de M2 reescrito")
        self.assert_stale("active_scope")

    def test_evidencia_anotada_en_mi_ac_con_reescritura_ajena_no_caduca(self) -> None:
        self.approve()
        self.replace(self.acceptance, "- [ ] AC de M1", "- [x] AC de M1 (evidencia: pytest rc=0)")
        self.replace(self.acceptance, "- [ ] AC de M2 original", "- [ ] AC de M2 precisado")
        self.assertEqual(self.problems(), [])

    def test_el_mensaje_dice_que_se_descontaron_secciones_ajenas(self) -> None:
        self.approve()
        self.replace(self.scope, "Scope de M1", "Otro scope para M1")
        message = " ".join(self.problems())
        self.assertIn("active_scope", message)
        self.assertIn("ajena", message)


class FormaDeLaCabeceraTests(SeccionesBase):
    """Solo es ajena la cabecera que nombra de forma estricta a otro módulo registrado."""

    def _sealed_for(self, header: str) -> list[str]:
        self.scope.write_text(f"## M1\nScope de M1\n\n{header}\ncontenido\n", encoding="utf-8")
        return self.approve()["foreign_sections"]["active_scope"]

    def test_formas_reconocidas(self) -> None:
        for header in (
            "## M2",
            "## `M2`",
            "## Módulo 2 — Dos (`M2`)",
            "## Módulo 2 — Dos (M2)",
            "## Modulo 2 - Dos (M2)",
            "## Módulo — M2",
            "## Módulo: M2",
            "## Módulo actual: M2",
            "## Módulo actual: `M2`",
        ):
            with self.subTest(header=header):
                self.assertEqual(self._sealed_for(header), [header])

    def test_formas_no_reconocidas_cuentan_para_todos(self) -> None:
        for header in (
            "## Restricciones (heredadas de M2)",
            "## Restricciones (M2)",
            "## Contexto previo (M2, conservado)",
            "## M2 — notas para todos",
            "## M2 (en cierre)",
            "## Notas sobre M2",
            "## M2 y M1",
            "## Módulo 2 — Compartido con M1 (`M2`)",
            "### M2",
        ):
            with self.subTest(header=header):
                self.assertEqual(self._sealed_for(header), [])
                self.replace(self.scope, "contenido", "contenido reescrito")
                self.assert_stale("active_scope")

    def test_cabecera_con_forma_de_id_sin_modulo_registrado_cuenta(self) -> None:
        self.assertEqual(self._sealed_for("## Restricciones_globales"), [])
        self.assertEqual(self._sealed_for("## M9"), [])

    def test_solo_es_ajena_la_seccion_que_leeria_el_cierre(self) -> None:
        """Una sola definición de sección: la de `module_section_lines`."""
        text = "## M1\nScope de M1\n\n## M2\nprimera\n\n## Módulo — M2\nsegunda\n"
        self.scope.write_text(text, encoding="utf-8")
        self.assertEqual(va.module_section_lines(text, "M2")[0], "## M2")
        self.assertEqual(self.approve()["foreign_sections"]["active_scope"], ["## M2"])
        self.replace(self.scope, "segunda", "segunda reescrita")
        self.assert_stale("active_scope")

    def test_mi_seccion_segun_el_cierre_nunca_es_ajena(self) -> None:
        """`M1-extra` contiene `M1` como palabra: el cierre de M1 podría leerla."""
        self.register("M1-extra")
        text = "## M1\nScope de M1\n\n## M1-extra\ncontenido\n"
        self.assertEqual(self._sealed_for("## M1-extra"), [])
        self.assertIsNotNone(va.module_section_lines(text, "M1"))

    def test_un_id_que_solo_comparte_prefijo_si_es_ajeno(self) -> None:
        self.register("M10")
        self.assertEqual(self._sealed_for("## M10"), ["## M10"])

    def test_el_bloque_ajeno_termina_en_una_cabecera_de_nivel_1(self) -> None:
        self.scope.write_text(
            "## M1\nScope de M1\n\n## M2\nScope de M2\n\n# Reglas globales\nRegla original\n",
            encoding="utf-8",
        )
        self.approve()
        self.replace(self.scope, "Regla original", "Regla reescrita")
        self.assert_stale("active_scope")


class HuellaAjenaALosFicherosTests(SeccionesBase):
    """La huella de un recibo sellado solo cambia si cambian los ficheros."""

    def _with_m3_section(self) -> None:
        self.scope.write_text(SCOPE + "\n## M3\nScope de M3 original\n", encoding="utf-8")

    def test_registrar_otro_modulo_despues_de_sellar_no_cambia_nada(self) -> None:
        self._with_m3_section()
        sealed = dict(self.approve()["basis"])
        self.register("M3")
        self.assertEqual(self.problems(), [])
        self.assertEqual(wr.approval_basis(self.root, self.state, "M1"), sealed)
        self.assertEqual(self.receipt()["foreign_sections"]["active_scope"], [H_SCOPE_M2])

    def test_la_seccion_de_un_modulo_sin_registrar_cuenta(self) -> None:
        self._with_m3_section()
        self.approve()
        self.replace(self.scope, "Scope de M3 original", "Scope de M3 reescrito")
        self.assert_stale("active_scope")

    def test_registrar_el_modulo_despues_no_blanquea_el_cambio(self) -> None:
        self._with_m3_section()
        self.approve()
        self.replace(self.scope, "Scope de M3 original", "Scope de M3 reescrito")
        self.register("M3")
        self.assert_stale("active_scope")

    def test_quitar_del_estado_un_modulo_sellado_como_ajeno_no_caduca(self) -> None:
        self.approve()
        self.state["modules"]["pending"].remove("M2")
        self.replace(self.scope, "Scope de M2 original", "Scope de M2 reescrito")
        self.assertEqual(self.problems(), [])

    def test_una_seccion_ajena_anadida_despues_no_caduca_ni_al_reescribirla(self) -> None:
        self.approve()
        self.register("M3")
        self.scope.write_text(SCOPE + "\n## M3\nScope de M3\n", encoding="utf-8")
        self.assertEqual(self.problems(), [])
        self.replace(self.scope, "Scope de M3\n", "Scope de M3 reescrito\n")
        self.assertEqual(self.problems(), [])

    def test_el_refresco_vuelve_a_calcular_la_lista(self) -> None:
        self._with_m3_section()
        self.approve()
        self.register("M3")
        self.replace(self.scope, "Scope de M1\n", "Scope de M1\nDetalle añadido\n")
        refreshed = wr.refresh_derived_approval(
            self.state, self.root, "M1",
            parent_fingerprint=self.receipt()["fingerprint"],
            derived_from=["active_scope"], evidence="detalle añadido al implementar",
            now_iso=ts(30),
        )
        self.assertEqual(refreshed["foreign_sections"]["active_scope"], [H_SCOPE_M2, "## M3"])
        self.assertEqual(self.problems(), [])
        self.replace(self.scope, "Scope de M3 original", "Scope de M3 reescrito")
        self.assertEqual(self.problems(), [])
        self.replace(self.scope, "Detalle añadido", "Detalle cambiado")
        self.assert_stale("active_scope")


class NadieEscondeUnCambioTests(SeccionesBase):
    """Casos adversarios: ninguna maniobra con cabeceras saca un cambio de la huella."""

    def test_anadir_una_cabecera_a_la_lista_sellada_caduca(self) -> None:
        self.approve()
        self.receipt()["foreign_sections"]["active_scope"].append("## Notas")
        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())
        self.replace(self.scope, "Nota común original", "Nota común reescrita")
        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())

    def test_anadir_mi_propia_cabecera_a_la_lista_sellada_caduca(self) -> None:
        self.approve()
        self.receipt()["foreign_sections"]["active_scope"].append("## M1")
        self.replace(self.scope, "Scope de M1", "Scope de M1 ampliado a producción")
        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())

    def test_quitar_la_lista_sellada_caduca(self) -> None:
        self.approve()
        del self.receipt()["foreign_sections"]
        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())

    def test_mover_una_linea_mia_bajo_una_cabecera_ajena_caduca(self) -> None:
        self.approve()
        self.scope.write_text(
            SCOPE.replace("Scope de M1\n", "").replace(
                "Scope de M2 original\n", "Scope de M2 original\nScope de M1\n"
            ),
            encoding="utf-8",
        )
        self.assert_stale("active_scope")

    def test_intercalar_una_cabecera_ajena_dentro_de_mi_seccion_caduca(self) -> None:
        self.approve()
        self.replace(self.scope, "## M1\nScope de M1\n", "## M1\n## M2\nScope de M1 rebajado\n")
        self.assert_stale("active_scope")

    def test_renombrar_una_cabecera_comun_a_la_de_otro_modulo_caduca(self) -> None:
        self.approve()
        self.replace(self.scope, "## Notas\nNota común original", "## M2\nNota común reescrita")
        self.assert_stale("active_scope")

    def test_renombrar_mi_cabecera_a_la_de_otro_modulo_caduca(self) -> None:
        self.approve()
        self.replace(self.acceptance, "## Módulo 1 — Uno (`M1`)\n- [ ] AC de M1", f"{H_ACC_M2}\n- [ ] AC rebajado")
        self.assert_stale("acceptance")

    def test_texto_nuevo_bajo_una_cabecera_ajena_no_cambia_mis_ac(self) -> None:
        """Límite conocido y aceptado: no caduca, y el cierre de M1 no lo lee."""
        self.approve()
        before = va.module_ac_lines(self.acceptance.read_text(encoding="utf-8"), "M1")
        self.replace(
            self.acceptance, "- [ ] AC de M2 original\n",
            "- [ ] AC de M2 original\n- [ ] Nota escrita en la sección de M2\n",
        )
        self.assertEqual(self.problems(), [])
        self.assertEqual(va.module_ac_lines(self.acceptance.read_text(encoding="utf-8"), "M1"), before)


class RecibosAnterioresTests(SeccionesBase):
    """D10: un recibo sin la lista sellada se valida sobre el fichero entero, como antes."""

    def _seal(self, version: int) -> dict:
        basis = wr.approval_basis(self.root, self.state, "M1", basis_version=version)
        snapshot = wr.approval_derived_snapshot(self.root, self.state, "M1", basis_version=version)
        extra: dict = {}
        if version >= 3:
            extra = {"covers_dependencies": [], "approved_dependencies": list(self.state["dependencies"])}
        receipt = {
            "status": "approved", "kind": "explicit", "module": "M1", "tier": "tier_2",
            "approved_by": "builder", "approval_evidence": "aprobado antes de L6b",
            "risk_summary": "riesgo", "adr_refs": ["ADR-001"],
            "basis": basis, "basis_version": version, **extra,
            "derived_snapshot_ref": wr.store_derived_snapshot(self.root, snapshot),
            "fingerprint": wr.approval_fingerprint(
                basis, tier="tier_2", approved_by="builder",
                approval_evidence="aprobado antes de L6b", risk_summary="riesgo",
                adr_refs=["ADR-001"], basis_version=version, **extra,
            ),
            "approved_at": T0,
        }
        wr._receipts(self.state)["approvals"]["M1"] = receipt
        return receipt

    def test_recibos_sin_lista_siguen_vigentes(self) -> None:
        for version in (2, 3):
            with self.subTest(version=version):
                self._seal(version)
                self.assertEqual(self.problems(), [])

    def test_recibos_sin_lista_caducan_al_reescribir_otra_seccion(self) -> None:
        for version in (2, 3):
            with self.subTest(version=version):
                self.scope.write_text(SCOPE, encoding="utf-8")
                self._seal(version)
                self.replace(self.scope, "Scope de M2 original", "Scope de M2 reescrito")
                self.assert_stale("active_scope")

    def test_anadir_la_lista_a_un_recibo_que_no_la_sello_caduca(self) -> None:
        self._seal(3)
        self.receipt()["foreign_sections"] = {"active_scope": [H_SCOPE_M2], "acceptance": []}
        self.replace(self.scope, "Scope de M2 original", "Scope de M2 reescrito")
        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())


class PruebasBase(L6bBase):
    def setUp(self) -> None:
        super().setUp()
        for module in ("M2", "M3"):
            self.state["work_tiers"][module] = {"tier": "tier_1", "rationale": "x"}
        self.register("M1", "M2", "M3", bucket="in_progress")
        (self.root / "tests").mkdir()
        (self.root / "src").mkdir()
        self.test_a = self.root / "tests" / "test_a.py"
        self.test_b = self.root / "tests" / "test_b.py"
        self.test_a.write_text("def test_a(): assert f() == 1  # v0\n")
        self.test_b.write_text("def test_b(): assert g() == 2  # v0\n")
        for module in ("m1", "m2", "m3"):
            (self.root / "src" / f"{module}.py").write_text(f"# {module}\n")
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "init")

    def touch_prod(self, module: str) -> None:
        path = self.root / "src" / f"{module.lower()}.py"
        path.write_text(path.read_text() + "# cambiado\n")

    def execute(self, module: str, when: str, declared: list[str] | None = None, **kwargs) -> dict:
        return wr.record_execution(
            self.state, self.root, module, kind="ok_automatizada", command="pytest", exit_code=0,
            evidence="suite ok verificada completamente rc=0", now_iso=when,
            test_changes=[
                {"path": p, "reason": "cambia el contrato", "authorized_by": "builder"}
                for p in (declared or [])
            ],
            **kwargs,
        )["test_gate"]

    def complete(self, module: str) -> None:
        self.state["modules"]["in_progress"].remove(module)
        self.register(module, bucket="completed")

    def identity(self, rel: str) -> str | None:
        return wr._test_baseline(self.root)["test_identities"].get(rel)

    def m2_covers_test_a(self) -> dict:
        """M2 se aprueba, cambia test_a y su código, lo declara, sella y cierra."""
        self.approve("M2", ts(5))
        self.test_a.write_text("def test_a(): assert f() == 10  # v1 de M2\n")
        self.touch_prod("M2")
        gate = self.execute("M2", ts(10), ["tests/test_a.py"])
        self.complete("M2")
        return gate

    def assert_rejected(self, *paths: str, module: str = "M1", when: str = ts(20), **kwargs) -> str:
        with self.assertRaises(ValueError) as caught:
            self.execute(module, when, **kwargs)
        message = str(caught.exception)
        self.assertIn(f"execution receipt rechazado para '{module}'", message)
        for path in paths:
            self.assertIn(path, message)
        return message


class PruebasCubiertasTests(PruebasBase):
    """Qué significa que otro módulo cubrió un cambio de prueba."""

    def test_el_gate_sella_de_donde_partia_y_donde_dejo_cada_prueba_declarada(self) -> None:
        self.approve()
        before = self.identity("tests/test_a.py")
        gate = self.m2_covers_test_a()
        self.assertEqual(
            gate.get("sealed_changes"),
            {"tests/test_a.py": {"from": before, "to": self.identity("tests/test_a.py")}},
        )
        self.assertNotEqual(before, self.identity("tests/test_a.py"))

    def test_cambio_declarado_y_sellado_por_otro_modulo_cerrado_queda_cubierto(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.touch_prod("M1")
        gate = self.execute("M1", ts(20))
        self.assertEqual(gate.get("covered_tests"), [{"path": "tests/test_a.py", "by": ["M2"]}])
        self.assertEqual(gate["modified_tests"], [])
        self.assertEqual(wr.test_gate_review_problems(self.state, "M1"), [])

    def test_prueba_borrada_y_declarada_por_otro_modulo_queda_cubierta(self) -> None:
        self.approve()
        self.approve("M2", ts(5))
        self.test_b.unlink()
        self.touch_prod("M2")
        sealed = self.execute("M2", ts(10), ["tests/test_b.py"])
        self.assertIsNone(sealed["sealed_changes"]["tests/test_b.py"]["to"])
        self.complete("M2")
        self.touch_prod("M1")
        gate = self.execute("M1", ts(20))
        self.assertEqual(gate.get("covered_tests"), [{"path": "tests/test_b.py", "by": ["M2"]}])

    def test_cadena_de_dos_modulos_queda_cubierta(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.approve("M3", ts(12))
        self.test_a.write_text("def test_a(): assert f() == 100  # v2 de M3\n")
        self.touch_prod("M3")
        self.execute("M3", ts(15), ["tests/test_a.py"])
        self.complete("M3")
        self.touch_prod("M1")
        gate = self.execute("M1", ts(20))
        self.assertEqual(gate.get("covered_tests"), [{"path": "tests/test_a.py", "by": ["M2", "M3"]}])

    def test_lo_cubierto_se_lista_aparte_y_lo_propio_se_sigue_pidiendo(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.test_b.write_text("def test_b(): pass  # debilitado por M1\n")
        self.touch_prod("M1")
        message = self.assert_rejected("tests/test_b.py")
        self.assertIn("cubiert", message)
        self.assertIn("M2", message)
        snippet = next(line for line in message.splitlines() if '"test_changes":' in line)
        declared = json.loads(snippet.split('"test_changes":', 1)[1])
        self.assertEqual([item["path"] for item in declared], ["tests/test_b.py"])
        gate = self.execute("M1", ts(21), ["tests/test_b.py"])
        self.assertEqual(gate["modified_tests"], ["tests/test_b.py"])
        self.assertEqual(gate.get("covered_tests"), [{"path": "tests/test_a.py", "by": ["M2"]}])


class PruebasNoCubiertasTests(PruebasBase):
    """Casos adversarios: cada condición que falta deja el cambio sin cubrir."""

    def test_el_otro_modulo_sello_sin_declararla(self) -> None:
        self.approve()
        self.approve("M2", ts(5))
        self.test_a.write_text("def test_a(): pass  # debilitado por M1\n")
        gate = self.execute("M2", ts(10))
        self.assertFalse(gate.get("sealed_changes"))
        self.complete("M2")
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")

    def test_la_declaro_sin_haberla_modificado_el(self) -> None:
        self.approve()
        self.test_a.write_text("def test_a(): pass  # debilitado por M1 antes de aprobar M2\n")
        self.approve("M2", ts(5))
        self.touch_prod("M2")
        gate = self.execute("M2", ts(10), ["tests/test_a.py"])
        self.assertFalse(gate.get("sealed_changes"))
        self.complete("M2")
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")

    def test_yo_la_toque_antes_de_que_el_otro_partiera(self) -> None:
        self.approve()
        self.test_a.write_text("def test_a(): pass  # debilitado por M1\n")
        self.m2_covers_test_a()
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")

    def test_yo_la_toque_despues_del_sello_del_otro(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.test_a.write_text("def test_a(): pass  # debilitado por M1 tras el sello de M2\n")
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")

    def test_el_otro_modulo_no_esta_cerrado(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.state["modules"]["completed"].remove("M2")
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")

    def test_el_otro_modulo_sello_antes_de_mi_aprobacion(self) -> None:
        """Repetir un estado antiguo: M2 dejó la prueba en v1, se revirtió a v0, y M1 la vuelve a v1."""
        v0 = self.test_a.read_text()
        self.approve("M2", ts(-20))
        self.test_a.write_text("def test_a(): assert f() == 10  # v1 de M2\n")
        v1 = self.test_a.read_text()
        self.touch_prod("M2")
        self.execute("M2", ts(-10), ["tests/test_a.py"])
        self.complete("M2")
        self.test_a.write_text(v0)
        self.approve("M1", T0)
        self.test_a.write_text(v1)
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")

    def test_cadena_rota_por_un_eslabon_sin_declarar(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.approve("M3", ts(12))
        self.test_a.write_text("def test_a(): pass  # v2 sin declarar\n")
        self.execute("M3", ts(15))
        self.complete("M3")
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")

    def test_mi_propio_recibo_anterior_no_me_cubre(self) -> None:
        self.approve()
        self.test_a.write_text("def test_a(): pass  # debilitado por M1\n")
        self.touch_prod("M1")
        self.execute("M1", ts(10), ["tests/test_a.py"])
        self.register("M1", bucket="completed")
        self.assert_rejected("tests/test_a.py")

    def test_con_base_explicita_no_hay_descuento(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py", base_ref="HEAD")

    def test_fecha_de_sello_ilegible_no_cubre(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.state["workflow_receipts"]["executions"]["M2"]["recorded_at"] = "ayer por la tarde"
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")


class ReaprobacionTests(PruebasBase):
    """Tras re-aprobar, la base y su reloj siguen siendo los de la aprobación de origen."""

    def test_la_fecha_de_origen_se_conserva_y_se_encadena(self) -> None:
        self.assertNotIn("approved_at_origin", self.approve("M1", T0))
        self.assertEqual(self.approve("M1", ts(15))["approved_at_origin"], T0)
        third = self.approve("M1", ts(30))
        self.assertEqual(third["approved_at_origin"], T0)
        self.assertEqual(third["approved_at"], ts(30))

    def test_lo_cubierto_antes_de_re_aprobar_sigue_cubierto(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.approve("M1", ts(15))
        self.touch_prod("M1")
        gate = self.execute("M1", ts(20))
        self.assertEqual(gate.get("covered_tests"), [{"path": "tests/test_a.py", "by": ["M2"]}])

    def test_re_aprobar_no_blanquea_un_cambio_propio(self) -> None:
        self.approve()
        self.test_a.write_text("def test_a(): pass  # debilitado por M1\n")
        self.approve("M1", ts(15))
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")

    def test_recibo_re_aprobado_sin_fecha_de_origen_usa_la_ultima_y_descuenta_menos(self) -> None:
        self.approve()
        self.m2_covers_test_a()
        self.approve("M1", ts(15))
        del self.receipt()["approved_at_origin"]
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py")


class ReciboManipuladoTests(L6bBase):
    """Hueco anterior a L6: un cambio aditivo no puede tapar un recibo manipulado."""

    def _additive_change(self) -> None:
        model = self.project / "02_technical_plan" / "data_model.md"
        model.write_text(model.read_text(encoding="utf-8") + "\nentidad nueva\n", encoding="utf-8")

    def _planned_change(self) -> None:
        self.acceptance.write_text("## M1\n- [x] AC aprobado (evidencia: pytest rc=0)\n", encoding="utf-8")

    def _seal_v2(self) -> None:
        basis = wr.approval_basis(self.root, self.state, "M1", basis_version=2)
        snapshot = wr.approval_derived_snapshot(self.root, self.state, "M1", basis_version=2)
        wr._receipts(self.state)["approvals"]["M1"] = {
            "status": "approved", "kind": "explicit", "module": "M1", "tier": "tier_2",
            "approved_by": "builder", "approval_evidence": "Sí, adelante",
            "risk_summary": "riesgo", "adr_refs": ["ADR-001"],
            "basis": basis, "basis_version": 2,
            "derived_snapshot_ref": wr.store_derived_snapshot(self.root, snapshot),
            "fingerprint": wr.approval_fingerprint(
                basis, tier="tier_2", approved_by="builder",
                approval_evidence="Sí, adelante", risk_summary="riesgo", adr_refs=["ADR-001"],
            ),
            "approved_at": T0,
        }

    def test_cambio_aditivo_o_previsto_sin_manipular_sigue_sin_caducar(self) -> None:
        for seal in (self.approve, self._seal_v2):
            for change in (self._additive_change, self._planned_change):
                with self.subTest(seal=seal.__name__, change=change.__name__):
                    twr.WorkflowReceiptTests.setUp(self)
                    self.project = self.root / "_project"
                    self.acceptance = self.project / "08_module_acceptance.md"
                    seal()
                    change()
                    self.assertEqual(self.problems(), [])
                    self.tmp.cleanup()
        twr.WorkflowReceiptTests.setUp(self)

    def test_recibo_manipulado_con_cambio_aditivo_o_previsto_caduca(self) -> None:
        for seal in (self.approve, self._seal_v2):
            for change in (self._additive_change, self._planned_change):
                for field, value in (
                    ("approval_evidence", "evidencia escrita a posteriori"),
                    ("risk_summary", "sin riesgo"),
                    ("adr_refs", ["ADR-001", "ADR-001"]),
                ):
                    with self.subTest(seal=seal.__name__, change=change.__name__, field=field):
                        twr.WorkflowReceiptTests.setUp(self)
                        self.project = self.root / "_project"
                        self.acceptance = self.project / "08_module_acceptance.md"
                        seal()
                        change()
                        self.receipt()[field] = value
                        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())
                        self.tmp.cleanup()
        twr.WorkflowReceiptTests.setUp(self)

    def test_base_guardada_manipulada_con_cambio_aditivo_caduca(self) -> None:
        self.approve()
        self._additive_change()
        self.receipt()["basis"]["prd"] = "0" * 64
        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())


class AdrDocumentadaTests(L6bBase):
    """El refresco valida la ADR que cita `documents_decision`."""

    def _refresh_with(self, documents: object) -> dict:
        self.approve()
        self.state["_decisions"].append({"id": "ADR-050", "module_id": "M2", "decision": "de otro módulo"})
        self.state["_decisions"].append({
            "id": "ADR-002", "module_id": "M1", "title": "t", "decision": "d",
            "documents_decision": documents,
        })
        return wr.refresh_derived_approval(
            self.state, self.root, "M1",
            parent_fingerprint=self.receipt()["fingerprint"],
            derived_from=["ADR-002"], evidence="refresco", now_iso=ts(30),
        )

    def test_adr_citada_que_existe_y_aplica_se_admite(self) -> None:
        self.assertEqual(self._refresh_with("ADR-001")["documents_decisions"], {"ADR-002": "ADR-001"})

    def test_adr_sin_cita_se_admite_como_hasta_ahora(self) -> None:
        self.assertEqual(self._refresh_with(None)["documents_decisions"], {"ADR-002": None})

    def test_adr_citada_inexistente_se_rechaza(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self._refresh_with("ADR-999")
        self.assertIn("ADR-999", str(caught.exception))

    def test_adr_citada_de_otro_modulo_se_rechaza(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self._refresh_with("ADR-050")
        self.assertIn("ADR-050", str(caught.exception))

    def test_adr_que_se_cita_a_si_misma_se_rechaza(self) -> None:
        with self.assertRaises(ValueError):
            self._refresh_with("ADR-002")

    def test_el_rechazo_no_deja_el_recibo_a_medias(self) -> None:
        with self.assertRaises(ValueError):
            self._refresh_with("ADR-999")
        self.assertEqual(self.receipt()["kind"], "explicit")
        self.assertNotIn("documents_decisions", self.receipt())


if __name__ == "__main__":
    unittest.main()
