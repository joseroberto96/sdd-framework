"""L7 (5.2.3, FW-294 y FW-305): el doctor vigila el host y las lecturas del framework no pasan por el revisor."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import ensure_codex_integration as eci
import framework_doctor as doctor


def load_rules(text: str) -> list[dict]:
    """Starlark ≈ Python para este subconjunto: se ejecuta con un `prefix_rule` espía."""
    rules: list[dict] = []
    exec(compile(text, "framework-reads.rules", "exec"), {"prefix_rule": lambda **kw: rules.append(kw)})
    return rules


def allowed(rules: list[dict], command: str) -> bool:
    words = command.split()
    for rule in rules:
        pattern = rule["pattern"]
        if len(words) < len(pattern):
            continue
        if all((w in p) if isinstance(p, list) else w == p for w, p in zip(words, pattern)):
            return rule.get("decision", "allow") == "allow"
    return False


class ReadRulesTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules(eci.CODEX_RULES_TEXT)

    def test_las_lecturas_del_framework_se_permiten(self):
        for command in (
            "python3 .agent/scripts/state_manager.py query --view startup",
            "python3 -B .agent/scripts/state_manager.py query --view implementation --module-id M1",
            "python3 .agent/scripts/state_manager.py lookup --kind receipts",
            "python3 .agent/scripts/state_manager.py describe refresh_approval_receipt",
            "python3 .agent/scripts/framework_doctor.py",
        ):
            self.assertTrue(allowed(self.rules, command), command)

    def test_mutaciones_push_y_destructivos_no_casan(self):
        for command in (
            "python3 .agent/scripts/state_manager.py mutate --action update_last_step --payload {}",
            "python3 -B .agent/scripts/state_manager.py mutate --action complete_module",
            "python3 .agent/scripts/state_manager.py",
            "python3 .agent/scripts/propagate_framework.py --apply",
            "git push --force origin main",
            "rm -rf build",
        ):
            self.assertFalse(allowed(self.rules, command), command)

    def test_los_ejemplos_declarados_son_coherentes_con_el_patron(self):
        for rule in self.rules:
            for example in rule["match"]:
                self.assertTrue(allowed([rule], example), example)
            for example in rule["not_match"]:
                self.assertFalse(allowed([rule], example), example)
            self.assertEqual(rule["decision"], "allow")

    def test_ningun_patron_nombra_mutate_ni_push(self):
        flat = json.dumps([rule["pattern"] for rule in self.rules])
        self.assertNotIn("mutate", flat)
        self.assertNotIn("push", flat)


class RulesFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_se_crea_y_es_idempotente(self):
        changed, _ = eci._ensure_codex_rules(self.root)
        self.assertTrue(changed)
        eci._write_codex_rules(self.root)
        self.assertEqual((self.root / eci.CODEX_RULES_REL).read_text(encoding="utf-8"), eci.CODEX_RULES_TEXT)
        self.assertFalse(eci._ensure_codex_rules(self.root)[0])

    def test_un_archivo_ajeno_con_ese_nombre_se_preserva(self):
        target = self.root / eci.CODEX_RULES_REL
        target.parent.mkdir(parents=True)
        target.write_text('prefix_rule(pattern=["ls"], decision="allow")\n', encoding="utf-8")
        with self.assertRaises(SystemExit):
            eci._ensure_codex_rules(self.root)
        self.assertIn("ls", target.read_text(encoding="utf-8"))

    def test_un_enlace_simbolico_se_rechaza(self):
        target = self.root / eci.CODEX_RULES_REL
        target.parent.mkdir(parents=True)
        outside = self.root / "otro.rules"
        outside.write_text("x\n", encoding="utf-8")
        target.symlink_to(outside)
        with self.assertRaises(SystemExit):
            eci._ensure_codex_rules(self.root)


class HostDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def run_check(self, mode="initialized"):
        warnings: list[str] = []
        doctor.validate_host_conflicts(mode, warnings, home=self.home)
        return warnings

    def write(self, rel, text):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_sin_archivos_del_host_no_avisa_ni_falla(self):
        self.assertEqual(self.run_check(), [])

    def test_superpowers_activo_en_claude_avisa(self):
        self.write(".claude/settings.json", json.dumps({"enabledPlugins": {"superpowers@marketplace": True}}))
        warnings = self.run_check()
        self.assertTrue(any("superpowers" in w and "el framework manda" in w for w in warnings), warnings)

    def test_superpowers_desactivado_no_avisa(self):
        self.write(".claude/settings.json", json.dumps({"enabledPlugins": {"superpowers@marketplace": False}}))
        self.assertEqual(self.run_check(), [])

    def test_superpowers_en_codex_avisa(self):
        self.write(".codex/config.toml", '[plugins."superpowers@openai"]\nenabled = true\n')
        self.assertTrue(any("config.toml" in w for w in self.run_check()))

    def test_en_la_plantilla_superpowers_no_avisa(self):
        self.write(".claude/settings.json", json.dumps({"enabledPlugins": {"superpowers@marketplace": True}}))
        self.assertEqual(self.run_check("template"), [])

    def test_reglas_del_adaptador_en_la_memoria_avisan_en_cualquier_modo(self):
        self.write(".codex/memories/memory_summary.md", "- usa small self-contained tasks y BLOCKED_HANDOFF\n")
        for mode in ("initialized", "template"):
            warnings = self.run_check(mode)
            self.assertTrue(any("memory_summary.md" in w and "FW-294" in w for w in warnings), warnings)

    def test_archivos_ilegibles_o_corruptos_no_rompen_el_doctor(self):
        self.write(".claude/settings.json", "{no es json")
        self.write(".codex/config.toml", "= = =")
        self.assertEqual(self.run_check(), [])

    def test_el_override_de_entorno_se_respeta(self):
        self.write(".codex/memories/MEMORY.md", "slow Codex subagent\n")
        os.environ["FRAMEWORK_HOST_HOME"] = str(self.home)
        self.addCleanup(os.environ.pop, "FRAMEWORK_HOST_HOME", None)
        warnings: list[str] = []
        doctor.validate_host_conflicts("initialized", warnings)
        self.assertTrue(any("MEMORY.md" in w for w in warnings))


class TextWiringTests(unittest.TestCase):
    def test_adaptador_y_gycd_dicen_que_las_reglas_no_son_preferencias(self):
        for relative in (".agent/adapters/codex.md", ".agent/workflows/gycd.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("no son preferencias del builder", text, relative)
            self.assertIn("memoria del host", " ".join(text.split()), relative)


if __name__ == "__main__":
    unittest.main()
