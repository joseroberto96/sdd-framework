"""Mecanismo de extensiones del motor de estado (ADR-052, raíz de PA FW-040).

Un proyecto puede añadir acciones custom en `.agent/extensions/state_actions.py`
— un archivo que los upgrades NUNCA tocan — en vez de parchear `state_core.py`
(parche que se perdía en cada upgrade y cuya única señal era un test del
producto, si existía).

Contrato:
- El archivo define `EXTENSION_ACTIONS: dict[str, callable(manager, payload, now_iso) -> str]`.
- Las built-in SIEMPRE ganan: una extensión no puede sombrear una acción del motor (error de carga).
- Archivo roto o contrato inválido → error claro, nunca silencio.
- La validación de schema post-mutación sigue aplicando (una extensión no puede corromper el estado).
- En modo template el archivo no debe existir (guard del doctor).
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core  # noqa: E402


BASE_STATE = {
    "dependencies": [],
    "modules": {"completed": [], "in_progress": [], "pending": []},
    "module_statuses": {},
}


def make_manager():
    m = state_core.StateManager.__new__(state_core.StateManager)
    m.state_data = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in BASE_STATE.items()}
    return m


class StateExtensionsTests(unittest.TestCase):
    def setUp(self):
        self._orig = state_core.EXTENSIONS_FILE_PATH
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ext_path = Path(self.tmp.name) / "state_actions.py"
        state_core.EXTENSIONS_FILE_PATH = self.ext_path
        self.addCleanup(setattr, state_core, "EXTENSIONS_FILE_PATH", self._orig)

    def test_extension_ejecuta_y_muta(self):
        self.ext_path.write_text(
            "def marcar(manager, payload, now_iso):\n"
            "    manager.state_data['dependencies'].append(payload['dep'])\n"
            "    return f\"dep {payload['dep']} registrada por extensión\"\n"
            "EXTENSION_ACTIONS = {'marcar_dep_custom': marcar}\n",
            encoding="utf-8",
        )
        m = make_manager()
        res = m._apply_event("marcar_dep_custom", {"dep": "polars"}, "2026-07-09T00:00:00+00:00")
        self.assertIn("polars", res)
        self.assertIn("polars", m.state_data["dependencies"])

    def test_extension_no_puede_sombrear_builtin(self):
        self.ext_path.write_text(
            "EXTENSION_ACTIONS = {'update_module_status': lambda m, p, t: 'no'}\n",
            encoding="utf-8",
        )
        m = make_manager()
        with self.assertRaises(ValueError) as ctx:
            m._apply_event("cualquier_cosa_desconocida", {}, "2026-07-09T00:00:00+00:00")
        self.assertIn("update_module_status", str(ctx.exception))

    def test_builtin_gana_aunque_extension_colisione(self):
        # Aunque el archivo colisione, una acción built-in ejecuta el motor,
        # nunca la extensión (el dispatch consulta extensiones solo al final).
        self.ext_path.write_text(
            "EXTENSION_ACTIONS = {'append_to_list_field': lambda m, p, t: 'secuestrada'}\n",
            encoding="utf-8",
        )
        m = make_manager()
        res = m._apply_event(
            "append_to_list_field",
            {"field": "dependencies", "value": "fastapi"},
            "2026-07-09T00:00:00+00:00",
        )
        self.assertNotIn("secuestrada", res)
        self.assertIn("fastapi", m.state_data["dependencies"])

    def test_archivo_roto_error_claro(self):
        self.ext_path.write_text("esto no es python válido ((", encoding="utf-8")
        m = make_manager()
        with self.assertRaises(ValueError) as ctx:
            m._apply_event("accion_inexistente", {}, "2026-07-09T00:00:00+00:00")
        self.assertIn("state_actions.py", str(ctx.exception))

    def test_contrato_invalido_error_claro(self):
        self.ext_path.write_text("EXTENSION_ACTIONS = ['no', 'es', 'dict']\n", encoding="utf-8")
        m = make_manager()
        with self.assertRaises(ValueError) as ctx:
            m._apply_event("accion_inexistente", {}, "2026-07-09T00:00:00+00:00")
        self.assertIn("EXTENSION_ACTIONS", str(ctx.exception))

    def test_sin_archivo_accion_desconocida_menciona_extensiones(self):
        m = make_manager()
        with self.assertRaises(ValueError) as ctx:
            m._apply_event("no_existe", {}, "2026-07-09T00:00:00+00:00")
        self.assertIn("Acción desconocida", str(ctx.exception))
        self.assertIn("extensions", str(ctx.exception))

    def test_resultado_no_string_error(self):
        self.ext_path.write_text(
            "EXTENSION_ACTIONS = {'mala': lambda m, p, t: 42}\n", encoding="utf-8"
        )
        m = make_manager()
        with self.assertRaises(ValueError):
            m._apply_event("mala", {}, "2026-07-09T00:00:00+00:00")


class DoctorTemplateGuardTests(unittest.TestCase):
    def test_doctor_valida_extensiones_en_template(self):
        text = (ROOT / ".agent" / "scripts" / "framework_doctor.py").read_text(encoding="utf-8")
        self.assertIn("validate_state_extensions_template_cleanliness", text)

    def test_actualizar_framework_preserva_extensiones(self):
        text = (ROOT / ".agent" / "workflows" / "actualizar-framework.md").read_text(encoding="utf-8")
        self.assertIn(".agent/extensions/", text)


if __name__ == "__main__":
    unittest.main()
