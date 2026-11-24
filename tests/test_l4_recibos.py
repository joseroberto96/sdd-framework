"""5.3.1 L4.2 (opción A, D1): deltas declarados en el recibo de aprobación.

Casos reales reducidos a su forma: TL-FW-318 (el plan aprobado marca casillas
de la PRD), las ADR aditivas del propio módulo (19 de los 53 resellos de P3),
las specs clasificadas después de sellar y la descripción prevista en una
sección ajena del modelo, de los contratos o de la API. Lo declarado en
`covers_changes` no caduca el recibo; lo que cambia de fondo sin declararse
sigue caducando, con un caso por dimensión (D18). Un recibo sellado antes de L4
sigue valiendo.
"""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import replay_state_log  # noqa: E402
import workflow_receipts as wr  # noqa: E402
import test_workflow_receipts as twr  # noqa: E402

T0 = "2026-10-21T12:00:00+00:00"
FIXTURE_PRE_L4 = ROOT / "tests" / "fixtures" / "l4" / "recibo_pre_l4"

PRD = (
    "# PRD\n\n## MVP\n"
    "- [ ] **M0 — núcleo**: tipos canónicos\n"
    "- [ ] **M2 — informes**: resúmenes\n\n"
    "## Extensión solicitada\nTexto común de producto\n"
)
SCOPE = "# Scope\n\n## M1\nScope de M1\nSuperficie: src/m1.py\n\n## M2\nScope de M2 original\n"
ACC = (
    "# Aceptación\n\n## Módulo 1 — Uno (`M1`)\n- [ ] AC-01 de M1\n- [ ] AC-02 de M1\n\n"
    "## Módulo 2 — Dos (`M2`)\n- [ ] AC de M2 original\n"
)
DATA_MODEL = "# Modelo\n\n## M1 — entidades\nTabla a: id, valor\n\n## M2 — entidades\nTabla b: id, nombre\n"
CONTRACTS = "# Contratos\n\n## M1 — contrato\nf() devuelve int\n\n## M2 — contrato\ng() devuelve str\n"
API = "paths:\n  /m1:\n    get:\n      description: lee m1\n  /m2:\n    get:\n      description: lee m2\n"

# Todo lo declarable sin cubrir ninguno de los cambios de fondo de D18.
DECLARADO_SIN_CUBRIR = [
    "decisions:aditivas", "module_spec:clasificar", "prd:## MVP",
    "data_model:## M2 — entidades", "contracts:## M2 — contrato", "api_contract:/m2",
]


class Proyecto:
    def __init__(self, case: unittest.TestCase) -> None:
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        p = self.root / "_project"
        (p / "02_technical_plan").mkdir(parents=True)
        for rel, text in {
            "01_product_requirements.md": PRD, "02_technical_plan/phase_01.md": "# Fase 1\nM1\n",
            "02_technical_plan/data_model.md": DATA_MODEL, "02_technical_plan/api_contract.yaml": API,
            "03_contracts.md": CONTRACTS, "04_active_scope.md": SCOPE, "08_module_acceptance.md": ACC,
        }.items():
            (p / rel).write_text(text, encoding="utf-8")
        self.state = {
            "current_phase": "phase_01",
            "dependencies": [],
            "modules": {"in_progress": ["M1"], "pending": ["M2"], "completed": []},
            "work_tiers": {"M1": {"tier": "tier_2", "rationale": "alto impacto"},
                           "M2": {"tier": "tier_1", "rationale": "x"}},
            "module_specs": {"M1": {"data_model": "required", "api_contract": "n/a", "justification": "sin API"}},
            "_decisions": [{"id": "ADR-001", "module_id": "M1", "decision": "usar cola"},
                           {"id": "ADR-050", "module_id": "M2", "decision": "informes en HTML"}],
        }

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.root / "_project" / rel
        text = path.read_text(encoding="utf-8")
        assert old in text, (rel, old)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def approve(self, covers=None) -> dict:
        kw = {} if covers is None else {"covers_changes": covers}
        return wr.record_approval(
            self.state, self.root, "M1", approved_by="builder", evidence="Sí, adelante con el plan",
            risk_summary="riesgo acotado", adr_refs=["ADR-001"], now_iso=T0, **kw,
        )

    def receipt(self) -> dict:
        return self.state["workflow_receipts"]["approvals"]["M1"]

    def problems(self) -> list[str]:
        return list(wr.approval_problems(self.root, self.state, "M1"))

    def warnings(self) -> list[str]:
        return wr.approval_warnings(self.root, self.state, "M1")


