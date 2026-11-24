"""Los gates del cierre MUERDEN — verificación de comportamiento, no de prosa.

Un grep prueba que el string existe. Esto prueba que el gate muerde: para cada
uno, se prepara un módulo cerrable, se rompe UNA cosa, y se exige que
`complete_module` bloquee nombrando esa razón. Si algún gate estuviera
desconectado, el cierre pasaría igual y aquí se vería.
"""
import json
import sys
import unittest
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core
from state_core import StateManager

BASE_STATE = {
    "project": "AuditoriaCableado",
    "framework_mode": "template",
    "framework_version": state_core.FRAMEWORK_VERSION,
    "current_phase": "phase_01",
    "current_step": "init",
    "last_completed_step": "",
    "stack": {"backend": "Python", "frontend": "-", "database": "-", "auth": "-"},
    "dependencies": [],
    "architecture_constraints": [],
    "modules": {"completed": [], "in_progress": ["m1"], "pending": []},
    "module_statuses": {"m1": "in_progress"},
    "work_tiers": {}, "module_specs": {}, "closure_rejections": [],
    "active_mcps": [], "contracts": {"public_interfaces": {}},
    "_refs": {}, "_bugs": [], "_clarifications": [], "_decisions": [],
}


def montar(tmp: Path) -> StateManager:
    (tmp / "_project").mkdir(parents=True, exist_ok=True)
    (tmp / "tests").mkdir(exist_ok=True)
    (tmp / "tests" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    for attr, rel in (
        ("STATE_FILE_PATH", "_project/03_project_state.json"),
        ("CORE_FILE_PATH", "_project/core.md"),
        ("BUGS_FILE_PATH", "_project/06_bug_fixes.md"),
        ("CLARIFICATIONS_FILE_PATH", "_project/07_pending_clarifications.md"),
        ("DECISIONS_FILE_PATH", "_project/03_arch_decisions.md"),
        ("SESSION_BRIEF_PATH", "_project/00_session_brief.json"),
    ):
        setattr(state_core, attr, tmp / rel)
    state_core.WORKSPACE_ROOT = tmp
    (tmp / "_project" / "03_project_state.json").write_text(
        json.dumps(BASE_STATE, ensure_ascii=False), encoding="utf-8")
    return StateManager()


ACS_OK = (
    "# ACs\n\n## m1\n\nEstado: ✅ Verificado con cliente\n\n"
    "- [x] AC1 pasa — evidencia: `tests/test_x.py` en verde (1 caso)\n"
)


def preparar(mgr, tmp, *, sello=True, receipt=True, spec_int="n/a", stub=False, harvest=True):
    mgr.mutate("update_module_status", {"id": "m1", "status": "ready_for_validation"})
    mgr.mutate("add_work_tier", {"id": "m1", "tier": "tier_1",
                                 "rationale": "auditoría", "classified_by": "manual"})
    # FW-201: HARVEST GATE exige cosecha declarada antes del cierre.
    if harvest:
        mgr.mutate("record_harvest", {"module_id": "m1", "classes": "none"})
    spec = {"id": "m1", "data_model": "n/a", "api_contract": "n/a",
            "justification": "módulo de auditoría", "classified_by": "manual"}
    if spec_int is not None:
        spec["integration_real"] = spec_int
    mgr.mutate("classify_module_spec", spec)
    acs = ACS_OK if sello else ACS_OK.replace("Estado: ✅ Verificado con cliente\n\n", "")
    (tmp / "_project" / "08_module_acceptance.md").write_text(acs, encoding="utf-8")
    # El codigo se escribe ANTES del receipt: el receipt fija el diff_hash y
    # cualquier cambio posterior lo deja stale (gate distinto, correcto pero
    # no el que se quiere medir aqui).
    if stub:
        src = tmp / "src"; src.mkdir(exist_ok=True)
        (src / "svc.py").write_text(
            "def c():\n    return {'email': 'x@example.com'}\n", encoding="utf-8")
        # citarlo como evidencia para que el STUB GATE lo alcance
        (tmp / "_project" / "08_module_acceptance.md").write_text(
            acs.replace("`tests/test_x.py`", "`src/svc.py` y `tests/test_x.py`"), encoding="utf-8")
    if receipt:
        mgr.mutate("record_execution_receipt", {
            "module": "m1", "kind": "no_aplica", "command": "",
            "evidence": "módulo de auditoría sin superficie ejecutable",
        })


@contextmanager
def entorno_temporal():
    """Aísla y restaura las rutas globales que usa el fixture de cierre."""
    attrs = (
        "WORKSPACE_ROOT", "STATE_FILE_PATH", "CORE_FILE_PATH",
        "BUGS_FILE_PATH", "CLARIFICATIONS_FILE_PATH", "DECISIONS_FILE_PATH",
        "SESSION_BRIEF_PATH",
    )
    original = {name: getattr(state_core, name) for name in attrs}
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            yield tmp, montar(tmp)
    finally:
        for name, value in original.items():
            setattr(state_core, name, value)


def intentar(preparador) -> str:
    """Devuelve '' si cerró, o el mensaje de bloqueo."""
    with entorno_temporal() as (tmp, mgr):
        preparador(mgr, tmp)
        try:
            mgr.mutate("complete_module", {"id": "m1"})
            return ""
        except ValueError as exc:
            return str(exc)



class GatesDeCierreMuerdenTests(unittest.TestCase):
    """Cada gate se comprueba rompiendo UNA cosa y exigiendo que bloquee.

    FW-156 salió de aquí: `stub_problems` recibía `root` pero leía la aceptación
    de una constante global del módulo. En un proyecto adoptado coincidía por
    casualidad, así que el fallo era invisible — y en cualquier otro contexto el
    STUB GATE devolvía OK sin escanear nada. Lo encontró esta comprobación, no un
    grep: el grep decía que la llamada estaba ahí, y estaba.
    """

    def test_control_todo_correcto_cierra(self):
        self.assertEqual(intentar(lambda m, t: preparar(m, t)), "")

    def test_fw144_sin_execution_receipt_bloquea(self):
        self.assertIn("execution receipt ausente",
                      intentar(lambda m, t: preparar(m, t, receipt=False)))

    def test_fw145_dato_fabricado_en_codigo_citado_bloquea(self):
        self.assertIn("STUB GATE", intentar(lambda m, t: preparar(m, t, stub=True)))

    def test_fw147_sin_sello_del_builder_bloquea(self):
        self.assertIn("Verificado por el agente",
                      intentar(lambda m, t: preparar(m, t, sello=False)))

    def test_fw148_integration_real_required_bloquea(self):
        self.assertIn("integration_real",
                      intentar(lambda m, t: preparar(m, t, spec_int="required")))

    def test_fw201_sin_cosecha_declarada_bloquea(self):
        """HARVEST GATE (FW-201): bloquea el SILENCIO — no llamar a
        record_harvest — nunca lo que la cosecha encuentre. classes: "none" ya
        satisface el gate en `preparar()`; aquí se desactiva para ejercer el
        bloqueo mismo."""
        self.assertIn("no tiene cosecha de fricción declarada",
                      intentar(lambda m, t: preparar(m, t, harvest=False)))

    def test_fw201_cosecha_none_satisface_el_gate(self):
        """"none" es una declaración legítima, no una omisión — no debe
        aparecer entre las razones de bloqueo cuando todo lo demás está OK."""
        self.assertEqual(intentar(lambda m, t: preparar(m, t)), "")

    def test_fw201_cosecha_con_hallazgos_tambien_satisface_el_gate(self):
        """El gate exige declarar, no encontrar: una lista de slugs cierra
        igual que "none"."""
        def _preparar_con_hallazgos(mgr, tmp):
            preparar(mgr, tmp, harvest=False)
            mgr.mutate("record_harvest", {"module_id": "m1", "classes": ["clase-x"]})
        self.assertEqual(intentar(_preparar_con_hallazgos), "")

    def test_fw201_classes_vacia_o_invalida_no_es_declaracion_valida(self):
        """Una lista vacía o cualquier otra forma no es 'nada que declarar':
        es un error de uso, no una declaración de cero hallazgos."""
        with entorno_temporal() as (tmp, mgr):
            preparar(mgr, tmp, harvest=False)
            with self.assertRaises(ValueError):
                mgr.mutate("record_harvest", {"module_id": "m1", "classes": []})
            with self.assertRaises(ValueError):
                mgr.mutate("record_harvest", {"module_id": "m1", "classes": "algo"})

    def test_fw149_el_cierre_sella_quien_cerro(self):
        with entorno_temporal() as (tmp, mgr):
            mgr.mutate("set_execution_profile",
                       {"capability_tier": "strong", "model": "modelo-de-prueba"})
            preparar(mgr, tmp)
            mgr.mutate("complete_module", {"id": "m1"})
            sello = mgr.state_data["work_tiers"]["m1"].get("closed_by", {})
        self.assertEqual(sello.get("model"), "modelo-de-prueba")

    def test_fw151_ejecutor_limited_puede_satisfacer_su_gate(self):
        with entorno_temporal() as (tmp, mgr):
            mgr.mutate("set_execution_profile", {"capability_tier": "limited"})
            mgr.mutate("add_work_tier", {"id": "m1", "tier": "tier_1",
                                         "rationale": "r", "classified_by": "manual"})
            marca = mgr.state_data["work_tiers"]["m1"].get("requires_second_review")
        self.assertIs(marca, True)


if __name__ == "__main__":
    unittest.main()
