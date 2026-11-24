"""5.3.3 (L7.3, TL-FW-327) — lo que otro módulo escribe después del sello no toca la base.

La base de `active_scope` y `acceptance` descontaba solo las secciones ajenas que
había al sellar. La sección que otro módulo añadía después entraba en la base: si
solo crecía, la toleraba la aditividad, pero aparecía como dimensión caducada en
cuanto caducaba cualquier otra cosa, y bloqueaba la transición prevista (evidencia
anotada) del propio módulo. Ahora también se descuenta la sección de otro módulo
registrado que no contaba al sellar (su cabecera no está en el snapshot del recibo):
añadirla, cambiarla o quitarla no caduca. Lo que contó al sellar sigue contando
(preámbulo, bloques comunes, la sección de un módulo entonces sin registrar; contrato
de L6b), y lo propio que cambia de fondo sigue caducando (D18).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import workflow_receipts as wr
from test_l6b_contrato import H_ACC_M2, H_SCOPE_M2, SeccionesBase, T0  # noqa: F401 (solo la base, sin sus tests)

M3_SCOPE = "\n## M3\nScope de M3\n"
M3_ACC = "\n## Módulo 3 — Tres (`M3`)\n- [ ] AC de M3\n"


class SeccionAjenaPosteriorAlSelloTests(SeccionesBase):
    def _m3_tras_el_sello(self) -> None:
        self.approve()
        self.register("M3")
        self.scope.write_text(self.scope.read_text(encoding="utf-8") + M3_SCOPE, encoding="utf-8")
        self.acceptance.write_text(self.acceptance.read_text(encoding="utf-8") + M3_ACC, encoding="utf-8")

    def _stale_message(self) -> str:
        return " ".join(p for p in self.problems() if "stale" in p)

    # --- lo ajeno no caduca (rojo antes del arreglo) ---------------------------
    def test_la_base_no_cambia_si_otro_modulo_anade_y_reescribe_su_seccion(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.scope, "Scope de M3\n", "Scope de M3 reescrito por completo\n")
        self.replace(self.acceptance, "- [ ] AC de M3\n", "- [ ] AC de M3 sustituido\n")
        basis = wr.approval_basis(self.root, self.state, "M1")
        self.assertEqual(basis["active_scope"], self.receipt()["basis"]["active_scope"])
        self.assertEqual(basis["acceptance"], self.receipt()["basis"]["acceptance"])
        self.assertEqual(self.problems(), [])

    def test_evidencia_en_mi_ac_con_seccion_ajena_nueva_y_cambiada_no_caduca(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.acceptance, "- [ ] AC de M3\n", "- [ ] AC de M3 sustituido\n")
        self.replace(self.acceptance, "- [ ] AC de M1", "- [x] AC de M1 (evidencia: pytest rc=0)")
        self.assertEqual(self.problems(), [])

    def test_evidencia_en_mi_scope_con_seccion_ajena_nueva_y_cambiada_no_caduca(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.scope, "Scope de M3\n", "Scope de M3 reescrito\n")
        self.replace(self.scope, "Scope de M1\n", "Scope de M1 (evidencia: commit abc123)\n")
        self.assertEqual(self.problems(), [])

    def test_si_caduca_otra_dimension_scope_y_acs_no_salen_por_lo_ajeno(self) -> None:
        self._m3_tras_el_sello()
        (self.project / "02_technical_plan" / "data_model.md").write_text("# Modelo reescrito\n", encoding="utf-8")
        message = self._stale_message()
        self.assertIn("data_model", message)
        self.assertNotIn("active_scope", message)
        self.assertNotIn("acceptance", message)

    def test_el_refresco_no_ve_cambio_en_scope_por_lo_ajeno(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.scope, "Scope de M3\n", "Scope de M3 reescrito\n")
        with self.assertRaisesRegex(ValueError, "sin cambios en superficies derivables"):
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=self.receipt()["fingerprint"],
                derived_from=["active_scope"], evidence="solo cambió la sección de M3", now_iso=T0,
            )

    def test_quitar_la_seccion_ajena_posterior_no_caduca(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.scope, M3_SCOPE, "")
        self.assertEqual(self.problems(), [])

    # --- lo propio sigue caducando (D18, una por dimensión) ---------------------
    def test_reescribir_mi_scope_con_seccion_ajena_nueva_caduca(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.scope, "Scope de M1\n", "Scope de M1 ampliado a otra tabla\n")
        self.assert_stale("active_scope")

    def test_quitar_mi_ac_con_seccion_ajena_nueva_caduca(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.acceptance, "- [ ] AC de M1\n", "")
        self.assert_stale("acceptance")

    def test_borrar_mi_seccion_de_scope_caduca(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.scope, "## M1\nScope de M1\n", "")
        self.assert_stale("active_scope")

    def test_borrar_mi_seccion_de_acs_caduca(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.acceptance, "## Módulo 1 — Uno (`M1`)\n- [ ] AC de M1\n", "")
        self.assert_stale("acceptance")

    def test_mover_mi_linea_bajo_la_cabecera_ajena_nueva_caduca(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.scope, "## M1\nScope de M1\n", "## M1\n")
        self.replace(self.scope, "## M3\nScope de M3\n", "## M3\nScope de M1\n")
        self.assert_stale("active_scope")

    def test_cabecera_ajena_nueva_intercalada_en_mi_seccion_caduca(self) -> None:
        self._m3_tras_el_sello()
        self.replace(self.acceptance, "## Módulo 1 — Uno (`M1`)\n", "## Módulo 1 — Uno (`M1`)\n## M3\n")
        self.assert_stale("acceptance")

    def test_renombrar_un_bloque_comun_a_un_modulo_nuevo_caduca(self) -> None:
        self.approve()
        self.register("M3")
        self.replace(self.scope, "## Notas\nNota común original\n", "## M3\nNota común reescrita\n")
        self.assert_stale("active_scope")

    def test_la_seccion_que_conto_al_sellar_sigue_contando_aunque_se_registre(self) -> None:
        self.scope.write_text(self.scope.read_text(encoding="utf-8") + M3_SCOPE, encoding="utf-8")
        self.approve()
        self.register("M3")
        self.replace(self.scope, "Scope de M3\n", "Scope de M3 reescrito\n")
        self.assert_stale("active_scope")

    # --- recibos anteriores ----------------------------------------------------
    def test_sin_snapshot_legible_no_se_descuenta_lo_nuevo(self) -> None:
        self._m3_tras_el_sello()
        self.receipt()["derived_snapshot_ref"] = "0" * 64
        self.replace(self.scope, "Scope de M3\n", "Scope de M3 reescrito\n")
        self.assert_stale("active_scope")

    def test_recibo_sin_lista_sellada_tambien_descuenta_lo_nuevo(self) -> None:
        basis = wr.approval_basis(self.root, self.state, "M1", basis_version=2)
        snapshot = wr.approval_derived_snapshot(self.root, self.state, "M1", basis_version=2)
        fields = {"tier": "tier_2", "approved_by": "builder", "approval_evidence": "aprobado antes",
                  "risk_summary": "riesgo", "adr_refs": ["ADR-001"]}
        wr._receipts(self.state)["approvals"]["M1"] = {
            "status": "approved", "kind": "explicit", "module": "M1", **fields,
            "approval_evidence": "aprobado antes", "basis": basis, "basis_version": 2,
            "derived_snapshot_ref": wr.store_derived_snapshot(self.root, snapshot),
            "fingerprint": wr.approval_fingerprint(basis, basis_version=2, **fields), "approved_at": T0,
        }
        self.assertEqual(self.problems(), [])
        self.register("M3")
        self.scope.write_text(self.scope.read_text(encoding="utf-8") + M3_SCOPE, encoding="utf-8")
        self.replace(self.scope, "Scope de M3\n", "Scope de M3 reescrito\n")
        self.replace(self.scope, "Scope de M1\n", "Scope de M1 (evidencia: commit abc123)\n")
        self.assertEqual(self.problems(), [])
        self.replace(self.scope, "Scope de M2 original", "Scope de M2 reescrito")
        self.assert_stale("active_scope")



class LoPropioNoSeEscondeTrasLoAjenoTests(SeccionesBase):
    """Apartar la sección ajena que cambió no esconde un cambio en lo propio o común."""

    def test_scope_borrar_lo_comun_tras_la_seccion_ajena_que_cambio_caduca(self) -> None:
        self.approve()
        self.replace(self.scope, "Scope de M2 original\n", "Scope de M2 reescrito\n")
        self.replace(self.scope, "\n## Notas\nNota común original\n", "")
        self.assert_stale("active_scope")

    def test_acs_pasar_mi_seccion_a_otro_modulo_caduca(self) -> None:
        self.approve()
        self.replace(self.acceptance, "## Módulo 1 — Uno (`M1`)\n", "## Módulo 1 — Uno (`M2`)\n")
        self.assert_stale("acceptance")


class LimiteDeLaSeccionAjenaTests(SeccionesBase):
    """Tercera revisión (hallazgo 5, hueco de L6b): una cabecera H1/H2 setext o una
    ATX sangrada de 1 a 3 espacios cierra la sección ajena; lo que sigue cuenta."""

    def _cambiar_lo_comun_caduca(self, path, bloque: str, viejo: str, nuevo: str, dim: str) -> None:
        ancla = "Scope de M2 original\n" if path == self.scope else "- [ ] AC de M2 original\n"
        self.replace(path, ancla, ancla + bloque)
        self.approve()
        self.replace(path, viejo, nuevo)
        self.assert_stale(dim)

    def test_una_setext_h2_tras_la_seccion_ajena_del_scope_caduca(self) -> None:
        self._cambiar_lo_comun_caduca(
            self.scope, "\nNotas comunes\n-------------\nNota común: no tocar users\n",
            "no tocar users", "tocar users libremente", "active_scope",
        )

    def test_el_texto_de_la_setext_tambien_cuenta(self) -> None:
        self._cambiar_lo_comun_caduca(
            self.scope, "\nNotas comunes\n-------------\nNota común: no tocar users\n",
            "Notas comunes\n", "Notas de M2\n", "active_scope",
        )

    def test_una_h2_sangrada_tras_la_seccion_ajena_del_scope_caduca(self) -> None:
        self._cambiar_lo_comun_caduca(
            self.scope, "\n ## Reglas comunes\nNota común: no tocar users\n",
            "no tocar users", "tocar users libremente", "active_scope",
        )

    def test_una_setext_h1_tras_la_seccion_ajena_de_los_acs_caduca(self) -> None:
        self._cambiar_lo_comun_caduca(
            self.acceptance, "\nCriterios comunes\n=================\n- [ ] Todo endpoint autenticado\n",
            "- [ ] Todo endpoint autenticado\n", "- [ ] Ningún endpoint autenticado\n", "acceptance",
        )

    def test_un_separador_tras_una_linea_en_blanco_no_cierra_la_seccion_ajena(self) -> None:
        self.replace(self.scope, "Scope de M2 original\n", "Scope de M2 original\n\n---\nMás scope de M2\n")
        self.approve()
        self.replace(self.scope, "Más scope de M2\n", "Más scope de M2 reescrito\n")
        self.assertEqual(self.problems(), [])


class CabeceraEnganosaDeScopeYAcsTests(SeccionesBase):
    """Cuarta ronda (riesgo anotado tras la tercera revisión): la cabecera que nombra
    a M2 pero esconde el id propio tras un carácter invisible o un homoglifo se lee
    «M1 … (M2)», una sección compartida que cuenta; no se descuenta como ajena."""

    def _sellar_y_cambiar(self, path, cabecera: str, viejo: str, nuevo: str, dim: str) -> None:
        self.replace(path, (H_SCOPE_M2 if path == self.scope else H_ACC_M2) + "\n", cabecera + "\n")
        self.approve()
        self.replace(path, viejo, nuevo)
        self.assert_stale(dim)

    def test_un_invisible_en_mi_id_no_hace_ajena_la_seccion_del_scope(self) -> None:
        self._sellar_y_cambiar(
            self.scope, "## Módulo M\u200b1 — Pagos (M2)",
            "Scope de M2 original", "Scope de M2 reescrito", "active_scope",
        )

    def test_un_homoglifo_en_mi_id_no_hace_ajena_la_seccion_de_los_acs(self) -> None:
        self._sellar_y_cambiar(
            self.acceptance, "## Módulo \u041c1 — Pagos (M2)",
            "- [ ] AC de M2 original", "- [ ] AC de M2 reescrito", "acceptance",
        )


class SeparadorQueMarkdownNoVeTests(SeccionesBase):
    """Revisión del corte (FN-1): `splitlines()` también corta en \\x1e, \\x0c, \\x85
    o U+2028, que en Markdown no terminan la línea. `…\\x1e## M3` se ve como un
    párrafo, no como la sección de M3: no esconde lo que sigue."""

    def test_una_cabecera_ajena_tras_un_separador_de_control_no_esconde_mi_ac(self) -> None:
        self.approve()
        self.register("M3")
        self.replace(
            self.acceptance, "- [ ] AC de M1",
            "- [x] AC de M1 (evidencia: pytest rc=0)\x1e## M3\n- [ ] AC nueva de M1",
        )
        self.assert_stale("acceptance")

    def test_una_cabecera_ajena_tras_u2028_no_esconde_lo_comun_del_scope(self) -> None:
        self.approve()
        self.register("M3")
        self.replace(self.scope, "Scope de M1\n", "Scope de M1 (evidencia: commit abc123)\n")
        self.replace(self.scope, "Scope de M2 original\n", "Scope de M2 original\u2028## M3\nExcepción común nueva\n")
        self.assert_stale("active_scope")


if __name__ == "__main__":
    unittest.main()
