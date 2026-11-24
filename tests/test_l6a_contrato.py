"""Contrato de L6a (FW-337): lo que el patch de L6 debe cumplir para integrarse.

Sale de la revisión del patch de L6 (rama wip/l6). Cada test reproduce un
defecto hallado en esa revisión o fija una retirada: las secciones por módulo
(punto 4) y el descuento de pruebas de otros módulos (punto 6) salen de L6a y
pasan a L6b, porque tal como estaban abrían vías de evasión.

Estos tests los escribe el revisor. El ejecutor no los modifica.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import test_workflow_receipts as twr
import workflow_receipts as wr

T0 = "2026-07-15T12:00:00+00:00"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True,
    )


class L6aBase(unittest.TestCase):
    def setUp(self) -> None:
        twr.WorkflowReceiptTests.setUp(self)
        self.addCleanup(twr.WorkflowReceiptTests.tearDown, self)
        self.project = self.root / "_project"

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

    def seal_v2(self) -> dict:
        """Recibo como los sellaba 5.2.3: base y snapshot de versión 2, sin claves nuevas."""
        basis = wr.approval_basis(self.root, self.state, "M1", basis_version=2)
        snapshot = wr.approval_derived_snapshot(self.root, self.state, "M1", basis_version=2)
        receipt = {
            "status": "approved", "kind": "explicit", "module": "M1", "tier": "tier_2",
            "approved_by": "builder", "approval_evidence": "aprobado v2",
            "risk_summary": "riesgo", "adr_refs": ["ADR-001"],
            "basis": basis, "basis_version": 2,
            "derived_snapshot_ref": wr.store_derived_snapshot(self.root, snapshot),
            "fingerprint": wr.approval_fingerprint(
                basis, tier="tier_2", approved_by="builder",
                approval_evidence="aprobado v2", risk_summary="riesgo", adr_refs=["ADR-001"],
            ),
            "approved_at": T0,
        }
        wr._receipts(self.state)["approvals"]["M1"] = receipt
        return receipt


class DependenciasCubiertasSelladasTests(L6aBase):
    """Punto 1: lo que autoriza una dependencia forma parte de lo sellado."""

    def test_anadir_covers_dependencies_tras_sellar_no_autoriza_nada(self) -> None:
        self.approve()
        self.state["dependencies"].append("evil==1.0")
        self.receipt()["covers_dependencies"] = ["evil==1.0"]
        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())

    def test_ampliar_covers_dependencies_tras_sellar_no_autoriza_nada(self) -> None:
        self.approve(covers_dependencies=["rich==15.0.0"])
        self.state["dependencies"].append("evil==1.0")
        self.receipt()["covers_dependencies"] = ["evil==1.0", "rich==15.0.0"]
        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())

    def test_reescribir_approved_dependencies_no_oculta_una_dependencia_quitada(self) -> None:
        self.state["dependencies"] = ["a==1", "b==1"]
        self.approve(covers_dependencies=["rich==15.0.0"])
        self.state["dependencies"] = ["a==1", "rich==15.0.0"]
        self.receipt()["approved_dependencies"] = ["a==1"]
        self.assertTrue(any("stale" in p for p in self.problems()), self.problems())

    def test_la_dependencia_cubierta_sigue_sin_caducar(self) -> None:
        self.approve(covers_dependencies=["rich==15.0.0"])
        self.state["dependencies"].append("rich==15.0.0")
        self.assertEqual(self.problems(), [])

    def test_covers_dependencies_que_no_es_lista_se_rechaza(self) -> None:
        with self.assertRaises(ValueError):
            self.approve(covers_dependencies="rich==15.0.0")

    def test_el_contrato_de_aprobacion_nombra_el_campo_opcional(self) -> None:
        self.assertIn("covers_dependencies", wr.APPROVAL_RECEIPT_CONTRACT)
        self.assertNotIn("todos los campos son obligatorios", wr.APPROVAL_RECEIPT_CONTRACT)


class DecisionesTests(L6aBase):
    """Punto 2: el digest de cierre ve todas las ADR posteriores a la aprobación."""

    def _refresh(self, derived_from: list[str], when: str) -> dict:
        return wr.refresh_derived_approval(
            self.state, self.root, "M1",
            parent_fingerprint=self.receipt()["fingerprint"],
            derived_from=derived_from, evidence="refresco", now_iso=when,
        )

    def test_added_decisions_se_acumula_entre_refrescos(self) -> None:
        self.approve()
        self.state["_decisions"].append({
            "id": "ADR-002", "module_id": "M1", "title": "t", "decision": "d",
            "documents_decision": "ADR-001",
        })
        self._refresh(["ADR-002"], "2026-07-16T12:00:00+00:00")
        model = self.project / "02_technical_plan" / "data_model.md"
        model.write_text(model.read_text(encoding="utf-8") + "\nlínea nueva\n", encoding="utf-8")
        second = self._refresh(["data_model"], "2026-07-17T12:00:00+00:00")
        self.assertEqual(second["added_decisions"], ["ADR-002"])
        self.state["_decisions"].append({"id": "ADR-003", "module_id": "M1", "title": "t", "decision": "d"})
        third = self._refresh(["ADR-003"], "2026-07-18T12:00:00+00:00")
        self.assertEqual(third["added_decisions"], ["ADR-002", "ADR-003"])
        self.assertEqual(third["documents_decisions"], {"ADR-002": "ADR-001", "ADR-003": None})

    def test_adr_aprobada_alterada_no_ofrece_el_refresco(self) -> None:
        self.approve()
        self.state["_decisions"][0]["decision"] = "otra cosa"
        message = " ".join(self.problems())
        self.assertIn("decisions", message)
        self.assertIn("ADR-001", message)
        self.assertNotIn("refresh_approval_receipt", message)
        self.assertIn("nueva aprobación", message)

    def test_adr_nueva_y_otra_alterada_no_ofrece_el_refresco(self) -> None:
        self.approve()
        self.state["_decisions"][0]["decision"] = "otra cosa"
        self.state["_decisions"].append({"id": "ADR-002", "module_id": "M1", "title": "t", "decision": "d"})
        message = " ".join(self.problems())
        self.assertNotIn("refresh_approval_receipt", message)

    def test_adr_solo_anadida_sigue_ofreciendo_el_refresco(self) -> None:
        self.approve()
        self.state["_decisions"].append({"id": "ADR-002", "module_id": "M1", "title": "t", "decision": "d"})
        message = " ".join(self.problems())
        self.assertIn("ADR-002", message)
        self.assertIn("refresh_approval_receipt", message)


class RetiradaSeccionesTests(L6aBase):
    """Punto 4 fuera de L6a: scope y aceptación se sellan sobre el fichero entero."""

    def test_no_queda_extractor_de_secciones(self) -> None:
        for name in ("_extract_module_section_text", "_header_module_id",
                     "_normalized_module_section_hash", "_ids_match"):
            self.assertFalse(hasattr(wr, name), name)

    def test_recibo_nuevo_sella_scope_y_aceptacion_como_la_version_2(self) -> None:
        self.approve()
        basis = self.receipt()["basis"]
        v2 = wr.approval_basis(self.root, self.state, "M1", basis_version=2)
        self.assertEqual(basis["active_scope"], v2["active_scope"])
        self.assertEqual(basis["acceptance"], v2["acceptance"])

    def test_cabecera_con_forma_de_id_sigue_contando(self) -> None:
        scope = self.project / "04_active_scope.md"
        scope.write_text("## M1\nScope M1\n\n## Restricciones_globales\nNo tocar la tabla users\n")
        self.approve()
        scope.write_text("## M1\nScope M1\n\n## Restricciones_globales\nSe puede borrar la tabla users\n")
        self.assertTrue(any("active_scope" in p for p in self.problems()), self.problems())

    def test_el_mensaje_no_habla_de_secciones(self) -> None:
        scope = self.project / "04_active_scope.md"
        scope.write_text("## M1\nScope M1\n")
        self.approve()
        scope.write_text("## M1\nScope M1 reescrito\n")
        message = " ".join(self.problems())
        self.assertIn("active_scope", message)
        self.assertNotIn("sección", message)
        self.assertNotIn("aisl", message)


class RetiradaDescuentoDePruebasTests(L6aBase):
    """Punto 6 fuera de L6a: el gate de FW-250 no descuenta nada."""

    def test_no_queda_descuento(self) -> None:
        self.assertFalse(hasattr(wr, "_discount_covered_tests"))

    def test_test_propio_debilitado_no_se_descuenta_por_el_recibo_de_otro_modulo(self) -> None:
        (self.root / "tests").mkdir(exist_ok=True)
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "tests" / "test_m1.py").write_text("def test_1(): assert f() == 1\n")
        (self.root / "src" / "m1.py").write_text("# m1\n")
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "init")
        self.approve()
        self.state.setdefault("work_tiers", {})["M2"] = {"tier": "tier_1", "rationale": "x"}
        self.approve("M2", "2026-07-15T12:05:00+00:00")
        (self.root / "tests" / "test_m1.py").write_text("def test_1(): pass\n")
        other = wr.record_execution(
            self.state, self.root, "M2", kind="ok_automatizada", command="pytest", exit_code=0,
            evidence="suite ok verificada completamente rc=0",
            now_iso="2026-07-15T12:10:00+00:00", test_changes=[],
        )
        for key in ("discounted_tests", "sealed_test_identities"):
            self.assertNotIn(key, other["test_gate"])
        self.state.setdefault("modules", {}).setdefault("completed", []).append("M2")
        (self.root / "src" / "m1.py").write_text("# m1 cambiado\n")
        with self.assertRaises(ValueError) as caught:
            wr.record_execution(
                self.state, self.root, "M1", kind="ok_automatizada", command="pytest", exit_code=0,
                evidence="suite ok verificada completamente rc=0",
                now_iso="2026-07-15T12:20:00+00:00", test_changes=[],
            )
        self.assertIn("execution receipt rechazado para 'M1'", str(caught.exception))
        self.assertIn("tests/test_m1.py", str(caught.exception))


class RecibosAntiguosTests(L6aBase):
    """D10: un recibo de 5.2.3 se valida como antes y su mensaje no miente."""

    def test_recibo_v2_con_la_justificacion_del_tier_cambiada(self) -> None:
        self.seal_v2()
        self.state["work_tiers"]["M1"] = {**self.state["work_tiers"]["M1"], "rationale": "otra justificación"}
        message = " ".join(self.problems())
        self.assertIn("tier", message)
        self.assertNotIn("cambió de tier_2 a tier_2", message)
        self.assertIn("justificación", message)

    def test_recibo_v2_con_dependencia_nueva_nombra_solo_la_nueva_o_ninguna(self) -> None:
        previas = list(self.state["dependencies"])
        self.assertTrue(previas, "el fixture debe traer dependencias")
        self.seal_v2()
        self.state["dependencies"].append("nueva==1.0")
        message = " ".join(self.problems())
        self.assertIn("dependencies", message)
        for dep in previas:
            self.assertNotIn(str(dep), message)

    def test_recibo_nuevo_con_el_nivel_de_tier_cambiado(self) -> None:
        self.approve()
        self.state["work_tiers"]["M1"] = {"tier": "tier_1", "rationale": "x"}
        message = " ".join(self.problems())
        self.assertIn("tier_2", message)
        self.assertIn("tier_1", message)
        self.assertIn("nueva aprobación", message)


class HuellaDeRevisionTests(L6aBase):
    """Punto 3: los declarados no cuentan como código, sin caducar lo ya sellado."""

    def setUp(self) -> None:
        super().setUp()
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src" / "app.py").write_text("x = 1\n")
        (self.project / "m1_evidence.json").write_text("{}\n")
        (self.project / "08_module_acceptance.md").write_text(
            "## M1\n- [ ] AC1: evidencia en _project/m1_evidence.json\n"
        )
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "init")

    def _sealed_by_5_2_3(self) -> str:
        """Huella como la calculaba 5.2.3: los declarados contaban como código."""
        return wr._compute_workspace_content_hashes(self.root)[2]

    def test_recibo_sellado_antes_de_la_regla_sigue_vigente(self) -> None:
        sealed = self._sealed_by_5_2_3()
        self.assertNotEqual(sealed, str(wr.workspace_diff_hash(self.root)))
        self.assertTrue(wr.diff_hash_matches(sealed, wr.workspace_diff_hash(self.root)))

    def test_recibo_sellado_antes_de_la_regla_caduca_si_cambia_el_codigo(self) -> None:
        sealed = self._sealed_by_5_2_3()
        (self.root / "src" / "app.py").write_text("x = 2\n")
        self.assertFalse(wr.diff_hash_matches(sealed, wr.workspace_diff_hash(self.root)))

    def test_recibo_sellado_antes_de_la_regla_caduca_si_cambia_el_declarado(self) -> None:
        sealed = self._sealed_by_5_2_3()
        (self.project / "m1_evidence.json").write_text('{"cambiado": true}\n')
        self.assertFalse(wr.diff_hash_matches(sealed, wr.workspace_diff_hash(self.root)))

    def test_recibo_nuevo_no_caduca_al_cambiar_el_declarado(self) -> None:
        sealed = str(wr.workspace_diff_hash(self.root))
        (self.project / "m1_evidence.json").write_text('{"aplicado": true}\n')
        self.assertTrue(wr.diff_hash_matches(sealed, wr.workspace_diff_hash(self.root)))

    def test_aceptacion_con_bytes_no_utf8_no_rompe_la_huella(self) -> None:
        (self.project / "08_module_acceptance.md").write_bytes(b"## M1\n- [ ] AC1 \xff\xfe _project/m1_evidence.json\n")
        wr.workspace_diff_hash(self.root)

    def test_declarado_desde_module_specs_en_disco(self) -> None:
        (self.project / "08_module_acceptance.md").write_text("## M1\n- [ ] AC1\n")
        (self.project / "m1_notas.txt").write_text("uno\n")
        (self.project / "03_project_state.json").write_text(
            '{"module_specs": {"M1": {"evidencia": "ver _project/m1_notas.txt"}}}', encoding="utf-8"
        )
        self.assertIn("_project/m1_notas.txt", wr._collect_declared_evidence_paths(self.root))


if __name__ == "__main__":
    unittest.main()
