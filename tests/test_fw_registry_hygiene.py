"""L8 (5.2.3, FW-303 y FW-304): prefijo de FW por proyecto y detector de entradas obsoletas."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import detect_stale_field_report as stale
import framework_doctor as doctor
import fw_registry


class PrefixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "_project").mkdir()

    def profile(self, text):
        (self.root / fw_registry.PROFILE_REL).write_text(text, encoding="utf-8")

    def test_prefijo_declarado(self):
        self.profile("# Perfil\n- `fw_prefix`: TL\n")
        self.assertEqual(fw_registry.fw_prefix(self.root), "TL")

    def test_marcador_de_plantilla_vacio_o_invalido_no_cuenta(self):
        for line in ("- `fw_prefix`: [prefijo corto del proyecto, p. ej. TL]", "- `fw_prefix`:", "- fw_prefix: no vale con espacios"):
            self.profile(line + "\n")
            self.assertIsNone(fw_registry.fw_prefix(self.root), line)

    def test_sin_perfil_no_falla(self):
        self.assertIsNone(fw_registry.fw_prefix(self.root))

    def test_la_plantilla_trae_el_campo_sin_valor(self):
        self.assertIsNone(fw_registry.fw_prefix(ROOT))
        self.assertIn("fw_prefix", (ROOT / fw_registry.PROFILE_REL).read_text(encoding="utf-8"))


class DuplicateTests(unittest.TestCase):
    def test_seccion_y_fila_con_el_mismo_id_se_detectan(self):
        text = "## FW-310 — Algo (2026-10-02)\n\n| **FW-310** | otra cosa | cerrada |\n"
        self.assertEqual(fw_registry.duplicate_ids(text), ["FW-310"])

    def test_dos_filas_iguales_se_detectan(self):
        self.assertEqual(fw_registry.duplicate_ids("| **FW-311** | a |\n| **FW-311** | b |\n"), ["FW-311"])

    def test_un_rango_con_su_fila_no_es_duplicado(self):
        self.assertEqual(fw_registry.duplicate_ids("## FW-228…235 — Auditoría\n\n| **FW-228** | x |\n"), [])

    def test_prefijos_distintos_no_chocan(self):
        self.assertEqual(fw_registry.duplicate_ids("## TL-FW-5 — a\n\n## FW-5 — b\n"), [])

    def test_los_historicos_conocidos_no_avisan_pero_uno_nuevo_si(self):
        text = "| **FW-150** | a |\n| **FW-150** | b |\n| **FW-312** | a |\n| **FW-312** | b |\n"
        self.assertEqual(fw_registry.duplicate_ids(text), ["FW-312"])

    def test_el_registro_real_de_la_plantilla_esta_limpio(self):
        text = (ROOT / ".agent" / "docs" / "field_report.md").read_text(encoding="utf-8")
        self.assertEqual(fw_registry.duplicate_ids(text), [])


class UnprefixedTests(unittest.TestCase):
    def test_entrada_nueva_sin_prefijo_se_avisa_y_la_antigua_no(self):
        text = (
            "## FW-100 — Vieja (2026-09-01)\n\n"
            "## FW-300 — Nueva sin prefijo (2026-10-02)\n\n"
            "## TL-FW-301 — Nueva con prefijo (2026-10-02)\n\n"
            "## FW-302 — Con fecha en el cuerpo\n\n**Fecha:** 2026-10-03\n"
        )
        found = fw_registry.unprefixed_new_entries(text)
        self.assertEqual([item.split()[0] for item in found], ["FW-300", "FW-302"])


class DoctorRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "_project").mkdir()
        (self.root / "_feedback").mkdir()
        original = doctor.ROOT
        doctor.ROOT = self.root
        self.addCleanup(setattr, doctor, "ROOT", original)

    def run_check(self, mode):
        warnings: list[str] = []
        doctor.validate_fw_registry(mode, warnings)
        return warnings

    def test_adoptante_sin_prefijo_declarado_avisa_una_vez(self):
        (self.root / "_feedback" / "field_report.md").write_text("## FW-1 — x (2026-10-02)\n", encoding="utf-8")
        warnings = self.run_check("initialized")
        self.assertEqual(len(warnings), 1)
        self.assertIn("fw_prefix", warnings[0])

    def test_adoptante_con_prefijo_avisa_de_entradas_nuevas_sin_el(self):
        (self.root / fw_registry.PROFILE_REL).write_text("- `fw_prefix`: TL\n", encoding="utf-8")
        (self.root / "_feedback" / "field_report.md").write_text(
            "## FW-1 — vieja (2026-09-01)\n\n## FW-2 — nueva (2026-10-02)\n\n## TL-FW-3 — ok (2026-10-02)\n",
            encoding="utf-8",
        )
        warnings = self.run_check("initialized")
        self.assertEqual(len(warnings), 1)
        self.assertIn("FW-2", warnings[0])
        self.assertNotIn("FW-1 ", warnings[0])

    def test_adoptante_sin_field_report_no_avisa(self):
        self.assertEqual(self.run_check("initialized"), [])

    def test_plantilla_avisa_de_duplicados(self):
        report = self.root / ".agent" / "docs" / "field_report.md"
        report.parent.mkdir(parents=True)
        report.write_text("| **FW-320** | a |\n| **FW-320** | b |\n", encoding="utf-8")
        warnings = self.run_check("template")
        self.assertTrue(any("FW-320" in w for w in warnings), warnings)


class StaleDetectorTests(unittest.TestCase):
    def test_abierto_y_abierta_cuentan_como_estado_abierto(self):
        text = "| **FW-1** | algo | abierto |\n| **FW-2** | algo | **Abierta** (2026-09-01) |\n| **FW-3** | algo | cerrada-2026-09-01 |\n"
        self.assertEqual([row.fw_id for row in stale.parse_open_rows(text)], ["1", "2"])

    def test_secciones_narrativas_con_estado_abierta(self):
        text = (
            "## FW-10 — Algo (2026-09-30)\n\n**Qué pasó.** Toca `src/a.py`.\n\n**Estado:** abierta — 5.2.3\n\n"
            "## FW-11 — Otra\n\n**Estado:** cerrada-2026-10-01\n"
        )
        rows = stale.parse_open_rows(text)
        self.assertEqual([row.fw_id for row in rows], ["10"])
        self.assertEqual(rows[0].cited_files, ("src/a.py",))

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def commit(self, rel, content, message, date):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git("add", "-A")
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message,
             "--date", f"{date}T12:00:00"],
            check=True, capture_output=True, env={"GIT_COMMITTER_DATE": f"{date}T12:00:00", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        )

    def make_repo(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q")

    def test_commit_que_toca_el_archivo_citado_sin_nombrar_el_fw_se_detecta(self):
        self.make_repo()
        report = (
            "## FW-10 — Algo (2026-09-30)\n\n**Qué pasó.** Falla `src/modulo.py`.\n\n**Estado:** abierta\n"
        )
        self.commit("src/modulo.py", "v1\n", "feat: base", "2026-09-01")
        self.commit("src/modulo.py", "v2\n", "fix: arregla el modulo sin citar nada", "2026-10-02")
        (self.repo / "field_report.md").write_text(report, encoding="utf-8")
        findings = stale.stale_field_report_candidates(self.repo / "field_report.md", self.repo)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("src/modulo.py", findings[0])

    def test_un_commit_anterior_a_la_fecha_no_cuenta(self):
        self.make_repo()
        self.commit("src/modulo.py", "v1\n", "feat: base", "2026-09-01")
        (self.repo / "field_report.md").write_text(
            "## FW-10 — Algo (2026-09-30)\n\nToca `src/modulo.py`.\n\n**Estado:** abierta\n", encoding="utf-8"
        )
        self.assertEqual(stale.stale_field_report_candidates(self.repo / "field_report.md", self.repo), [])

    def test_los_archivos_calientes_no_discriminan(self):
        self.make_repo()
        for index in range(stale.HOT_FILE_COMMITS + 1):
            self.commit("src/caliente.py", f"v{index}\n", f"chore: cambio {index}", "2026-10-02")
        (self.repo / "field_report.md").write_text(
            "## FW-10 — Algo (2026-09-30)\n\nToca `src/caliente.py`.\n\n**Estado:** abierta\n", encoding="utf-8"
        )
        self.assertEqual(stale.stale_field_report_candidates(self.repo / "field_report.md", self.repo), [])

    def test_si_el_subject_ya_cita_el_fw_no_se_duplica_el_hallazgo(self):
        self.make_repo()
        self.commit("src/modulo.py", "v1\n", "fix: cierra FW-10 en modulo", "2026-10-02")
        (self.repo / "field_report.md").write_text(
            "## FW-10 — Algo (2026-09-30)\n\nToca `src/modulo.py`.\n\n**Estado:** abierta\n", encoding="utf-8"
        )
        self.assertEqual(len(stale.stale_field_report_candidates(self.repo / "field_report.md", self.repo)), 1)


if __name__ == "__main__":
    unittest.main()