class CasosRealesTests(unittest.TestCase):
    def setUp(self):
        self.p = Proyecto(self)

    def test_tl_fw_318_marcar_la_prd_declarada_no_caduca(self):
        rec = self.p.approve(["prd:## MVP"])
        self.assertEqual(rec["covers_changes"], ["prd:## MVP"])
        self.p.edit("01_product_requirements.md", "- [ ] **M0 — núcleo**", "- [x] **M0 — núcleo**")
        self.assertEqual(self.p.problems(), [])

    def test_tl_fw_318_sin_declarar_sigue_caducando(self):
        self.p.approve()
        self.p.edit("01_product_requirements.md", "- [ ] **M0 — núcleo**", "- [x] **M0 — núcleo**")
        self.assertIn("prd", " ".join(self.p.problems()))

    def test_tl_fw_318_otra_seccion_de_la_prd_caduca(self):
        self.p.approve(["prd:## MVP"])
        self.p.edit("01_product_requirements.md", "Texto común de producto", "Texto común ampliado")
        self.assertIn("prd", " ".join(self.p.problems()))

    def test_una_seccion_nueva_con_el_mismo_encabezado_caduca(self):
        self.p.approve(["prd:## MVP"])
        self.p.edit("01_product_requirements.md", "## Extensión solicitada", "## MVP\n- [ ] M9 nuevo\n\n## Extensión solicitada")
        self.assertIn("prd", " ".join(self.p.problems()))

    def test_renombrar_el_encabezado_declarado_caduca(self):
        self.p.approve(["prd:## MVP"])
        self.p.edit("01_product_requirements.md", "## MVP", "## Producto mínimo")
        self.assertIn("prd", " ".join(self.p.problems()))

    def test_adr_aditiva_declarada_no_caduca_y_avisa(self):
        self.p.approve(["decisions:aditivas"])
        self.p.state["_decisions"].append(
            {"id": "ADR-002", "module_id": "M1", "decision": "fijar el umbral medido", "documents_decision": "ADR-001"})
        self.assertEqual(self.p.problems(), [])
        self.assertIn("ADR-002", " ".join(self.p.warnings()))

    def test_adr_aditiva_sin_declarar_pide_refresh(self):
        self.p.approve()
        self.p.state["_decisions"].append({"id": "ADR-002", "module_id": "M1", "decision": "otra"})
        self.assertIn("usa refresh_approval_receipt", " ".join(self.p.problems()))

    def test_adr_declarada_no_cubre_alterar_ni_sustituir_la_aprobada(self):
        for name, mutate in (
            ("alterar", lambda s: s["_decisions"][0].__setitem__("decision", "usar otra cola")),
            ("sustituir", lambda s: (s["_decisions"][0].__setitem__("superseded_by", "ADR-002"),
                                     s["_decisions"].append({"id": "ADR-002", "module_id": "M1", "decision": "nueva"}))),
            ("quitar", lambda s: s["_decisions"].pop(0)),
        ):
            with self.subTest(name):
                p = Proyecto(self)
                p.approve(["decisions:aditivas"])
                mutate(p.state)
                self.assertIn("decisions", " ".join(p.problems()))

    def test_adr_declarada_con_documents_decision_invalido_caduca(self):
        for name, cited in (("inexistente", "ADR-999"), ("de otro módulo", "ADR-050"), ("a sí misma", "ADR-002")):
            with self.subTest(name):
                p = Proyecto(self)
                p.approve(["decisions:aditivas"])
                p.state["_decisions"].append(
                    {"id": "ADR-002", "module_id": "M1", "decision": "x", "documents_decision": cited})
                self.assertIn("decisions", " ".join(p.problems()))

    def test_specs_clasificadas_tras_el_sello_declarado(self):
        del self.p.state["module_specs"]["M1"]
        self.p.approve(["module_spec:clasificar"])
        self.p.state["module_specs"]["M1"] = {"data_model": "n/a", "api_contract": "n/a", "justification": "sin esquema"}
        self.assertEqual(self.p.problems(), [])

    def test_specs_clasificadas_tras_el_sello_sin_declarar_caduca(self):
        del self.p.state["module_specs"]["M1"]
        self.p.approve()
        self.p.state["module_specs"]["M1"] = {"data_model": "n/a", "api_contract": "n/a", "justification": "sin esquema"}
        self.assertIn("module_spec", " ".join(self.p.problems()))

    def test_clasificar_no_cubre_cambiar_una_spec_ya_clasificada_al_sellar(self):
        self.p.approve(["module_spec:clasificar"])  # data_model required, api_contract n/a
        self.p.state["module_specs"]["M1"] = {"data_model": "required", "api_contract": "required"}
        self.assertIn("module_spec", " ".join(self.p.problems()))

    def test_secciones_declaradas_de_modelo_contratos_y_api(self):
        casos = (
            ("data_model", "02_technical_plan/data_model.md", "data_model:## M2 — entidades",
             ("Tabla b: id, nombre", "Tabla b: id, nombre_completo"), ("Tabla a: id, valor", "Tabla a: id"),
             ("## M2 — entidades", "## M2 — tablas")),
            ("contracts", "03_contracts.md", "contracts:## M2 — contrato",
             ("g() devuelve str", "g() devuelve bytes"), ("f() devuelve int", "f() devuelve float"),
             ("## M2 — contrato", "## M2 — firma")),
            ("api_contract", "02_technical_plan/api_contract.yaml", "api_contract:/m2",
             ("lee m2", "lee m2 paginado"), ("lee m1", "escribe m1"), ("  /m2:", "  /m2b:")),
        )
        for dim, rel, selector, dentro, fuera, renombrar in casos:
            with self.subTest(dim, cambio="dentro"):
                p = Proyecto(self)
                p.approve([selector])
                p.edit(rel, *dentro)
                self.assertEqual(p.problems(), [])
            for nombre, cambio in (("fuera", fuera), ("renombrar", renombrar)):
                with self.subTest(dim, cambio=nombre):
                    p = Proyecto(self)
                    p.approve([selector])
                    p.edit(rel, *cambio)
                    self.assertIn(dim, " ".join(p.problems()))


