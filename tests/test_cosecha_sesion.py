"""La cosecha de sesión: dónde se engancha, qué no bloquea y qué sigue pidiendo tu sí."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCTOR_PATH = ROOT / ".agent" / "scripts" / "framework_doctor.py"


def load_doctor():
    spec = importlib.util.spec_from_file_location("framework_doctor_cosecha", DOCTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CosechaSesionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doctor = load_doctor()

    def test_el_cableado_completo_esta_presente(self):
        errors = []
        self.doctor.validate_cosecha_sesion_wiring(errors)
        self.assertEqual(errors, [], errors)

    def test_la_cosecha_vive_en_cierre_modulo_y_no_en_gycd(self):
        """La transcripción entera está en `/cierre-modulo` (paso 5.5); `/gycd`
        solo CONSOLIDA el búfer al cierre de sesión — llega cuando el contexto
        suele estar compactado y cosechar sobre un resumen inventa."""
        cierre = (ROOT / ".agent/workflows/cierre-modulo.md").read_text(encoding="utf-8")
        gycd = (ROOT / ".agent/workflows/gycd.md").read_text(encoding="utf-8")
        self.assertIn("COSECHA DE FRICCIÓN DE SESIÓN", cierre)
        self.assertIn("Consolidar el búfer de fricción", gycd)
        self.assertNotIn("COSECHA DE FRICCIÓN DE SESIÓN", gycd)
        self.assertIn("no cosechar", gycd)

    def test_la_cosecha_exige_declarar_no_encontrar(self):
        """FW-201: el HARVEST GATE (`record_harvest` + `closure_gate`) bloquea
        el SILENCIO (no llamar a `record_harvest`), nunca el CONTENIDO de la
        cosecha — un gate que rechazara cierres por lo que encuentra sería la
        ceremonia que el builder ya registró como fricción (FW-109/110/111).
        `classes: "none"` satisface el gate igual que una lista de hallazgos."""
        cierre = (ROOT / ".agent/workflows/cierre-modulo.md").read_text(encoding="utf-8")
        self.assertIn("El gate exige DECLARAR, no ENCONTRAR", cierre)
        self.assertIn("record_harvest", cierre)
        self.assertIn('classes: "none"', cierre)

    def test_las_lecciones_siguen_exigiendo_aprobacion_humana(self):
        """`22_lessons.md` son reglas permanentes que el agente se aplica a sí
        mismo: escribirlas sin supervisión es deriva silenciosa."""
        cierre = (ROOT / ".agent/workflows/cierre-modulo.md").read_text(encoding="utf-8")
        self.assertIn("proponerla, no escribirla", cierre)
        self.assertIn("/aprender-de-error", cierre)

    def test_el_bufer_declara_umbral_y_criterio_de_muerte(self):
        """Acumular sin umbral no lleva a ninguna parte, y un búfer del que nunca
        se promueve nada es un diario."""
        buf = (ROOT / ".agent/docs/friction_buffer.md").read_text(encoding="utf-8")
        self.assertIn("Umbral de promoción", buf)
        self.assertIn("Criterio de muerte", buf)
        self.assertIn("Slugs en uso", buf)

    def test_gycd_declara_el_hueco_de_las_sesiones_sin_modulo(self):
        """Una sesión que no cierra módulos nunca pasa por 8.5: el hueco se
        declara y se ofrece, no se tapa cosechando sobre contexto compactado."""
        gycd = (ROOT / ".agent/workflows/gycd.md").read_text(encoding="utf-8")
        self.assertIn("no cerró ningún módulo", gycd)
        self.assertIn("suelo, no un techo", gycd)


if __name__ == "__main__":
    unittest.main()
