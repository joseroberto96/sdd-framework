import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "tests"))
from _dynamic_import import load_module_from_path  # noqa: E402


def _load_doctor():
    mod = load_module_from_path("framework_doctor", ROOT / ".agent" / "scripts" / "framework_doctor.py")
    mod.ROOT = ROOT
    return mod


class TestCodeGraphWiring(unittest.TestCase):
    def setUp(self):
        self.doctor = _load_doctor()

    def _base_spec(self) -> dict:
        return json.loads((ROOT / ".agent" / "scripts" / "doctor_wiring.json").read_text(encoding="utf-8"))

    # test_real_repo_passes se retiró: tests/test_wiring_spec.py::
    # test_all_declared_features_pass_on_real_repo ya itera TODAS las features
    # de doctor_wiring.json (incluida code_graph_adapter) contra el repo real
    # y exige errors == [] — cobertura redundante sin valor adicional.

    def test_wrong_pin_is_rejected(self):
        # _WIRING_SPEC_CACHE es la definición del spec (no archivos del repo),
        # así que mutarla en memoria no toca el árbol de trabajo.
        spec = self._base_spec()
        spec["features"]["code_graph_adapter"]["files"][1]["required_regex"][0]["pattern"] = (
            r"TOOL_VERSION_PIN\s*=\s*[\"']9\.9\.9[\"']"
        )
        self.doctor._WIRING_SPEC_CACHE = spec
        errors: list[str] = []
        self.doctor.validate_wiring_feature("code_graph_adapter", None, errors)
        self.assertTrue(any("pin verificado" in e for e in errors), errors)

    def test_manage_adr_reference_is_rejected(self):
        # A diferencia de test_wrong_pin_is_rejected, esto necesita que el
        # ARCHIVO code_graph.py contenga "manage_adr(" -- no se puede simular
        # solo mutando el spec en memoria. Nunca escribir sobre el repo real:
        # copiar a un árbol temporal y apuntar doctor.ROOT ahí.
        original_root = self.doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                self.doctor.ROOT = Path(tmp)
                adapters = Path(tmp) / ".agent" / "adapters"
                scripts = Path(tmp) / ".agent" / "scripts"
                adapters.mkdir(parents=True)
                scripts.mkdir(parents=True)
                real_adapter = (ROOT / ".agent" / "adapters" / "code_graph.md").read_text(encoding="utf-8")
                real_shim = (ROOT / ".agent" / "scripts" / "code_graph.py").read_text(encoding="utf-8")
                real_matrix = (ROOT / ".agent" / "adapters" / "_capability_matrix.md").read_text(encoding="utf-8")
                (adapters / "code_graph.md").write_text(real_adapter, encoding="utf-8")
                (adapters / "_capability_matrix.md").write_text(real_matrix, encoding="utf-8")
                (scripts / "code_graph.py").write_text(real_shim + "\nmanage_adr()\n", encoding="utf-8")

                errors: list[str] = []
                self.doctor.validate_wiring_feature("code_graph_adapter", None, errors)
                self.assertTrue(any("manage_adr" in e for e in errors), errors)
            finally:
                self.doctor.ROOT = original_root


if __name__ == "__main__":
    unittest.main()