class ValidacionAlSellarTests(unittest.TestCase):
    def test_lo_que_no_se_puede_declarar_se_rechaza_con_el_contrato(self):
        for item in (
            "active_scope:## M2", "acceptance:## Módulo 2 — Dos (`M2`)", "tier:tier_1", "fase:phase_02",
            "decisions:todas", "module_spec:n/a", "prd:## No existe", "prd:### MVP", "prd:MVP",
            "api_contract:/no-existe", "api_contract:m1", "data_model:", "sin separador",
        ):
            with self.subTest(item):
                p = Proyecto(self)
                with self.assertRaises(ValueError) as cm:
                    p.approve([item])
                self.assertIn("covers_changes", str(cm.exception))
                self.assertIn("Contrato de record_approval_receipt", str(cm.exception))

    def test_un_encabezado_repetido_no_se_puede_declarar(self):
        p = Proyecto(self)
        p.edit("01_product_requirements.md", "## Extensión solicitada", "## MVP\notra\n\n## Extensión solicitada")
        with self.assertRaises(ValueError):
            p.approve(["prd:## MVP"])

    def test_no_es_lista_de_strings(self):
        for raw in ("prd:## MVP", [1], {"prd": "## MVP"}):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    Proyecto(self).approve(raw)

    def test_lista_vacia_es_lo_mismo_que_no_declarar(self):
        p = Proyecto(self)
        rec = p.approve([])
        self.assertNotIn("covers_changes", rec)
        sin = Proyecto(self)
        self.assertEqual(sin.approve()["fingerprint"], rec["fingerprint"])


