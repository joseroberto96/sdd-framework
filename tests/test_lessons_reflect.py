"""Tests de lessons_reflect.py — digest determinista de lecciones con
decaimiento temporal y corroboración (FW know-how portado de graphify/reflect.py).

Propiedades bajo test:
- determinismo byte-estable dado un --now fijo,
- decaimiento con half-life (un dead_end fresco pesa más que un useful viejo),
- umbral de corroboración (una sola señal no acuña una lección preferida),
- clasificación Preferidas / Tentativas / Cuestionadas / Corregidas / Caducadas,
- degradación segura: sin 22_lessons.md no se escribe nada (modo template).
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / ".agent" / "scripts" / "lessons_reflect.py"

sys.path.insert(0, str(ROOT / "tests"))
from _dynamic_import import load_module_from_path  # noqa: E402

NOW = "2026-07-09T12:00:00+00:00"


def lesson_block(n, titulo, fecha, regla, señales=()):
    lines = [
        f"## Lección {n} — {titulo}",
        f"**Fecha:** {fecha}",
        "",
        "**Síntoma:**",
        "Algo salió mal.",
        "",
        "**Regla:**",
        regla,
        "",
        "**Por qué:**",
        "Razón concreta.",
        "",
        "**Aplicabilidad:**",
        "- Dominio: técnico",
        "- Alcance: este proyecto",
    ]
    if señales:
        lines += ["", "**Señales:**"] + [f"- {s}" for s in señales]
    return "\n".join(lines) + "\n"


HEADER = "# Lecciones Aprendidas — Proyecto Test\n\n> Cabecera.\n\n"


class LessonsReflectBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module_from_path("lessons_reflect", SCRIPT_PATH)

    def render(self, lessons_text, **kwargs):
        """Escribe lessons_text a un tmp, corre el digest y devuelve su contenido."""
        with tempfile.TemporaryDirectory() as tmp:
            lessons = Path(tmp) / "22_lessons.md"
            out = Path(tmp) / "22_lessons_digest.md"
            lessons.write_text(lessons_text, encoding="utf-8")
            rc = self.mod.generar_digest(
                lessons_path=lessons, out_path=out, now_iso=NOW, **kwargs
            )
            self.assertEqual(rc, 0)
            return out.read_text(encoding="utf-8")


class TestClasificacion(LessonsReflectBase):
    def test_leccion_recien_creada_es_tentativa(self):
        text = HEADER + lesson_block(1, "Regla nueva", "2026-07-08", "No hagas X.")
        digest = self.render(text)
        self.assertIn("## Tentativas", digest)
        self.assertIn("Lección 1 — Regla nueva", digest)

    def test_corroborada_es_preferida(self):
        # creación + 1 señal useful de fecha distinta = 2 señales → preferida
        text = HEADER + lesson_block(
            1, "Regla probada", "2026-06-01", "Siempre haz Y.",
            señales=["2026-07-01 useful — aplicada con éxito"],
        )
        digest = self.render(text)
        self.assertIn("## Preferidas", digest)
        preferidas = digest.split("## Preferidas")[1].split("##")[0]
        self.assertIn("Lección 1 — Regla probada", preferidas)
        self.assertIn("Siempre haz Y.", preferidas)

    def test_una_sola_senal_no_acuna_preferida(self):
        # sin señales adicionales, aunque sea vieja, sigue tentativa
        text = HEADER + lesson_block(1, "Solitaria", "2026-06-20", "Regla Z.")
        digest = self.render(text)
        preferidas = digest.split("## Preferidas")[1].split("##")[0] if "## Preferidas" in digest else ""
        self.assertNotIn("Solitaria", preferidas)

    def test_senales_mixtas_es_cuestionada(self):
        text = HEADER + lesson_block(
            1, "Dudosa", "2026-05-01", "Regla dudosa.",
            señales=["2026-06-01 useful — sirvió", "2026-07-05 dead_end — no aplicaba"],
        )
        digest = self.render(text)
        self.assertIn("## Cuestionadas", digest)
        self.assertIn("Lección 1 — Dudosa", digest.split("## Cuestionadas")[1].split("##")[0])

    def test_ultima_senal_corrected_va_a_corregidas(self):
        text = HEADER + lesson_block(
            1, "Equivocada", "2026-05-01", "Regla mala.",
            señales=["2026-07-01 corrected — la regla correcta es W"],
        )
        digest = self.render(text)
        self.assertIn("## Corregidas", digest)
        seccion = digest.split("## Corregidas")[1].split("##")[0]
        self.assertIn("la regla correcta es W", seccion)

    def test_leccion_vieja_sin_refuerzo_caduca(self):
        # creada hace ~2 años, half-life 30 días → peso ≈ 0 → caducada, fuera del cuerpo
        text = HEADER + lesson_block(1, "Fósil", "2024-07-01", "Regla vieja.")
        digest = self.render(text)
        self.assertNotIn("Fósil", digest.split("Caducadas")[0])
        self.assertIn("Caducadas", digest)


class TestDecaimiento(LessonsReflectBase):
    def test_dead_end_fresco_pesa_mas_que_useful_viejo(self):
        # useful hace 120 días vs dead_end hace 2 días → score negativo → cuestionada
        # con nota de score negativo (no preferida)
        text = HEADER + lesson_block(
            1, "Invertida", "2026-03-01", "Regla en duda.",
            señales=["2026-03-11 useful — sirvió entonces", "2026-07-07 dead_end — ya no aplica"],
        )
        mod = self.mod
        lecciones = mod.parsear_lecciones(text)
        score = mod.score_leccion(lecciones[0], mod.parse_now(NOW), half_life_days=30)
        self.assertLess(score, 0)

    def test_peso_decae_con_half_life(self):
        mod = self.mod
        now = mod.parse_now(NOW)
        w_fresca = mod.peso_senal(mod.parse_now("2026-07-09T12:00:00+00:00"), now, 30)
        w_30d = mod.peso_senal(mod.parse_now("2026-06-09T12:00:00+00:00"), now, 30)
        self.assertAlmostEqual(w_fresca, 1.0, places=3)
        self.assertAlmostEqual(w_30d, 0.5, places=3)


class TestDeterminismo(LessonsReflectBase):
    def test_salida_byte_estable(self):
        text = HEADER + lesson_block(
            1, "A", "2026-06-01", "Regla A.", señales=["2026-07-01 useful — ok"]
        ) + "\n" + lesson_block(2, "B", "2026-07-08", "Regla B.")
        self.assertEqual(self.render(text), self.render(text))

    def test_banner_de_autogeneracion(self):
        text = HEADER + lesson_block(1, "A", "2026-07-08", "Regla A.")
        digest = self.render(text)
        self.assertTrue(digest.startswith("<!-- GENERADO AUTOMÁTICAMENTE - NO EDITAR A MANO -->"))


class TestDegradacion(LessonsReflectBase):
    def test_sin_archivo_no_escribe_nada(self):
        with tempfile.TemporaryDirectory() as tmp:
            lessons = Path(tmp) / "22_lessons.md"  # no existe
            out = Path(tmp) / "22_lessons_digest.md"
            rc = self.mod.generar_digest(
                lessons_path=lessons, out_path=out, now_iso=NOW
            )
            self.assertEqual(rc, 0)
            self.assertFalse(out.exists())

    def test_archivo_sin_lecciones_no_escribe(self):
        with tempfile.TemporaryDirectory() as tmp:
            lessons = Path(tmp) / "22_lessons.md"
            out = Path(tmp) / "22_lessons_digest.md"
            lessons.write_text(HEADER, encoding="utf-8")
            rc = self.mod.generar_digest(lessons_path=lessons, out_path=out, now_iso=NOW)
            self.assertEqual(rc, 0)
            self.assertFalse(out.exists())


class TestShrinkGuard(LessonsReflectBase):
    """Guarda de encogimiento (port de graphify #479): una regeneración con
    menos lecciones que el digest existente no lo machaca sin --force."""

    def test_regeneracion_menor_se_niega_sin_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            lessons = Path(tmp) / "22_lessons.md"
            out = Path(tmp) / "22_lessons_digest.md"
            dos = HEADER + lesson_block(1, "A", "2026-07-01", "Regla A.") + "\n" + \
                lesson_block(2, "B", "2026-07-02", "Regla B.")
            lessons.write_text(dos, encoding="utf-8")
            self.assertEqual(self.mod.generar_digest(lessons_path=lessons, out_path=out, now_iso=NOW), 0)
            contenido_bueno = out.read_text(encoding="utf-8")
            # Corrupción parcial: solo 1 lección parseable
            lessons.write_text(HEADER + lesson_block(1, "A", "2026-07-01", "Regla A."), encoding="utf-8")
            rc = self.mod.generar_digest(lessons_path=lessons, out_path=out, now_iso=NOW)
            self.assertNotEqual(rc, 0)
            self.assertEqual(out.read_text(encoding="utf-8"), contenido_bueno)

    def test_force_permite_encoger(self):
        with tempfile.TemporaryDirectory() as tmp:
            lessons = Path(tmp) / "22_lessons.md"
            out = Path(tmp) / "22_lessons_digest.md"
            dos = HEADER + lesson_block(1, "A", "2026-07-01", "Regla A.") + "\n" + \
                lesson_block(2, "B", "2026-07-02", "Regla B.")
            lessons.write_text(dos, encoding="utf-8")
            self.mod.generar_digest(lessons_path=lessons, out_path=out, now_iso=NOW)
            lessons.write_text(HEADER + lesson_block(1, "A", "2026-07-01", "Regla A."), encoding="utf-8")
            rc = self.mod.generar_digest(lessons_path=lessons, out_path=out, now_iso=NOW, force=True)
            self.assertEqual(rc, 0)
            self.assertIn("Fuente: 1 lecciones", out.read_text(encoding="utf-8"))

    def test_digest_declara_numero_de_lecciones_fuente(self):
        text = HEADER + lesson_block(1, "A", "2026-07-08", "Regla A.")
        self.assertIn("Fuente: 1 lecciones", self.render(text))


class TestCLI(unittest.TestCase):
    def test_cli_genera_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            lessons = Path(tmp) / "22_lessons.md"
            out = Path(tmp) / "22_lessons_digest.md"
            lessons.write_text(
                HEADER + lesson_block(1, "CLI", "2026-07-08", "Regla CLI."),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--lessons", str(lessons),
                 "--out", str(out), "--now", NOW],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.exists())


class TestWiring(unittest.TestCase):
    """Los workflows deben referenciar el digest para que el flujo exista de verdad."""

    def test_gycd_regenera_digest(self):
        text = (ROOT / ".agent" / "workflows" / "gycd.md").read_text(encoding="utf-8")
        self.assertIn("lessons_reflect.py", text)

    def test_arrancar_prefiere_digest(self):
        text = (ROOT / ".agent" / "workflows" / "arrancar.md").read_text(encoding="utf-8")
        self.assertIn("22_lessons_digest.md", text)

    def test_aprender_de_error_documenta_senales(self):
        text = (ROOT / ".agent" / "workflows" / "aprender-de-error.md").read_text(encoding="utf-8")
        self.assertIn("**Señales:**", text)
        self.assertIn("useful", text)
        self.assertIn("dead_end", text)


if __name__ == "__main__":
    unittest.main()
