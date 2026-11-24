# -*- coding: utf-8 -*-
"""Gate de estructura del checkpoint de builder (3.19.0, FW-074).

Calibración: la plantilla completa pasa; la pregunta técnica cruda, la
analogía ausente/vacía, una sola opción y 4+ opciones bloquean.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
from validate_checkpoint import checkpoint_problems  # noqa: E402

GOOD = """Decisión: dónde guardan sus datos los usuarios de tu app.
Qué significa para ti: si la app pierde la conexión, ¿el usuario puede seguir trabajando o se queda bloqueado?
Analogía: es como elegir entre una libreta que llevas encima y un archivador en la oficina.
Recomendación: la libreta (guardar en el dispositivo) — tus usuarios trabajan sin señal y perder una anotación les cuesta dinero.
Opciones:
A) Guardar en el dispositivo — si eliges esto: funciona sin internet, pero sincronizar costará un módulo extra.
B) Guardar en la nube — si eliges esto: todo sincronizado, pero sin señal la app no sirve y pagas mensualidad.
"""


class TestCheckpointGate(unittest.TestCase):
    def test_full_template_passes(self):
        self.assertEqual(checkpoint_problems(GOOD), [])

    def test_crude_technical_question_blocks(self):
        problems = checkpoint_problems("¿Postgres o SQLite?")
        self.assertGreaterEqual(len(problems), 4, f"debe fallar por toda la estructura: {problems}")

    def test_missing_analogy_blocks(self):
        text = GOOD.replace("Analogía: es como elegir entre una libreta que llevas encima y un archivador en la oficina.\n", "")
        problems = checkpoint_problems(text)
        self.assertTrue(any("Analogía" in p for p in problems), problems)

    def test_empty_analogy_blocks(self):
        text = GOOD.replace(
            "Analogía: es como elegir entre una libreta que llevas encima y un archivador en la oficina.",
            "Analogía: —",
        )
        problems = checkpoint_problems(text)
        self.assertTrue(any("Analogía" in p and "contenido real" in p for p in problems), problems)

    def test_single_option_blocks(self):
        text = GOOD.replace("B) Guardar en la nube — si eliges esto: todo sincronizado, pero sin señal la app no sirve y pagas mensualidad.\n", "")
        problems = checkpoint_problems(text)
        self.assertTrue(any("faltan opciones" in p for p in problems), problems)

    def test_four_options_block(self):
        text = GOOD + "C) Un híbrido — si eliges esto: lo mejor de ambos pero el doble de tiempo de construcción.\n"
        text += "D) No guardar nada — si eliges esto: la app olvida todo al cerrarse.\n"
        problems = checkpoint_problems(text)
        self.assertTrue(any("demasiadas opciones" in p for p in problems), problems)

    def test_option_without_tradeoff_blocks(self):
        text = GOOD.replace(
            "B) Guardar en la nube — si eliges esto: todo sincronizado, pero sin señal la app no sirve y pagas mensualidad.",
            "B) Guardar en la nube",
        )
        problems = checkpoint_problems(text)
        self.assertTrue(any("sin trade-off" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