class RevisionSecurityTests(unittest.TestCase):
    """Hallazgos 1, 3 y 4 de la revisión `security` de L4."""

    PRD_MVP = (
        "# PRD\n\n## MVP\n### Incluye\n- [ ] M0 núcleo\n### No incluye por ahora\n- exportar a PDF\n"
        "### Criterios de éxito\n- el núcleo responde en 1 s\n\n## Preguntas abiertas\n- ninguna\n"
    )

    def proyecto(self, prd=None, dm=None) -> Proyecto:
        p = Proyecto(self)
        if prd is not None:
            (p.root / "_project" / "01_product_requirements.md").write_text(prd, encoding="utf-8")
        if dm is not None:
            (p.root / "_project" / "02_technical_plan" / "data_model.md").write_text(dm, encoding="utf-8")
        return p

    def test_en_la_prd_declarada_solo_pasan_casillas_y_anotaciones_de_progreso(self):
        p = self.proyecto(self.PRD_MVP)
        p.approve(["prd:## MVP"])
        p.edit("01_product_requirements.md", "- [ ] M0 núcleo", "- [x] M0 núcleo — evidencia: cerrado en M0")
        self.assertEqual(p.problems(), [])
        self.assertIn("prd:## MVP", " ".join(p.warnings()))
        for nombre, cambio in (
            ("mover de alcance", ("- exportar a PDF\n### Criterios", "### Criterios")),
            ("reescribir criterios", ("en 1 s", "en 10 s")),
        ):
            with self.subTest(nombre):
                q = self.proyecto(self.PRD_MVP)
                q.approve(["prd:## MVP"])
                q.edit("01_product_requirements.md", *cambio)
                self.assertIn("prd", " ".join(q.problems()))

    def test_la_ultima_seccion_declarada_no_absorbe_lo_que_se_anade_al_final(self):
        p = self.proyecto(self.PRD_MVP)
        p.approve(["prd:## Preguntas abiertas"])
        p.edit("01_product_requirements.md", "- ninguna\n", "- ninguna\n### Requisito nuevo\n- pagar en línea\n")
        self.assertIn("prd", " ".join(p.problems()))

    def test_declarar_todo_un_documento_se_rechaza(self):
        p = self.proyecto(dm="# Modelo\nTabla a: id, valor\n")
        with self.assertRaises(ValueError) as cm:
            p.approve(["data_model:# Modelo"])
        self.assertIn("cubriría todo", str(cm.exception))

    def test_lo_declarado_en_modelo_contratos_y_api_no_caduca_pero_avisa(self):
        p = Proyecto(self)
        p.approve(["data_model:## M2 — entidades"])
        p.edit("02_technical_plan/data_model.md", "Tabla b: id, nombre", "Tabla b: id")
        self.assertEqual(p.problems(), [])
        self.assertIn("cambió lo declarado en covers_changes desde el sello: data_model:## M2 — entidades",
                      " ".join(p.warnings()))

    def test_los_digests_de_lo_declarado_van_sellados(self):
        p = Proyecto(self)
        p.approve(["data_model:## M2 — entidades"])
        p.receipt()["covers_digests"]["data_model:## M2 — entidades"] = "0" * 16
        self.assertNotEqual(p.problems(), [])

    def test_limites_de_seccion_como_en_commonmark(self):
        for nombre, dm in (
            ("setext", "# Modelo\n## M2 — entidades\nTabla b\nReglas de M1\n-------------\nTabla a: id, valor\n"),
            ("ATX con espacios", "# Modelo\n## M2 — entidades\nTabla b\n  ## M1 — entidades\nTabla a: id, valor\n"),
        ):
            with self.subTest(nombre):
                p = self.proyecto(dm=dm)
                p.approve(["data_model:## M2 — entidades"])
                p.edit("02_technical_plan/data_model.md", "Tabla a: id, valor", "Tabla a: id")
                self.assertIn("data_model", " ".join(p.problems()))

    def test_un_encabezado_dentro_de_un_bloque_de_codigo_no_se_puede_declarar(self):
        p = self.proyecto(dm="# Modelo\n```\n## Ejemplo\n```\nTabla a: id, valor\n\n## M2 — entidades\nTabla b\n")
        with self.assertRaises(ValueError):
            p.approve(["data_model:## Ejemplo"])

    def test_las_specs_selladas_van_en_la_huella(self):
        p = Proyecto(self)
        p.approve(["module_spec:clasificar"])
        p.receipt()["spec_applicability"] = {}
        p.state["module_specs"]["M1"] = {"data_model": "required", "api_contract": "required"}
        self.assertNotEqual(p.problems(), [])


