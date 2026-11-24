# -*- coding: utf-8 -*-
"""Docs generados desde manifest (3.18.0, FW-070).

Calibración:
- known-good: el repo real pasa la frescura; regenerar es idempotente.
- known-bad: un bloque GENERADO editado a mano falla; una referencia a un
  workflow inexistente en un doc raíz falla.
"""
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import sync_claude_commands as scc  # noqa: E402


def _load_doctor():
    path = ROOT / ".agent" / "scripts" / "framework_doctor.py"
    spec = importlib.util.spec_from_file_location("framework_doctor", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest() -> dict:
    return json.loads((ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8"))


class TestRootDocsFreshness(unittest.TestCase):
    def setUp(self):
        self.doctor = _load_doctor()
        self.doctor.ROOT = ROOT

    def test_freshness_passes_on_real_repo(self):
        errors: list[str] = []
        self.doctor.validate_root_docs_freshness(_manifest(), errors)
        self.assertEqual(errors, [], f"repo real debe estar fresco: {errors}")

    def test_regeneration_is_idempotent(self):
        manifest = _manifest()
        claude_text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        body = scc.render_claude_internal_body(manifest)
        regenerated = scc.replace_generated_block(claude_text, "maquinaria-interna", body)
        self.assertEqual(claude_text, regenerated, "regenerar sobre repo fresco no debe cambiar nada")

    def test_specialists_cover_all_public_commands(self):
        manifest = _manifest()
        claude_text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        public = {c["name"] for c in manifest["commands"] if c.get("claude_command", False)}
        mentioned = set(re.findall(r"`/([^`]+)`", claude_text))
        missing = public - mentioned
        self.assertEqual(missing, set(), f"comandos públicos sin documentar en CLAUDE.md: {missing}")

    def test_stale_generated_block_fails(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            for rel in ("CLAUDE.md", "AGENTS.md", "README.md", "llms.txt"):
                shutil.copy(ROOT / rel, tmp / rel)
            (tmp / ".agent" / "workflows").mkdir(parents=True)
            claude = (tmp / "CLAUDE.md").read_text(encoding="utf-8")
            # 5.1.1: manipular dentro del bloque generado. En un adoptante,
            # `/entrevistar` puede aparecer antes, fuera del bloque.
            claude = claude.replace("`/entrevistar` · `/tdd`", "`/comando-viejo-eliminado` · `/tdd`", 1)
            assert "`/comando-viejo-eliminado`" in claude
            (tmp / "CLAUDE.md").write_text(claude, encoding="utf-8")
            self.doctor.ROOT = tmp
            errors: list[str] = []
            self.doctor.validate_root_docs_freshness(_manifest(), errors)
            self.assertTrue(
                any("desactualizado" in e and "maquinaria-interna" in e for e in errors),
                f"bloque manipulado debe fallar: {errors}",
            )
        finally:
            shutil.rmtree(tmp)

    def test_dangling_workflow_reference_fails(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            for rel in ("CLAUDE.md", "AGENTS.md", "README.md", "llms.txt"):
                shutil.copy(ROOT / rel, tmp / rel)
            (tmp / ".agent" / "workflows").mkdir(parents=True)
            readme = (tmp / "README.md").read_text(encoding="utf-8")
            readme += "\nVer `.agent/workflows/workflow-fantasma.md` para más detalle.\n"
            (tmp / "README.md").write_text(readme, encoding="utf-8")
            self.doctor.ROOT = tmp
            errors: list[str] = []
            self.doctor.validate_root_docs_freshness(_manifest(), errors)
            self.assertTrue(
                any("workflow-fantasma.md" in e and "drift de poda" in e for e in errors),
                f"referencia colgante debe fallar: {errors}",
            )
        finally:
            shutil.rmtree(tmp)


    def test_builder_facing_canonical_set(self):
        # 4.0.0 (FW-071): los 9 verbos del builder son contrato; cambiarlos
        # exige tocar CLAUDE.md y este test en el mismo commit.
        manifest = _manifest()
        builder = sorted(c["name"] for c in manifest["commands"] if c.get("builder_facing"))
        self.assertEqual(
            builder,
            sorted([
                "nuevo-proyecto", "migrar-proyecto", "arrancar", "diseñar",
                "ciclo", "nuevo-bug", "reconciliar", "gycd", "actualizar-framework",
            ]),
        )

    def test_internal_wrapper_descriptions_prefixed(self):
        # Los wrappers de comandos internos se marcan "(interno — lo invoca el
        # agente)"; los de builder no.
        tdd = (ROOT / ".claude" / "commands" / "tdd.md").read_text(encoding="utf-8")
        self.assertIn("(interno — lo invoca el agente)", tdd)
        arrancar = (ROOT / ".claude" / "commands" / "arrancar.md").read_text(encoding="utf-8")
        self.assertNotIn("(interno — lo invoca el agente)", arrancar)


if __name__ == "__main__":
    unittest.main()
