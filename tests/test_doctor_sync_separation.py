"""Patrón B (cosecha 2026-07: Example CLI FW-001, PA FW-038, TL FW-004):
el doctor debe separar la salud de sincronización de menú/wrappers (drift
regenerable con sync_claude_commands.py) de la salud del producto/plantilla,
para que un gate de cierre no bloquee por causas ajenas al módulo.

Contrato:
- Los hallazgos de wrappers/menú se marcan con el prefijo [SYNC].
- partition_errors separa ambos grupos.
- Solo-errores-SYNC → exit code 2 (regenerable); errores de producto → 1.
- El namespace interno del doctor es DOCTOR-SYNC-NNN (no FW-NNN, que colisiona
  con los ledgers de los proyectos).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCTOR_PATH = ROOT / ".agent" / "scripts" / "framework_doctor.py"

sys.path.insert(0, str(ROOT / "tests"))
from _dynamic_import import load_module_from_path  # noqa: E402


class TestSyncSeparation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doctor = load_module_from_path("framework_doctor_sync", DOCTOR_PATH)

    def test_sync_fail_marca_con_prefijo(self):
        errors: list[str] = []
        self.doctor.sync_fail(errors, "Wrapper OpenCode faltante: x.md")
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith(self.doctor.SYNC_ERROR_PREFIX))

    def test_partition_separa_sync_de_producto(self):
        errors: list[str] = []
        self.doctor.fail(errors, "README.md no declara Framework v4")
        self.doctor.sync_fail(errors, "Menu bloat en Codex: a, b")
        producto, sync = self.doctor.partition_errors(errors)
        self.assertEqual(len(producto), 1)
        self.assertEqual(len(sync), 1)
        self.assertIn("README", producto[0])
        self.assertIn("Menu bloat", sync[0])

    def test_wrappers_usan_sync_fail(self):
        # Los emisores de drift de wrappers/menú deben usar sync_fail, no fail.
        text = DOCTOR_PATH.read_text(encoding="utf-8")
        for needle in (
            'sync_fail(errors, f"Wrapper Claude faltante',
            'sync_fail(errors, f"Wrapper OpenCode faltante',
            'sync_fail(errors, f"Wrapper Codex faltante',
            'sync_fail(errors, f"Wrapper Codex generado pero no declarado',
        ):
            self.assertIn(needle, text, needle)
        # Menu bloat también se emite vía sync_fail (call multilínea)
        idx = text.index('f"Menu bloat en {label}')
        self.assertIn("sync_fail(", text[idx - 120:idx])

    def test_namespace_doctor_sync_sin_colision_fw(self):
        text = DOCTOR_PATH.read_text(encoding="utf-8")
        self.assertNotIn("FW-001 BF", text)
        self.assertIn("DOCTOR-SYNC-001", text)


if __name__ == "__main__":
    unittest.main()
