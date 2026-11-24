#!/usr/bin/env python3
"""L4 (5.2.3, ADR-073): AGENTS.md como fuente única y bloque gestionado."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "root_docs" / "example_app"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import root_docs as rd  # noqa: E402
import migrate_root_docs as mig  # noqa: E402
from _dynamic_import import load_module_from_path  # noqa: E402


def _project(tmp: str, agents: str, claude: str) -> Path:
    project = Path(tmp) / "Mi Proyecto con espacios"
    (project / "_project").mkdir(parents=True)
    (project / "AGENTS.md").write_text(agents, encoding="utf-8")
    (project / "CLAUDE.md").write_text(claude, encoding="utf-8")
    return project


def _legacy_pair() -> tuple[str, str]:
    return (FIXTURES / "AGENTS.md").read_text(encoding="utf-8"), (FIXTURES / "CLAUDE.md").read_text(encoding="utf-8")


class ManagedBlockTests(unittest.TestCase):
    def test_template_roots_have_a_valid_block_and_are_not_flagged_as_edited(self):
        for rel in ("AGENTS.md", "CLAUDE.md"):
            block = rd.find_block((ROOT / rel).read_text(encoding="utf-8"))
            self.assertIsNotNone(block, rel)
            self.assertFalse(rd.edited_by_hand(block), rel)

    def test_template_claude_md_imports_agents_md_and_has_no_project_zone(self):
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("@AGENTS.md\n"))
        self.assertNotIn("## Stack", text)
        self.assertIn("## Stack", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))

    def test_replace_block_leaves_everything_outside_byte_identical(self):
        text = "# Mío\n\nZona propia  \n\n" + rd.render_block("viejo") + "\n\nCola propia\n"
        out = rd.replace_block(text, "nuevo")
        before, after = text.split(rd.find_block(text).body)[0].split("<!-- BEGIN")[0], text.split("<!-- FIN GESTIONADO: framework -->")[1]
        self.assertTrue(out.startswith(before))
        self.assertTrue(out.endswith(after))
        self.assertIn("nuevo", out)
        self.assertNotIn("viejo", out)

    def test_fingerprint_ignores_generated_subblocks_but_not_the_rest(self):
        gen = "<!-- BEGIN GENERADO: t — x -->\n{}\n<!-- FIN GENERADO: t -->"
        a = "texto\n" + gen.format("uno")
        b = "texto\n" + gen.format("dos")
        self.assertEqual(rd.fingerprint(a), rd.fingerprint(b))
        self.assertNotEqual(rd.fingerprint(a), rd.fingerprint("otro texto\n" + gen.format("uno")))

    def test_regenerating_generated_subblocks_keeps_the_fingerprint_but_a_hand_edit_does_not(self):
        text = rd.render_block("cuerpo\n<!-- BEGIN GENERADO: t — x -->\nA\n<!-- FIN GENERADO: t -->")
        self.assertFalse(rd.edited_by_hand(rd.find_block(text)))
        regenerated = text.replace("\nA\n", "\nB\n")  # lo hace sync_claude_commands
        self.assertFalse(rd.edited_by_hand(rd.find_block(regenerated)))
        tampered = text.replace("cuerpo", "cuerpo editado")
        self.assertTrue(rd.edited_by_hand(rd.find_block(tampered)))

    def test_malformed_or_quoted_markers_abort_instead_of_guessing(self):
        real = rd.render_block("cuerpo")
        quoted = "```\n<!-- BEGIN GESTIONADO: framework sha256=abc -->\n```\n\n" + real
        with self.assertRaises(rd.BlockError):
            rd.find_block(quoted)
        with self.assertRaises(rd.BlockError):
            rd.replace_block(quoted, "x")
        with self.assertRaises(rd.BlockError):
            rd.find_block(real + "\n<!-- FIN GESTIONADO: framework -->\n")
        with self.assertRaises(rd.BlockError):
            rd.find_block("<!-- FIN GESTIONADO: framework -->\n" + real.split("\n")[0])
        # citado en línea (con texto delante) no cuenta como marcador
        self.assertIsNone(rd.find_block("ver `<!-- BEGIN GESTIONADO: framework -->` y `<!-- FIN GESTIONADO: framework -->`"))

    def test_crlf_is_preserved_outside_the_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.md"
            path.write_bytes(("# Mío\r\n\r\n" + rd.render_block("viejo").replace("\n", "\r\n") + "\r\nCola\r\n").encode())
            text, eol = rd.read_raw(path)
            self.assertEqual(eol, "\r\n")
            out = rd.restore_eol(rd.replace_block(text, "nuevo"), eol)
            self.assertTrue(out.startswith("# Mío\r\n\r\n") and out.endswith("\r\nCola\r\n"))
            self.assertNotIn("\n", out.replace("\r\n", ""))

    def test_forbidden_orders_and_dead_lines(self):
        self.assertTrue(rd.forbidden_orders("- Luego leer `CLAUDE.md` aunque el entorno no sea Claude."))
        self.assertTrue(rd.forbidden_orders("- Leer primero `GUARDRAILS_MASTER.md` si existe"))
        self.assertEqual(rd.forbidden_orders("Abrir `GUARDRAILS_MASTER.md` bajo demanda; `CLAUDE.md` es de Claude."), [])
        texto, quitadas = rd.strip_dead_lines("a\nexige BLOCKED_HANDOFF aquí\nb\nel límite duro del presupuesto de contexto\n")
        self.assertEqual(texto, "a\nb\n")
        self.assertEqual(len(quitadas), 2)
        # español corriente: no se toca sin contexto de presupuesto de Codex
        prosa = "Respetar el límite duro de 3 intentos y el relevo fresco del turno.\n"
        self.assertEqual(rd.strip_dead_lines(prosa), (prosa, []))
        # las variantes que el doctor marcaría también se retiran
        self.assertTrue(rd.strip_dead_lines("LEER PRIMERO GUARDRAILS_MASTER.md\nok\n")[1])

    def test_template_has_no_dead_or_forbidden_orders(self):
        for rel in ("AGENTS.md", "CLAUDE.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertEqual(rd.forbidden_orders(text), [], rel)
            self.assertEqual(rd.strip_dead_lines(text)[1], [], rel)


class MigrationTests(unittest.TestCase):
    def test_example_app_roots_migrate_without_losing_project_content(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, claude)
            result = mig.compute(project)
        new_agents = result["files"]["AGENTS.md"]["new"]
        new_claude = result["files"]["CLAUDE.md"]["new"]
        zone = claude[: mig.FRAMEWORK_HEADING_RE.search(claude).start()]
        for line in (l for l in zone.splitlines() if l.strip() and l.strip() != "---"):
            self.assertIn(line, new_agents, f"se perdió de la zona del proyecto: {line[:60]}")
        body_agents, body_claude = mig.template_bodies()
        self.assertEqual(rd.find_block(new_agents).body, body_agents)
        self.assertEqual(rd.find_block(new_claude).body, body_claude)
        self.assertTrue(new_claude.startswith("@AGENTS.md\n"))
        # el protocolo antiguo del framework ya no está
        self.assertNotIn("## Framework v4 — Protocolo Mínimo", new_agents + new_claude)
        for text in (new_agents, new_claude):
            self.assertEqual(rd.forbidden_orders(text), [])
            self.assertFalse(rd.edited_by_hand(rd.find_block(text)))

    def test_old_framework_text_is_only_removed_from_the_known_sections(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            result = mig.compute(_project(tmp, agents, claude))
        # El AGENTS.md anterior ordenaba leer CLAUDE.md aunque no fuera Claude.
        self.assertTrue(rd.forbidden_orders(agents))
        self.assertEqual(rd.forbidden_orders(result["files"]["AGENTS.md"]["new"]), [])

    def test_it_is_idempotent(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, claude)
            first = mig.compute(project)
            for rel, data in first["files"].items():
                (project / rel).write_text(data["new"], encoding="utf-8")
            second = mig.compute(project)
        self.assertFalse(any(d["changed"] for d in second["files"].values()))

    def test_text_outside_the_block_survives_a_second_pass_byte_for_byte(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, claude)
            for rel, data in mig.compute(project)["files"].items():
                (project / rel).write_text(data["new"], encoding="utf-8")
            propio = (project / "AGENTS.md").read_text(encoding="utf-8")
            propio = propio.replace("## Restricciones", "## Reglas añadidas por el proyecto\n- X\n\n## Restricciones", 1)
            (project / "AGENTS.md").write_text(propio, encoding="utf-8")
            again = mig.compute(project)["files"]["AGENTS.md"]["new"]
        self.assertIn("## Reglas añadidas por el proyecto\n- X", again)

    def test_dead_phrases_are_removed_from_the_project_zone_and_reported(self):
        agents, claude = _legacy_pair()
        claude = claude.replace("## Stack", "- Exige BLOCKED_HANDOFF y relevo fresco.\n\n## Stack", 1)
        with tempfile.TemporaryDirectory() as tmp:
            result = mig.compute(_project(tmp, agents, claude))
        self.assertNotIn("BLOCKED_HANDOFF", result["files"]["AGENTS.md"]["new"])
        self.assertTrue(any("BLOCKED_HANDOFF" in line for line in result["removed_lines"]))

    def test_content_after_the_framework_protocol_is_kept_outside_the_block(self):
        agents, claude = _legacy_pair()
        claude += "\n## Reglas de dominio propias\n- Nunca operar en real.\n"
        with tempfile.TemporaryDirectory() as tmp:
            result = mig.compute(_project(tmp, agents, claude))
        new_claude = result["files"]["CLAUDE.md"]["new"]
        self.assertTrue(new_claude.rstrip().endswith("- Nunca operar en real."))
        self.assertNotIn("Nunca operar en real", rd.find_block(new_claude).body)

    def test_unsplittable_claude_md_proposes_nothing(self):
        agents, _ = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, "# Producto\nSin sección del framework.\n")
            with self.assertRaises(mig.MigrationError):
                mig.compute(project)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.assertEqual(mig.main(["--project", str(project)]), 1)
            self.assertIn("NO SE PROPONE NADA", buf.getvalue())
            self.assertEqual((project / "CLAUDE.md").read_text(encoding="utf-8"), "# Producto\nSin sección del framework.\n")

    def test_default_is_proposal_only_and_apply_writes_only_the_two_files(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, claude)
            (project / "otro.txt").write_text("intacto", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "add", "-A"], cwd=project, check=True)
            subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "x"], cwd=project, check=True)
            diff = Path(tmp) / "out.diff"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mig.main(["--project", str(project), "--out", str(diff)]), 0)
            self.assertEqual((project / "AGENTS.md").read_text(encoding="utf-8"), agents)
            self.assertTrue(diff.read_text(encoding="utf-8").startswith("--- AGENTS.md (actual)"))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mig.main(["--project", str(project), "--apply", "--out", str(diff)]), 0)
            self.assertNotEqual((project / "AGENTS.md").read_text(encoding="utf-8"), agents)
            self.assertEqual((project / "otro.txt").read_text(encoding="utf-8"), "intacto")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(mig.main(["--project", str(project), "--apply", "--out", str(diff)]), 0)
            self.assertIn("sin cambios", out.getvalue())

    def test_apply_refuses_uncommitted_root_docs(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, claude)
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "add", "-A"], cwd=project, check=True)
            subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "x"], cwd=project, check=True)
            (project / "CLAUDE.md").write_text(claude + "\nedición sin commitear\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.assertEqual(mig.main(["--project", str(project), "--apply", "--out", str(Path(tmp) / "d.diff")]), 2)
            self.assertIn("sin commitear", buf.getvalue())

    def test_symlinked_root_docs_abort_without_writing(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, claude)
            (project / "CLAUDE.md").unlink()
            (project / "CLAUDE.md").symlink_to("AGENTS.md")
            with self.assertRaises(mig.MigrationError):
                mig.compute(project)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.assertEqual(mig.main(["--project", str(project), "--apply", "--out", str(Path(tmp) / "d")]), 1)
            self.assertEqual((project / "AGENTS.md").read_text(encoding="utf-8"), agents)

    def test_legacy_protocol_without_its_closing_generated_block_proposes_nothing(self):
        agents, claude = _legacy_pair()
        claude = claude.replace("<!-- FIN GENERADO: maquinaria-interna -->", "")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(mig.MigrationError):
                mig.compute(_project(tmp, agents, claude))

    def test_migrated_agents_with_a_legacy_claude_zone_is_not_silently_dropped(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, claude)
            first = mig.compute(project)["files"]["AGENTS.md"]["new"]
            (project / "AGENTS.md").write_text(first, encoding="utf-8")  # AGENTS migrado, CLAUDE aún antiguo
            with self.assertRaises(mig.MigrationError):
                mig.compute(project)

    def test_malformed_markers_in_the_project_abort(self):
        agents, claude = _legacy_pair()
        migrated = "# P\n\n" + rd.render_block("a") + "\n" + rd.render_block("b")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(mig.MigrationError):
                mig.compute(_project(tmp, migrated, claude))

    def test_apply_needs_a_git_repo(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, claude)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.assertEqual(mig.main(["--project", str(project), "--apply", "--out", str(Path(tmp) / "d")]), 2)
            self.assertIn("no es un repo git", buf.getvalue())
            self.assertEqual((project / "AGENTS.md").read_text(encoding="utf-8"), agents)

    def test_it_refuses_to_run_outside_the_template_or_on_the_template_itself(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertEqual(mig.main(["--project", str(ROOT)]), 2)
        self.assertIn("no parece un proyecto adoptante", buf.getvalue())


class PropagatorTests(unittest.TestCase):
    def setUp(self):
        self.prop = load_module_from_path("propagate_framework_l4", SCRIPTS / "propagate_framework.py")

    def _migrated(self, tmp: str) -> Path:
        agents, claude = _legacy_pair()
        project = _project(tmp, agents, claude)
        for rel, data in mig.compute(project)["files"].items():
            (project / rel).write_text(data["new"], encoding="utf-8")
        return project

    def test_only_the_managed_block_is_replaced_and_the_diff_is_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._migrated(tmp)
            agents = project / "AGENTS.md"
            text = agents.read_text(encoding="utf-8")
            block = rd.find_block(text)
            stale = rd.replace_block(text, block.body.replace("Carga progresiva", "Carga completa"))
            agents.write_text(stale, encoding="utf-8")
            before, after = stale[: rd.find_block(stale).start], stale[rd.find_block(stale).end :]
            actualizados, diff = self.prop.sync_managed_root_docs(project)
            now = agents.read_text(encoding="utf-8")
            self.assertEqual(actualizados, ["AGENTS.md(bloque)"])
            self.assertTrue(now.startswith(before) and now.endswith(after))
            self.assertEqual(rd.find_block(now).body, block.body)
            self.assertTrue(any("Carga completa" in line for line in diff))
            self.assertEqual(self.prop.sync_managed_root_docs(project), ([], []))

    def test_unmigrated_roots_are_reported_as_legacy_and_never_written(self):
        agents, claude = _legacy_pair()
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, agents, claude)
            changes = self.prop.managed_root_changes(project)
            self.assertEqual({k: v["status"] for k, v in changes.items()}, {"AGENTS.md": "legacy", "CLAUDE.md": "legacy"})
            self.assertEqual(self.prop.sync_managed_root_docs(project), ([], []))
            self.assertEqual((project / "AGENTS.md").read_text(encoding="utf-8"), agents)
            self.assertEqual((project / "CLAUDE.md").read_text(encoding="utf-8"), claude)

    def test_symlinked_or_malformed_roots_are_never_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._migrated(tmp)
            real = project / "AGENTS.md"
            original = real.read_text(encoding="utf-8")
            (project / "CLAUDE.md").unlink()
            (project / "CLAUDE.md").symlink_to("AGENTS.md")
            real.write_text(original + "\n" + rd.render_block("duplicado"), encoding="utf-8")
            actualizados, mostrado = self.prop.sync_managed_root_docs(project)
            self.assertEqual(actualizados, [])
            self.assertTrue(any("symlink" in l for l in mostrado) and any("malformed" in l for l in mostrado), mostrado)
            self.assertTrue((project / "CLAUDE.md").is_symlink())

    def test_crlf_roots_keep_their_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._migrated(tmp)
            agents = project / "AGENTS.md"
            text = agents.read_text(encoding="utf-8")
            stale = rd.replace_block(text, rd.find_block(text).body.replace("Carga progresiva", "Carga completa"))
            agents.write_bytes(stale.replace("\n", "\r\n").encode("utf-8"))
            self.prop.sync_managed_root_docs(project)
            raw = agents.read_bytes()
            self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))

    def test_upgrade_rollback_covers_claude_md(self):
        source = (SCRIPTS / "propagate_framework.py").read_text(encoding="utf-8")
        self.assertIn('"CLAUDE.md",\n        ".claude",', source)
        self.assertIn('"AGENTS.md", "CLAUDE.md", ".claude/settings.json"', source)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.doctor = load_module_from_path("framework_doctor_l4", SCRIPTS / "framework_doctor.py")
        self.original_root = self.doctor.ROOT
        self.addCleanup(setattr, self.doctor, "ROOT", self.original_root)

    def _run(self, mode, agents, claude="# C\n"):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "AGENTS.md").write_text(agents, encoding="utf-8")
            (Path(tmp) / "CLAUDE.md").write_text(claude, encoding="utf-8")
            self.doctor.ROOT = Path(tmp)
            errors, warnings = [], []
            self.doctor.validate_root_docs_contract(mode, errors, warnings)
        return errors, warnings

    def test_real_template_is_clean(self):
        self.doctor.ROOT = self.original_root
        errors, warnings = [], []
        self.doctor.validate_root_docs_contract("template", errors, warnings)
        self.assertEqual((errors, warnings), ([], []))

    def test_template_errors_on_orders_to_read_claude_md_or_load_guardrails(self):
        errors, _ = self._run("template", "- Luego leer `CLAUDE.md` aunque el entorno no sea Claude.\n")
        self.assertTrue(any("AGENTS.md" in e and "CLAUDE.md" in e for e in errors), errors)
        errors, _ = self._run("template", "# A\n", "- Leer primero `GUARDRAILS_MASTER.md` si existe\n")
        self.assertTrue(any("GUARDRAILS_MASTER.md" in e for e in errors), errors)

    def test_unmigrated_adopter_only_warns(self):
        """FW-202: un ERROR aquí revertiría la propagación de los adoptantes sin migrar."""
        errors, warnings = self._run("initialized", "- Luego leer `CLAUDE.md` aunque el entorno no sea Claude.\n")
        self.assertEqual(errors, [])
        self.assertTrue(any("migrate_root_docs.py" in w for w in warnings), warnings)
        self.assertTrue(any("no tiene el bloque" in w for w in warnings), warnings)

    def test_migrated_adopter_only_warns_on_a_forbidden_order_in_its_own_zone(self):
        """La raíz es del proyecto: un ERROR revertiría la propagación por una línea ajena (FW-202)."""
        agents = "- Leer primero `GUARDRAILS_MASTER.md`\n\n" + rd.render_block("x")
        errors, warnings = self._run("initialized", agents, rd.render_block("y"))
        self.assertEqual(errors, [])
        self.assertTrue(any("GUARDRAILS_MASTER.md" in w for w in warnings), warnings)

    def test_malformed_markers_warn_once_and_never_fail(self):
        errors, warnings = self._run("initialized", rd.render_block("a") + "\n" + rd.render_block("b"))
        self.assertEqual(errors, [])
        self.assertTrue(any("malformados" in w for w in warnings), warnings)
        self.assertFalse(any("no tiene el bloque" in w and "AGENTS.md" in w for w in warnings), warnings)

    def test_edited_block_warns_but_does_not_fail(self):
        tampered = rd.render_block("original").replace("original", "editado")
        errors, warnings = self._run("initialized", tampered, rd.render_block("y"))
        self.assertEqual(errors, [])
        self.assertTrue(any("huella" in w for w in warnings), warnings)

    def test_clean_migrated_adopter_has_no_findings(self):
        errors, warnings = self._run("initialized", "# P\n\n" + rd.render_block("a"), "@AGENTS.md\n\n" + rd.render_block("b"))
        self.assertEqual((errors, warnings), ([], []))


class SyncKeepsTheFingerprintValidTests(unittest.TestCase):
    def test_check_is_green_and_blocks_stay_unedited(self):
        result = subprocess.run(
            [sys.executable, ".agent/scripts/sync_claude_commands.py", "--check"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for rel in ("AGENTS.md", "CLAUDE.md"):
            self.assertFalse(rd.edited_by_hand(rd.find_block((ROOT / rel).read_text(encoding="utf-8"))), rel)


if __name__ == "__main__":
    unittest.main()