class ManipulacionTests(unittest.TestCase):
    def test_anadir_quitar_o_cambiar_la_lista_tras_sellar_caduca(self):
        for name, covers, mutate in (
            ("añadir", None, lambda r: r.__setitem__("covers_changes", ["prd:## MVP"])),
            ("quitar", ["prd:## MVP"], lambda r: r.pop("covers_changes")),
            ("cambiar", ["prd:## MVP"], lambda r: r.__setitem__("covers_changes", ["prd:## Extensión solicitada"])),
            ("como texto", ["decisions:aditivas"], lambda r: r.__setitem__("covers_changes", "decisions:aditivas")),
        ):
            with self.subTest(name):
                p = Proyecto(self)
                p.approve(covers)
                mutate(p.receipt())
                self.assertNotEqual(p.problems(), [])

    def test_un_recibo_anterior_a_v3_no_gana_la_tolerancia(self):
        p = Proyecto(self)
        p.approve(["prd:## MVP"])
        p.receipt()["basis_version"] = 2
        p.edit("01_product_requirements.md", "- [ ] **M0 — núcleo**", "- [x] **M0 — núcleo**")
        self.assertNotEqual(p.problems(), [])


class RefrescoTests(unittest.TestCase):
    def test_el_refresco_conserva_la_lista_y_su_tolerancia(self):
        p = Proyecto(self)
        p.approve(["prd:## MVP"])
        p.state["_decisions"].append({"id": "ADR-002", "module_id": "M1", "decision": "otra"})
        rec = wr.refresh_derived_approval(
            p.state, p.root, "M1", parent_fingerprint=p.receipt()["fingerprint"],
            derived_from=["_decisions"], evidence="ADR-002 aditiva", now_iso=T0)
        self.assertEqual(rec["covers_changes"], ["prd:## MVP"])
        self.assertEqual(p.problems(), [])
        p.edit("01_product_requirements.md", "- [ ] **M0 — núcleo**", "- [x] **M0 — núcleo**")
        self.assertEqual(p.problems(), [])
        p.edit("01_product_requirements.md", "Texto común de producto", "Texto común ampliado")
        self.assertIn("prd", " ".join(p.problems()))

    def test_el_refresco_acepta_lo_declarado_y_guarda_la_clasificacion(self):
        p = Proyecto(self)
        del p.state["module_specs"]["M1"]
        p.approve(["module_spec:clasificar"])
        p.state["module_specs"]["M1"] = {"data_model": "n/a", "api_contract": "n/a", "justification": "sin esquema"}
        p.edit("08_module_acceptance.md", "- [ ] AC-02 de M1\n", "- [ ] AC-02 de M1\n- [ ] AC-03 de M1\n")
        wr.refresh_derived_approval(
            p.state, p.root, "M1", parent_fingerprint=p.receipt()["fingerprint"],
            derived_from=["08_module_acceptance.md"], evidence="AC-03 añadido", now_iso=T0)
        self.assertEqual(p.problems(), [])
        p.state["module_specs"]["M1"] = {"data_model": "required", "api_contract": "n/a", "justification": "x"}
        self.assertIn("module_spec", " ".join(p.problems()))


