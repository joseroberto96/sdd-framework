#!/usr/bin/env python3
"""Regresiones del validador único del cierre de turno (puro, sin E/S)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import turn_close as tc  # noqa: E402

BUILDER = ["arrancar", "reconciliar", "gycd", "diseñar", "nuevo-bug", "nuevo-proyecto",
           "migrar-proyecto", "actualizar-framework", "ciclo"]
MANIFEST = {
    "commands": [{"name": n, "builder_facing": True} for n in BUILDER]
    + [{"name": "tdd", "builder_facing": False}, {"name": "cierre-modulo", "builder_facing": False}]
}
MODEL = "Modelo: standard · Esfuerzo: 🟡 medio (tier_1)"


def codes(text: str, mode: str = "live") -> list[str]:
    return [d.code for d in tc.validate_turn_close(text, manifest=MANIFEST, mode=mode)]


def close(line: str, model: str | None = MODEL, before: str = "Resumen del turno.") -> str:
    parts = [before, "", line]
    if model:
        parts.append(model)
    return "\n".join(parts)


class ConstantsTests(unittest.TestCase):
    def test_twelve_errors_and_seven_warnings(self) -> None:
        # 5.3.1 L5.1 (FW-360): PROHIBICIONES e INSTRUCCION_LARGA; L5.2 (FW-362): PALABRAS_PEGADAS.
        # 5.3.2 (FW-402): PROHIBICIONES e INSTRUCCION_LARGA pasan de error a aviso.
        self.assertEqual(len(tc.ERROR_CODES), 12)
        self.assertEqual(len(tc.WARNING_CODES), 7)
        self.assertEqual(len(set(tc.ALL_CODES)), 19)
        self.assertTrue({tc.PROHIBICIONES, tc.INSTRUCCION_LARGA} <= set(tc.WARNING_CODES))

    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            tc.validate_turn_close("x", manifest=MANIFEST, mode="otro")

    def test_defect_is_immutable_and_has_actionable_fix(self) -> None:
        defect = tc.validate_turn_close("sin cierre", manifest=MANIFEST)[0]
        self.assertEqual((defect.code, defect.severity, defect.line), ("SIN_SIGUIENTE", "error", 0))
        self.assertTrue(defect.fix)
        with self.assertRaises(Exception):
            defect.code = "otro"  # type: ignore[misc]


class ValidFormsTests(unittest.TestCase):
    def test_form_command_with_model_line(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo Implementar el motor en src/motor.py")), [])

    def test_form_ciclo_fase_and_parallel(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo fase")), [])
        self.assertEqual(codes(close("Siguiente: /ciclo fase --paralelo")), [])

    def test_every_builder_command_with_accents(self) -> None:
        for name in BUILDER:
            self.assertEqual(codes(close(f"Siguiente: /{name} Hacer algo concreto")), [], name)

    def test_form_ninguno_and_separators(self) -> None:
        self.assertEqual(codes(close("Siguiente: ninguno — bloqueo por aprobación", model=None)), [])
        self.assertEqual(codes(close("Siguiente: ninguno – bloqueo por aprobación", model=None)), [])
        self.assertEqual(codes(close("Siguiente: ninguno - bloqueo por aprobación", model=None)), [])

    def test_form_decision_valid(self) -> None:
        self.assertEqual(codes(close("Siguiente: decisión — responde A (recomendada) o B en este chat", model=None)), [])

    def test_markdown_adornments_are_tolerated_not_defects(self) -> None:
        self.assertEqual(codes(close("**Siguiente:** /ciclo Implementar el motor")), [])
        self.assertEqual(codes(close("> Siguiente: /ciclo Implementar el motor", model="> " + MODEL)), [])
        self.assertEqual(codes(close("**Siguiente: /ciclo Implementar el motor**")), [])
        parts = tc.extract_close_parts(close("**Siguiente:** /ciclo Hacer algo"), manifest=MANIFEST)
        self.assertEqual(parts.adornos, ("negrita",))

    def test_trailing_blank_lines_and_spaces_are_ignored(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo Hacer algo") + "   \n\n\n"), [])

    def test_model_variants(self) -> None:
        for model in (
            "Modelo: strong (Opus 5.5) · Esfuerzo: 🟠 alto (tier_1)",
            "Modelo: limited · Esfuerzo: 🟢 bajo (tier_0)",
            "Modelo: standard · Esfuerzo: medio (tier_1)",
            "Modelo: strong · Esfuerzo: 🔴 xhigh (tier_2)",
            "**Modelo: standard · Esfuerzo: 🟡 medio (tier_1)**",
        ):
            self.assertEqual(codes(close("Siguiente: /ciclo Hacer algo", model=model)), [], model)

    def test_is_valid_ignores_warnings(self) -> None:
        defects = tc.validate_turn_close(close("Siguiente: /ciclo Hacer algo", model=None), manifest=MANIFEST)
        self.assertEqual([d.code for d in defects], ["SIN_MODELO"])
        self.assertTrue(tc.is_valid(defects))
        self.assertFalse(tc.is_valid(tc.validate_turn_close("nada", manifest=MANIFEST)))


class ErrorCodesTests(unittest.TestCase):
    def test_sin_siguiente(self) -> None:
        self.assertEqual(codes("Solo texto, sin cierre."), ["SIN_SIGUIENTE"])
        self.assertEqual(codes(""), ["SIN_SIGUIENTE"])

    def test_formato_heredado(self) -> None:
        text = "Hecho.\nSiguiente comando: /ciclo algo\nPrompt sugerido: continúa"
        self.assertEqual(codes(text), ["FORMATO_HEREDADO", "SIN_SIGUIENTE"])
        # Con un cierre actual, el heredado se señala igualmente
        self.assertIn("FORMATO_HEREDADO", codes("Prompt sugerido: x\n" + close("Siguiente: /ciclo Hacer algo")))

    def test_varias_lineas_siguiente(self) -> None:
        text = "Siguiente: /ciclo Primero\n\n" + close("Siguiente: /ciclo Segundo")
        self.assertEqual(codes(text), ["VARIAS_LINEAS_SIGUIENTE"])

    def test_en_bloque_de_codigo_is_not_sin_siguiente(self) -> None:
        for fence in ("```", "~~~", "````"):
            text = f"Hecho.\n{fence}\nSiguiente: /ciclo Hacer algo\n{fence}"
            self.assertEqual(codes(text), ["EN_BLOQUE_DE_CODIGO"], fence)

    def test_block_example_plus_real_close_is_valid(self) -> None:
        text = "Ejemplo:\n```\nSiguiente: /ciclo ejemplo\n```\n" + close("Siguiente: /ciclo Hacer algo")
        self.assertEqual(codes(text), [])

    def test_comillas_en_comando(self) -> None:
        self.assertEqual(codes(close("Siguiente: `/ciclo Hacer algo concreto`")), ["COMILLAS_EN_COMANDO"])
        self.assertEqual(codes(close("Siguiente: `/ciclo` Hacer algo concreto")), ["COMILLAS_EN_COMANDO"])
        self.assertEqual(codes(close("`Siguiente: /ciclo Hacer algo concreto`")), ["COMILLAS_EN_COMANDO"])

    def test_code_spans_inside_instruction_are_fine(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo Revisar `src/a.py` y `src/b.py`")), [])

    def test_comando_interno(self) -> None:
        self.assertEqual(codes(close("Siguiente: /tdd Implementar con prueba primero")), ["COMANDO_INTERNO"])

    def test_comando_desconocido(self) -> None:
        self.assertEqual(codes(close("Siguiente: /inventado Hacer algo")), ["COMANDO_DESCONOCIDO"])

    def test_sin_comando(self) -> None:
        self.assertEqual(codes(close("Siguiente: implementar el motor")), ["SIN_COMANDO"])
        self.assertIn("SIN_COMANDO", codes(close("Siguiente: ninguno bloqueado sin separador")))

    def test_marcador_live(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo Implementar <módulo>")), ["MARCADOR"])
        self.assertEqual(codes(close("Siguiente: /ciclo Implementar [módulo]")), ["MARCADOR"])
        self.assertEqual(codes(close("Siguiente: /<comando> Hacer algo")), ["COMANDO_DESCONOCIDO", "MARCADOR"])

    def test_marker_false_positives_are_avoided(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo Ver [la guía](docs/g.md) y si x <= 3 entonces y > 2")), [])
        self.assertEqual(codes(close("Siguiente: /ciclo Tipar `list[int]` en src/a.py")), [])

    def test_brackets_attached_to_identifier_are_not_markers(self) -> None:
        for instr in ("arreglar items[0] en parser.py", "tipar Dict<str> en api.ts", "cambiar la firma a list[int]"):
            self.assertEqual(codes(close(f"Siguiente: /ciclo {instr}")), [], instr)
            self.assertEqual(codes(f"Siguiente: /ciclo {instr}", "template"), [], instr)

    def test_unattached_brackets_remain_markers(self) -> None:
        for instr in ("revisar [A/B/C]", "usar [comando activo]", "abrir <zona que declares>",
                      "implementar [módulo]", "[A/B] primero", "tipar x_1 [int] y y<z>"):
            self.assertEqual(codes(close(f"Siguiente: /ciclo {instr}")), ["MARCADOR"], instr)
        self.assertEqual(codes(close("Siguiente: /<comando> Hacer algo")), ["COMANDO_DESCONOCIDO", "MARCADOR"])

    def test_instruccion_vacia(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo")), ["INSTRUCCION_VACIA"])
        self.assertEqual(codes(close("Siguiente: /arrancar")), ["INSTRUCCION_VACIA"])

    def test_no_es_lo_ultimo(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo Hacer algo") + "\nPD: una cosa más."), ["NO_ES_LO_ULTIMO"])
        self.assertEqual(codes("Siguiente: /ciclo Hacer algo\nTexto inmediato después", ), ["NO_ES_LO_ULTIMO", "SIN_MODELO"])

    def test_modelo_mal_formado(self) -> None:
        for model in (
            "Modelo: gigante · Esfuerzo: 🟡 medio (tier_1)",
            "Modelo: standard · Esfuerzo: 🟡 medio",
            "Modelo: standard · Esfuerzo: 🟡 extremo (tier_1)",
            "Modelo: standard · Esfuerzo: 🟡 medio (tier_9)",
            "Modelo: standard · Esfuerzo: 🟡 medio (tier_1). Conviene sesión nueva.",
        ):
            self.assertEqual(codes(close("Siguiente: /ciclo Hacer algo", model=model)), ["MODELO_MAL_FORMADO"], model)


class WarningCodesTests(unittest.TestCase):
    def test_instruccion_vaga_closed_list(self) -> None:
        for instr in ("continuar", "Continuar.", "cuando quieras", "o el comando que aplique",
                      "retomar", "continuar o el comando que aplique"):
            self.assertEqual(codes(close(f"Siguiente: /ciclo {instr}")), ["INSTRUCCION_VAGA"], instr)
        self.assertEqual(codes(close("Siguiente: /arrancar Retomar con el estado del módulo F3")), [])
        self.assertEqual(codes(close("Siguiente: /ciclo Continuar con la tarea T3 del módulo de reportes")), [])

    def test_demasiado_larga(self) -> None:
        long_line = "Siguiente: /ciclo " + "x" * 400
        self.assertEqual(codes(close(long_line)), ["DEMASIADO_LARGA"])
        self.assertEqual(codes(close("Siguiente: /ciclo " + "x" * 380)), [])

    def test_sin_modelo(self) -> None:
        self.assertEqual(codes(close("Siguiente: /ciclo Hacer algo", model=None)), ["SIN_MODELO"])
        self.assertEqual(codes(close("Siguiente: /ciclo fase", model=None)), ["SIN_MODELO"])

    def test_decision_en_ciclo(self) -> None:
        for instr in ("Aprobar el plan", "aprueba el plan", "Decidir entre A y B", "Confirma el alcance",
                      "Responder las dudas", "Elegir opción", "Esperar la revisión", "Elige la ruta"):
            self.assertEqual(codes(close(f"Siguiente: /ciclo {instr}")), ["DECISION_EN_CICLO"], instr)
        self.assertEqual(codes(close("Siguiente: /arrancar Aprobar el plan")), [])
        self.assertEqual(codes(close("Siguiente: /ciclo Implementar y aprobar")), [])

    def test_decision_en_ciclo_sin_verbo_delante(self) -> None:
        """FW-348: «pendiente del builder» y parecidas son la misma decisión pendiente."""
        for instr in ("Pendiente del builder: elegir proveedor de pagos", "pendiente de tu decisión sobre el esquema",
                      "Pendiente de aprobación del plan técnico", "Pendiente de que apruebes el diff",
                      "Pendiente de la confirmación del alcance", "Pendientes del builder: A o B"):
            self.assertEqual(codes(close(f"Siguiente: /ciclo {instr}")), ["DECISION_EN_CICLO"], instr)
        for instr in ("Implementar lo pendiente del módulo de pagos", "Pendientes del módulo M3: cerrar los tests",
                      "Cerrar lo pendiente del builder_profile en src/perfil.py", "Pendiente de migrar: mover auth a src/auth.py"):
            self.assertEqual(codes(close(f"Siguiente: /ciclo {instr}")), [], instr)
        self.assertEqual(codes(close("Siguiente: /arrancar Pendiente del builder: elegir proveedor")), [])


class DecisionFormTests(unittest.TestCase):
    LINE = "Siguiente: decisión — responde A (recomendada) o B en este chat"

    def test_valid_without_model_line_has_no_sin_modelo(self) -> None:
        self.assertEqual(codes(close(self.LINE, model=None)), [])

    def test_valid_with_wellformed_model_line(self) -> None:
        self.assertEqual(codes(close(self.LINE)), [])

    def test_bad_model_line(self) -> None:
        self.assertEqual(codes(close(self.LINE, model="Modelo: raro")), ["MODELO_MAL_FORMADO"])

    def test_empty_text(self) -> None:
        self.assertEqual(codes(close("Siguiente: decisión —", model=None)), ["INSTRUCCION_VACIA"])
        self.assertEqual(codes(close("Siguiente: decisión", model=None)), ["INSTRUCCION_VACIA"])

    def test_inside_code_block(self) -> None:
        self.assertEqual(codes(f"Hecho.\n```\n{self.LINE}\n```"), ["EN_BLOQUE_DE_CODIGO"])

    def test_text_after(self) -> None:
        self.assertEqual(codes(close(self.LINE, model=None) + "\nUn párrafo más."), ["NO_ES_LO_ULTIMO"])

    def test_marker(self) -> None:
        self.assertEqual(codes(close("Siguiente: decisión — responde <A o B>", model=None)), ["MARCADOR"])

    def test_duplicated(self) -> None:
        self.assertEqual(codes(self.LINE + "\n" + close(self.LINE, model=None)), ["VARIAS_LINEAS_SIGUIENTE"])

    def test_does_not_emit_sin_comando_nor_desconocido(self) -> None:
        for variant in (self.LINE, "Siguiente: decisión – elige una opción", "Siguiente: Decisión - elige una opción"):
            result = codes(close(variant, model=None))
            self.assertNotIn("SIN_COMANDO", result)
            self.assertNotIn("COMANDO_DESCONOCIDO", result)
        parts = tc.extract_close_parts(close(self.LINE, model=None), manifest=MANIFEST)
        self.assertEqual((parts.form, parts.command), ("decision", ""))


class NingunoFormTests(unittest.TestCase):
    def test_valid_without_model_line(self) -> None:
        self.assertEqual(codes(close("Siguiente: ninguno — bloqueo", model=None)), [])

    def test_bad_model_line_is_validated(self) -> None:
        self.assertEqual(codes(close("Siguiente: ninguno — bloqueo", model="Modelo: x")), ["MODELO_MAL_FORMADO"])

    def test_empty_motive(self) -> None:
        self.assertEqual(codes(close("Siguiente: ninguno", model=None)), ["INSTRUCCION_VACIA"])
        self.assertEqual(codes(close("Siguiente: ninguno —", model=None)), ["INSTRUCCION_VACIA"])

    def test_in_block_text_after_marker_and_duplicate(self) -> None:
        self.assertEqual(codes("```\nSiguiente: ninguno — x\n```"), ["EN_BLOQUE_DE_CODIGO"])
        self.assertEqual(codes(close("Siguiente: ninguno — x", model=None) + "\notro"), ["NO_ES_LO_ULTIMO"])
        self.assertEqual(codes(close("Siguiente: ninguno — [motivo]", model=None)), ["MARCADOR"])
        self.assertEqual(codes("Siguiente: ninguno — a\nSiguiente: ninguno — b"), ["VARIAS_LINEAS_SIGUIENTE"])


class TemplateModeTests(unittest.TestCase):
    def test_instruction_and_module_markers_are_admitted(self) -> None:
        self.assertEqual(codes("Siguiente: /ciclo <instrucción concreta del siguiente módulo>", "template"), [])
        self.assertEqual(codes("Siguiente: /ciclo Construir <módulo> con todos los gates", "template"), [])
        self.assertEqual(codes(close("Siguiente: /ciclo <instrucción>"), "live"), ["MARCADOR"])

    def test_other_markers_remain_errors_in_template(self) -> None:
        self.assertEqual(codes("Siguiente: /ciclo Hacer <algo>", "template"), ["MARCADOR"])
        self.assertEqual(codes("Siguiente: /ciclo Hacer [instrucción]", "template"), ["MARCADOR"])
        self.assertEqual(codes("Siguiente: [comando activo] — continuar flujo actual.", "template"), ["SIN_COMANDO", "MARCADOR"])

    def test_template_does_not_require_model_line(self) -> None:
        self.assertEqual(codes("Siguiente: /ciclo Hacer algo", "template"), [])


class RealManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8"))

    def test_nine_builder_commands_pass(self) -> None:
        names = [c["name"] for c in self.manifest["commands"] if c.get("builder_facing")]
        self.assertEqual(len(names), 9)
        for name in names:
            text = close(f"Siguiente: /{name} Hacer algo concreto y suficiente")
            self.assertEqual(tc.validate_turn_close(text, manifest=self.manifest), [], name)

    def test_internal_command_is_rejected(self) -> None:
        internal = next(c["name"] for c in self.manifest["commands"] if not c.get("builder_facing"))
        defects = tc.validate_turn_close(close(f"Siguiente: /{internal} Hacer algo"), manifest=self.manifest)
        self.assertEqual([d.code for d in defects], ["COMANDO_INTERNO"])


if __name__ == "__main__":
    unittest.main()
