#!/usr/bin/env python3
"""5.3.2 correctiva (FW-402): avisos de cierre que no duplican respuestas.

Decisión del builder, «Avisos y corrección corta»:

1. `INSTRUCCION_LARGA` y `PROHIBICIONES` son avisos, como `PALABRAS_PEGADAS`: se
   registran y no bloquean, ni en Claude Code ni en Codex.
2. Cuando el hook sí bloquea, pide responder solo con las líneas de cierre corregidas,
   sin repetir la respuesta anterior; un mensaje con solo esas líneas es un cierre válido.
3. `/arrancar` redacta como una sola acción de hasta 40 palabras un puntero largo o con
   prohibiciones, sin mutar el estado.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import turn_close as tc  # noqa: E402
import turn_close_hook as hook  # noqa: E402

MANIFEST = json.loads((ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8"))
MODEL = "Modelo: standard · Esfuerzo: 🟡 medio (tier_1)"
LINE = "Siguiente: /ciclo Implementar el logout en src/auth.py con sus tests"
GOOD = "Hecho el login.\n\n" + LINE + "\n" + MODEL
BAD = "Hecho el login. Mañana seguimos."
# Un cierre real reducido: más de 40 palabras y «sin X ni Y» (FW-360).
LONG = ("Hecho.\n\nSiguiente: /ciclo " + " ".join(["paso"] * 41) + " sin commit ni push\n" + MODEL)
ONLY_CLOSE = LINE + "\n" + MODEL
ASK_ONLY_CLOSE = "Responde solo con las líneas de cierre corregidas («Siguiente: …» y, si toca, «Modelo: …»)"
NO_REPEAT = "sin repetir la respuesta anterior"


class AvisosTests(unittest.TestCase):
    """Punto 1: las dos reglas avisan y el cierre sigue siendo válido."""

    def test_las_dos_reglas_son_avisos(self) -> None:
        for code in (tc.INSTRUCCION_LARGA, tc.PROHIBICIONES):
            with self.subTest(code=code):
                self.assertIn(code, tc.WARNING_CODES)
                self.assertNotIn(code, tc.ERROR_CODES)

    def test_un_cierre_largo_y_con_prohibiciones_es_valido(self) -> None:
        defects = tc.validate_turn_close(LONG, manifest=MANIFEST)
        self.assertEqual({(d.code, d.severity) for d in defects},
                         {(tc.INSTRUCCION_LARGA, tc.SEVERITY_WARNING), (tc.PROHIBICIONES, tc.SEVERITY_WARNING)})
        self.assertTrue(tc.is_valid(defects))


class SoloCierreTests(unittest.TestCase):
    """Punto 2: un mensaje que solo lleva las líneas de cierre es un cierre válido."""

    def test_las_dos_lineas_solas(self) -> None:
        self.assertEqual(tc.validate_turn_close(ONLY_CLOSE, manifest=MANIFEST), [])
        self.assertEqual(tc.validate_turn_close("\n" + ONLY_CLOSE + "\n\n", manifest=MANIFEST), [])

    def test_decision_y_bloqueo_en_una_sola_linea(self) -> None:
        for line in ("Siguiente: decisión — responde A (recomendada) o B en este chat",
                     "Siguiente: ninguno — falta la clave de la API en .env",
                     "Siguiente: /ciclo fase\n" + MODEL):
            with self.subTest(line=line):
                self.assertEqual(tc.validate_turn_close(line, manifest=MANIFEST), [])


class HookCase(unittest.TestCase):
    """Proyecto inicializado mínimo y un HOME propio, para no tocar el del usuario."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="avisos_532_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.root = self.tmp / "proyecto"
        (self.root / "_project").mkdir(parents=True)
        (self.root / ".agent" / "workflows").mkdir(parents=True)
        shutil.copy2(ROOT / ".agent" / "workflows" / "manifest.json", self.root / ".agent" / "workflows" / "manifest.json")
        (self.root / "_project" / "03_project_state.json").write_text(
            json.dumps({"framework_mode": "initialized", "module_statuses": {}}), encoding="utf-8")
        self.transcript = self.tmp / "sesion.jsonl"
        self.transcript.write_text(json.dumps({"type": "user", "uuid": "u0", "message": {"content": "hola"}}) + "\n",
                                   encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _event(self, message: str, session: str = "s1", **extra) -> dict:
        return {"session_id": session, "hook_event_name": "Stop", "stop_hook_active": False,
                "last_assistant_message": message, "transcript_path": str(self.transcript), **extra}

    def _checks(self) -> list[dict]:
        path = hook.telemetry_path(self.root)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [r for r in rows if r["event"] == "turn_close_check" and "valid" in r]


class HookTests(HookCase):
    """Puntos 1 y 2 en `turn_close_hook.evaluate`, con los dos hosts."""

    def test_los_avisos_no_bloquean_en_ningun_host(self) -> None:
        for host in hook.HOSTS:
            with self.subTest(host=host):
                self.assertEqual(hook.evaluate(self._event(LONG, session=host), host=host, root=self.root, gycd=False), {})
        checks = self._checks()
        self.assertEqual([(r["host"], r["result"], r["valid"]) for r in checks], [(h, "pass", True) for h in hook.HOSTS])
        for record in checks:
            self.assertEqual(set(record["warnings"]), {tc.INSTRUCCION_LARGA, tc.PROHIBICIONES})

    def test_el_bloqueo_pide_solo_las_lineas_de_cierre(self) -> None:
        for host in hook.HOSTS:
            with self.subTest(host=host):
                out = hook.evaluate(self._event(BAD, session=host), host=host, root=self.root, gycd=False)
                self.assertEqual(out["decision"], "block")
                self.assertIn(ASK_ONLY_CLOSE, out["reason"])
                self.assertIn(NO_REPEAT, out["reason"])
                self.assertIn("SIN_SIGUIENTE", out["reason"])

    def test_la_correccion_corta_pasa_y_cuenta_como_valida(self) -> None:
        # Claude Code marca la continuación con `stop_hook_active`; si un host no lo marcara, la racha del hook basta.
        for host, active in (("claude", True), ("codex", False)):
            with self.subTest(host=host):
                session = f"corta-{host}"
                first = hook.evaluate(self._event(BAD, session=session), host=host, root=self.root, gycd=False)
                self.assertEqual(first["decision"], "block")
                second = hook.evaluate(self._event(ONLY_CLOSE, session=session, stop_hook_active=active),
                                       host=host, root=self.root, gycd=False)
                self.assertEqual(second, {})
        results = [(r["host"], r["result"], r["valid"], r["codes"]) for r in self._checks()]
        self.assertEqual(results, [
            ("claude", "blocked", False, ["SIN_SIGUIENTE"]), ("claude", "passed_after_block", True, []),
            ("codex", "blocked", False, ["SIN_SIGUIENTE"]), ("codex", "passed_after_block", True, []),
        ])


class WiringTests(HookCase):
    """Lo que emiten de verdad la orden de Claude Code y el runner de Codex."""

    def setUp(self) -> None:
        super().setUp()
        scripts = self.root / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("turn_close_hook.py", "turn_close.py", "session_metrics.py", "session_cost.py",
                     "codex_hook_runner.py", "guard_destructive_command.py"):
            shutil.copy2(SCRIPTS / name, scripts / name)
        self.env = {**os.environ, "HOME": str(self.home)}

    def _claude(self, event: dict) -> dict:
        base = json.loads((ROOT / ".agent" / "templates" / "settings.json.base").read_text(encoding="utf-8"))
        (group,) = base["hooks"]["Stop"]
        (entry,) = group["hooks"]
        result = subprocess.run(entry["command"], shell=True, input=json.dumps(event), capture_output=True, text=True,
                                env={**self.env, "CLAUDE_PROJECT_DIR": str(self.root)}, cwd=self.tmp, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def _codex(self, event: dict) -> dict:
        result = subprocess.run([sys.executable, str(self.root / ".agent" / "scripts" / "codex_hook_runner.py"),
                                 "--hook-id", "stop"], input=json.dumps({**event, "cwd": str(self.root)}),
                                capture_output=True, text=True, env=self.env, cwd=self.root, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_claude_con_solo_avisos_no_continua_el_turno(self) -> None:
        # Con avisos la salida es la misma que con un cierre válido: a lo sumo el recordatorio de /gycd.
        long_out = self._claude(self._event(LONG, session="largo"))
        good_out = self._claude(self._event(GOOD, session="bueno"))
        self.assertEqual(long_out, good_out)
        self.assertEqual(list(long_out), ["systemMessage"])
        self.assertEqual(self._claude(self._event(LONG, session="largo")), {})

    def test_codex_con_solo_avisos_no_continua_el_turno(self) -> None:
        long_out = self._codex(self._event(LONG, session="largo"))
        self.assertEqual(long_out, self._codex(self._event(GOOD, session="bueno")))
        self.assertEqual(list(long_out), ["systemMessage"])

    def test_los_dos_hosts_piden_solo_el_cierre_al_bloquear(self) -> None:
        for name, run in (("claude", self._claude), ("codex", self._codex)):
            with self.subTest(host=name):
                out = run(self._event(BAD, session=f"malo-{name}"))
                self.assertEqual(out["decision"], "block")
                self.assertIn(ASK_ONLY_CLOSE, out["reason"])
                self.assertIn(NO_REPEAT, out["reason"])
                # Tras el bloqueo nada vuelve a bloquear: la validez se mira en la telemetría, no en la salida.
                self.assertIsNone(run(self._event(ONLY_CLOSE, session=f"malo-{name}")).get("decision"))
                # Sin bloqueo previo, la corrección corta sola tampoco bloquea: es un cierre válido.
                self.assertIsNone(run(self._event(ONLY_CLOSE, session=f"solo-{name}")).get("decision"))
        checks = [(r["session"], r["result"], r["valid"], r["codes"]) for r in self._checks()]
        self.assertEqual(checks, [
            ("malo-claude", "blocked", False, ["SIN_SIGUIENTE"]), ("malo-claude", "passed_after_block", True, []),
            ("solo-claude", "pass", True, []),
            ("malo-codex", "blocked", False, ["SIN_SIGUIENTE"]), ("malo-codex", "passed_after_block", True, []),
            ("solo-codex", "pass", True, []),
        ])


class NormaTests(unittest.TestCase):
    """Punto 3 y los textos que describen el bloqueo."""

    @staticmethod
    def _flat(rel: str) -> str:
        return " ".join((ROOT / rel).read_text(encoding="utf-8").split())

    def test_arrancar_redacta_el_puntero_largo_sin_mutar(self) -> None:
        text = self._flat(".agent/workflows/arrancar.md")
        self.assertIn("**tal cual** el siguiente paso vigente", text)
        self.assertIn("si pasa de 40 palabras o si lleva prohibiciones", text)
        self.assertIn("una sola acción de hasta 40 palabras con el mismo comando", text)
        self.assertIn("`/arrancar` no muta el estado: el puntero guardado lo reescribe el siguiente `/gycd` (FW-402)", text)
        self.assertIn("(`/arrancar` solo lo redacta: no muta el estado)", self._flat(".agent/skills/decision_translation_core.md"))

    def test_los_adaptadores_y_la_skill_lo_dicen(self) -> None:
        for rel in (".agent/adapters/claude-code.md", ".agent/adapters/codex.md",
                    ".agent/skills/decision_translation_core.md"):
            with self.subTest(rel=rel):
                text = self._flat(rel)
                self.assertIn("solo con las líneas de cierre corregidas", text)
                self.assertIn("sin repetir la respuesta anterior", text)
        for rel in (".agent/adapters/claude-code.md", ".agent/adapters/codex.md"):
            with self.subTest(rel=rel):
                self.assertIn("`INSTRUCCION_LARGA` y `PROHIBICIONES` avisan sin bloquear", self._flat(rel))


if __name__ == "__main__":
    unittest.main()