class UnaPorDimensionTests(unittest.TestCase):
    """D18: lo que cambia de fondo y no está declarado sigue caducando, en cada
    una de las doce dimensiones de la base, con `covers_changes` presente."""

    CAMBIOS = {
        "prd": lambda p: p.edit("01_product_requirements.md", "Texto común de producto", "Texto común ampliado"),
        "phase_plan": lambda p: p.edit("02_technical_plan/phase_01.md", "M1", "M1 y M3"),
        "active_scope": lambda p: p.edit("04_active_scope.md", "Superficie: src/m1.py", "Superficie: src/m1.py y src/m9.py"),
        "acceptance": lambda p: p.edit("08_module_acceptance.md", "- [ ] AC-02 de M1\n", ""),
        "data_model": lambda p: p.edit("02_technical_plan/data_model.md", "Tabla a: id, valor", "Tabla a: id"),
        "api_contract": lambda p: p.edit("02_technical_plan/api_contract.yaml", "lee m1", "escribe m1"),
        "contracts": lambda p: p.edit("03_contracts.md", "f() devuelve int", "f() devuelve float"),
        "decisions": lambda p: p.state["_decisions"][0].__setitem__("decision", "usar otra cola"),
        "tier": lambda p: p.state["work_tiers"].__setitem__("M1", {"tier": "tier_1", "rationale": "x"}),
        "module_spec": lambda p: p.state["module_specs"].__setitem__(
            "M1", {"data_model": "n/a", "api_contract": "n/a", "justification": "ya no"}),
        "current_phase": lambda p: p.state.__setitem__("current_phase", "phase_02"),
        "dependencies": lambda p: p.state.__setitem__("dependencies", ["requests==2.31.0"]),
    }

    def test_las_doce_dimensiones(self):
        self.assertEqual(set(self.CAMBIOS), set(wr.approval_basis(Proyecto(self).root, {}, "M1", basis_version=3)))
        for dim, cambio in self.CAMBIOS.items():
            for covers in (None, DECLARADO_SIN_CUBRIR):
                with self.subTest(dim, declarado=bool(covers)):
                    p = Proyecto(self)
                    p.approve(covers)
                    self.assertEqual(p.problems(), [])
                    cambio(p)
                    self.assertIn(dim, " ".join(p.problems()))


class ReciboAntiguoTests(unittest.TestCase):
    """Un recibo sellado con el código de antes de L4 (fixture generada con
    2e111b8, sin `covers_changes` y con los mapas de identidades inline)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "proyecto"
        shutil.copytree(FIXTURE_PRE_L4, self.root)
        self.state = json.loads((self.root / "_project" / "03_project_state.json").read_text("utf-8"))

    def test_sigue_valiendo(self):
        for module in ("M1", "M2"):
            with self.subTest(module):
                receipt = self.state["workflow_receipts"]["approvals"][module]
                self.assertNotIn("covers_changes", receipt)
                self.assertEqual(list(wr.approval_problems(self.root, self.state, module)), [])
                self.assertEqual(wr.approval_basis(self.root, self.state, module), receipt["basis"])

    def test_sigue_caducando_por_lo_de_siempre(self):
        (self.root / "_project" / "01_product_requirements.md").write_text("# PRD\nOtra cosa\n", encoding="utf-8")
        self.assertIn("prd", " ".join(wr.approval_problems(self.root, self.state, "M1")))


class MotorTests(unittest.TestCase):
    """Por el motor real: la clave llega al recibo y el replay lo reproduce."""

    def setUp(self):
        twr.PilotClosureSequenceTests.setUp(self)
        self.addCleanup(twr.PilotClosureSequenceTests.tearDown, self)

    def test_record_approval_receipt_con_covers_changes_y_replay(self):
        base_state = json.loads((self.root / "_project" / "03_project_state.json").read_text("utf-8"))
        prd = self.root / "_project" / "01_product_requirements.md"
        prd.write_text("# PRD\nRequisitos base\n\n## MVP\n- [ ] M1 núcleo\n", "utf-8")
        msg = self.manager.mutate("record_approval_receipt", {
            "module": "M1", "approved_by": "builder", "evidence": "aprobación de M1",
            "risk_summary": "bajo", "adr_refs": ["ADR-001"],
            "covers_changes": ["prd:## MVP", "decisions:aditivas"],
        })
        self.assertIn("covers_changes (no caduca): decisions:aditivas, prd:## MVP", msg)
        live = self.manager.state_data["workflow_receipts"]["approvals"]["M1"]
        self.assertEqual(live["covers_changes"], ["decisions:aditivas", "prd:## MVP"])
        prd.write_text("# PRD\nRequisitos base\n\n## MVP\n- [x] M1 núcleo — evidencia: cerrado\n", "utf-8")
        self.assertEqual(list(wr.approval_problems(self.root, self.manager.state_data, "M1")), [])
        stats = replay_state_log.replay(self.root / "_project" / "state_log.jsonl", self.root / "replayed.json",
                                        strict=False, base_state=copy.deepcopy(base_state))
        self.assertEqual(stats["errors"], [])
        self.assertEqual(stats["state"]["workflow_receipts"]["approvals"]["M1"], live)


if __name__ == "__main__":
    unittest.main()
