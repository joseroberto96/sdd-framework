"""Confinamiento de rutas del sincronizador (triaje 2026-07-09, Oleada 3).

Los `name`/aliases del manifest se usan como componentes de RUTA de escritura
y los `source` como rutas de lectura. Un manifest malicioso o corrupto no debe
poder escribir/leer fuera del repo (PoC de la auditoría: `../../../escaped`).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import codex_profile  # noqa: E402
import sync_claude_commands as sc  # noqa: E402


def _codex_only_project() -> bool:
    """Los proyectos Codex no deben exigirse ni generar superficies ajenas."""
    return codex_profile.detect_primary_harness(ROOT).get("status") == "codex"


class SlugConfinementTests(unittest.TestCase):
    def test_rechaza_slugs_de_escape(self):
        for malo in ("../../../escaped", "a/b", "..", "a\\b", ".oculto", "-flag", "", "  "):
            with self.assertRaises(ValueError, msg=f"debió rechazar {malo!r}"):
                sc.normalize_slug(malo)

    def test_acepta_slugs_reales_del_manifest(self):
        manifest = sc.load_manifest()
        for cmd in manifest["commands"]:
            sc.normalize_slug(cmd["name"])  # no debe lanzar
            for alias in cmd.get("aliases", []) or []:
                sc.normalize_slug(alias)

    def test_source_fuera_del_repo_rechazado(self):
        with self.assertRaises(ValueError):
            sc._assert_confined(ROOT / ".." / "fuera.md", ROOT)

    def test_source_dentro_del_repo_pasa(self):
        p = sc._assert_confined(ROOT / ".agent" / "workflows" / "gycd.md", ROOT)
        self.assertTrue(str(p).endswith("gycd.md"))

    def test_wrapper_codex_escapa_descripcion_yaml(self):
        description = 'Ejecuta "/ciclo fase" con "--paralelo" y mantiene Unicode: autónomo'
        wrapper = sc.codex_wrapper_text("ciclo", description, ".agent/workflows/ciclo.md")
        self.assertIn(
            'description: "Ejecuta \\"/ciclo fase\\" con \\"--paralelo\\" y mantiene Unicode: autónomo"',
            wrapper,
        )


class CodexAsciiSlugTests(unittest.TestCase):
    """FW-100 (PA FW-041): la superficie Codex emite slugs ASCII deterministas
    — macOS/Codex transliteran no-ASCII de forma impredecible (añadir→a-adir)
    creando wrappers huérfanos duplicados."""

    def test_slug_translitera_enie_y_acentos(self):
        self.assertEqual(sc.codex_slug("añadir-experto"), "anadir-experto")
        self.assertEqual(sc.codex_slug("diseñar"), "disenar")
        self.assertEqual(sc.codex_slug("análisis"), "analisis")

    def test_slug_ascii_queda_intacto(self):
        self.assertEqual(sc.codex_slug("arrancar"), "arrancar")
        self.assertEqual(sc.codex_slug("cierre-modulo"), "cierre-modulo")

    def test_slug_nfd_y_nfc_convergen(self):
        import unicodedata
        nfc = unicodedata.normalize("NFC", "añadir-experto")
        nfd = unicodedata.normalize("NFD", "añadir-experto")
        self.assertEqual(sc.codex_slug(nfc), sc.codex_slug(nfd))

    def test_doctor_y_sync_comparten_slug(self):
        # Una sola verdad: el doctor importa codex_slug del sync — si esto
        # rompe, los esperados divergen y vuelven los huérfanos.
        import framework_doctor  # noqa: F401
        import sync_claude_commands
        self.assertTrue(callable(sync_claude_commands.codex_slug))


class WrapperFrontmatterYamlSafetyTests(unittest.TestCase):
    """FW-137 (2026-08-25): `claude_wrapper_text` interpolaba `description` sin
    comillas ni escape en el frontmatter YAML — a diferencia de
    `opencode_wrapper_text` (que sí escapa) y `codex_wrapper_text` (que usa
    `json.dumps`, YAML flow-scalar válido). Antes del fix, cualquier
    description con ':' rompía el YAML; tras endurecer las 39 descriptions del
    manifest con la cláusula "Usar cuando: ..." (todas llevan ':'), el bug
    pasó de latente-ocasional a universal en los 29 wrappers Claude/OpenCode.
    Verificación sin dependencia externa (pyyaml no es dependencia del
    framework): la línea `description:` debe quedar entre comillas dobles
    cuando el valor contiene ':' o '"' — mismo criterio que un parser YAML
    real usaría para decidir si el escalar es ambiguo."""

    def _description_line(self, text: str) -> str:
        frontmatter = text.split("---", 2)[1]
        for line in frontmatter.splitlines():
            if line.startswith("description:"):
                return line
        raise AssertionError(f"sin línea 'description:' en frontmatter: {frontmatter!r}")

    def test_claude_wrapper_quotes_description_with_colon(self):
        text = sc.claude_wrapper_text("x", "Etiqueta — Usar cuando: pasa algo", ".agent/workflows/x.md")
        line = self._description_line(text)
        self.assertTrue(
            line.startswith('description: "') and line.endswith('"'),
            f"description con ':' debe ir entre comillas: {line!r}",
        )

    def test_claude_wrapper_escapes_embedded_quotes(self):
        text = sc.claude_wrapper_text("x", 'Usar cuando: dice "algo" concreto', ".agent/workflows/x.md")
        line = self._description_line(text)
        self.assertIn('\\"algo\\"', line, f"comillas internas deben escaparse: {line!r}")

    def test_all_real_claude_wrappers_have_quoted_description(self):
        claude_dir = ROOT / ".claude" / "commands"
        if not claude_dir.exists():
            self.skipTest(".claude/commands no existe en este checkout")
        checked = 0
        for path in claude_dir.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            line = self._description_line(text)
            self.assertTrue(
                line.startswith('description: "') and line.endswith('"'),
                f"{path.name}: description sin comillas — YAML frontmatter inválido: {line!r}",
            )
            checked += 1
        self.assertGreater(checked, 0, "no se revisó ningún wrapper Claude")


class SyncCheckParityTests(unittest.TestCase):
    """Verifica que el comprobador sin mutación (--check) opere en paridad exacta
    con la generación real en disco y el doctor, incluyendo todas las superficies:
    Claude, Codex, OpenCode commands, OpenCode agents y OpenCode skills."""

    def test_check_sync_passes_on_clean_repo(self):
        manifest = sc.load_manifest()
        issues = sc.check_sync(manifest, codex_only=_codex_only_project())
        self.assertEqual(issues, [], f"check_sync reporta drift inesperado: {issues}")

    def test_expected_opencode_skills_slug_normalized(self):
        if _codex_only_project():
            self.skipTest("un proyecto Codex no certifica ni genera skills OpenCode")
        manifest = sc.load_manifest()
        expected = sc._expected_generated_files(manifest, include_claude=True)
        opencode_skills = [
            p for p in expected
            if p.is_relative_to(sc.OPENCODE_SKILLS)
        ]
        self.assertGreater(len(opencode_skills), 0, "debe haber skills OpenCode esperadas")
        for p in opencode_skills:
            slug = p.parent.name
            self.assertNotIn(
                "_",
                slug,
                f"El directorio de skill OpenCode {p} contiene '_' en lugar de '-'",
            )
            self.assertTrue(
                p.exists(),
                f"La skill OpenCode esperada en memoria no existe en disco: {p}",
            )

    def test_main_check_entrypoint_passes(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sc.main(["--codex-only", "--check"] if _codex_only_project() else ["--check"])
        self.assertIn("check: OK", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
