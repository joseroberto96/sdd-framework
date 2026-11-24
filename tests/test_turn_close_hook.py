#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hook `Stop` del cierre de turno (5.3.0 L4, ADR-074; FW-314, FW-328, FW-335).

Cubre: cierre válido y defectuoso, un solo bloqueo seguido, fallo abierto,
subagentes, modo plantilla, interruptor `off`, recordatorio de `/gycd` una vez
por sesión, detector de bucles, aviso de sesión larga, telemetría sin contenido
y cero escrituras en el proyecto; y el cableado real: la orden de
`settings.json.base`, el runner de Codex, el fusionador de settings y el doctor.
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ensure_claude_settings as ecs  # noqa: E402
import session_metrics  # noqa: E402
import turn_close_hook as hook  # noqa: E402

MODEL = "Modelo: standard · Esfuerzo: 🟡 medio (tier_1)"
GOOD = "Hecho el login.\n\nSiguiente: /ciclo Implementar el logout en src/auth.py con sus tests\n" + MODEL
BAD = "Hecho el login. Mañana seguimos."
SECRET = "CONTENIDO-PRIVADO-9f3a"
TEST_CMD = "python3 -m unittest tests.test_auth"
RED = "FAIL: test_login (tests.test_auth.AuthTests)\nAssertionError: 1 != 2\nFAILED (failures=1)"
GREEN = "Ran 3 tests in 0.01s\n\nOK"


def _tool_pair(index: int, name: str, tool_input: dict, result: str, *, error: bool = False) -> list[dict]:
    return [
        {"type": "assistant", "uuid": f"a{index}", "message": {"content": [
            {"type": "tool_use", "id": f"t{index}", "name": name, "input": tool_input}]}},
        {"type": "user", "uuid": f"u{index}", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{index}", "content": result, "is_error": error}]}},
    ]


def _test_runs(*outputs: str) -> list[dict]:
    lines: list[dict] = []
    for index, output in enumerate(outputs):
        lines += _tool_pair(index, "Bash", {"command": TEST_CMD}, output, error=output is RED)
    return lines


def _usage(tokens: int, *, sidechain: bool = False) -> dict:
    return {"type": "assistant", "uuid": f"usage{tokens}{sidechain}", "isSidechain": sidechain,
            "message": {"content": [{"type": "text", "text": SECRET}],
                        "usage": {"input_tokens": 10, "cache_read_input_tokens": tokens - 10,
                                  "cache_creation_input_tokens": 0}}}


def _compaction(ident: str) -> dict:
    return {"type": "system", "subtype": "compact_boundary", "uuid": ident}


class HookCase(unittest.TestCase):
    """Proyecto inicializado mínimo y un HOME propio, para no tocar el del usuario."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="turn_close_hook_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.root = self.tmp / "proyecto"
        (self.root / "_project").mkdir(parents=True)
        (self.root / ".agent" / "workflows").mkdir(parents=True)
        shutil.copy2(ROOT / ".agent" / "workflows" / "manifest.json", self.root / ".agent" / "workflows" / "manifest.json")
        self._state("initialized")
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _state(self, mode: str) -> None:
        (self.root / "_project" / "03_project_state.json").write_text(
            json.dumps({"framework_mode": mode, "module_statuses": {"M1_auth": "in_progress"}}), encoding="utf-8")

    def _transcript(self, lines: list[dict]) -> str:
        path = self.tmp / "sesion.jsonl"
        path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        return str(path)

    def _event(self, message: str = GOOD, **extra) -> dict:
        return {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False,
                "last_assistant_message": message, **extra}

    def _run(self, event: dict, *, host: str = "codex", gycd: bool = False) -> dict:
        return hook.evaluate(event, host=host, root=self.root, gycd=gycd)

    def _records(self, event: str | None = None) -> list[dict]:
        path = hook.telemetry_path(self.root)
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if event is None or row["event"] == event]

    def _snapshot(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.root)): p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}


class CloseCheckTests(HookCase):
    def test_cierre_valido_pasa(self) -> None:
        self.assertEqual(self._run(self._event(GOOD)), {})
        (record,) = self._records("turn_close_check")
        self.assertEqual((record["result"], record["valid"], record["codes"]), ("pass", True, []))

    def test_sin_cierre_bloquea_con_el_arreglo(self) -> None:
        out = self._run(self._event(BAD))
        self.assertEqual(out["decision"], "block")
        self.assertIn("SIN_SIGUIENTE", out["reason"])
        self.assertIn("Termina la respuesta con una línea", out["reason"])
        (record,) = self._records("turn_close_check")
        self.assertEqual((record["result"], record["codes"]), ("blocked", ["SIN_SIGUIENTE"]))

    def test_usa_el_validador_unico_en_modo_live(self) -> None:
        import turn_close

        with mock.patch.object(turn_close, "validate_turn_close", wraps=turn_close.validate_turn_close) as spy:
            self._run(self._event(GOOD))
        self.assertEqual(spy.call_args.kwargs["mode"], "live")

    def test_un_aviso_no_bloquea(self) -> None:
        text = "Listo.\n\nSiguiente: /ciclo Aprobar el plan del módulo de pagos\n" + MODEL
        self.assertEqual(self._run(self._event(text)), {})
        (record,) = self._records("turn_close_check")
        self.assertEqual((record["valid"], record["warnings"]), (True, ["DECISION_EN_CICLO"]))

    def test_stop_hook_active_nunca_bloquea(self) -> None:
        self.assertEqual(self._run(self._event(BAD, stop_hook_active=True)), {})
        (record,) = self._records("turn_close_check")
        self.assertEqual((record["result"], record["valid"]), ("passed_after_block", False))

    def test_el_limite_de_un_bloqueo_seguido_es_del_hook(self) -> None:
        """Aunque el arnés no marque `stop_hook_active`, no bloquea dos veces seguidas."""
        # Un mensaje distinto por turno: el mismo mensaje repetido es un duplicado del turno (L5b, FW-355).
        results = [self._run(self._event(f"{BAD} Turno {turn}.")).get("decision") for turn in "ABC"]
        self.assertEqual(results, ["block", None, "block"])
        self.assertEqual([r["result"] for r in self._records("turn_close_check")],
                         ["blocked", "passed_after_block", "blocked"])

    def test_sin_mensaje_falla_abierto(self) -> None:
        event = self._event()
        del event["last_assistant_message"]
        self.assertEqual(self._run(event), {})
        (record,) = self._records("turn_close_check")
        self.assertEqual((record["result"], record["stage"], record["error"]), ("failed_open", "close", "ValueError"))

    def test_excepcion_falla_abierto(self) -> None:
        with mock.patch.object(hook, "_validate_close", side_effect=RuntimeError(SECRET)):
            self.assertEqual(self._run(self._event(BAD)), {})
        (record,) = self._records("turn_close_check")
        self.assertEqual((record["result"], record["error"]), ("failed_open", "RuntimeError"))
        self.assertNotIn(SECRET, hook.telemetry_path(self.root).read_text(encoding="utf-8"))

    def test_fallo_fuera_de_las_etapas_falla_abierto(self) -> None:
        with mock.patch.object(hook, "_evaluate", side_effect=KeyError("x")):
            self.assertEqual(self._run(self._event(BAD)), {})
        (record,) = self._records("turn_close_check")
        self.assertEqual((record["result"], record["stage"]), ("failed_open", "hook"))
        self.assertEqual(hook.evaluate("no es un evento", host="codex", root=self.root), {})  # type: ignore[arg-type]
        self.assertEqual(hook.evaluate(self._event(BAD), host="otro", root=self.root), {})

    def test_tiempo_maximo_falla_abierto(self) -> None:
        with mock.patch.object(hook, "HOOK_TIMEOUT_S", 1), \
                mock.patch.object(hook, "_validate_close", side_effect=lambda *a: time.sleep(5)):
            started = time.monotonic()
            self.assertEqual(self._run(self._event(BAD)), {})
        self.assertLess(time.monotonic() - started, 4)
        (record,) = self._records("turn_close_check")
        self.assertEqual((record["result"], record["stage"], record["error"]), ("failed_open", "hook", "_Timeout"))

    def test_subagente_no_se_evalua(self) -> None:
        self.assertEqual(self._run(self._event(BAD, agent_id="agente-1"), gycd=True), {})
        self.assertEqual(self._run(self._event(BAD, hook_event_name="SubagentStop"), gycd=True), {})
        self.assertEqual(self._records(), [])

    def test_modo_plantilla_no_aplica(self) -> None:
        self._state("template")
        self.assertEqual(self._run(self._event(BAD)), {})
        self.assertEqual(self._records(), [])

    def test_sin_estado_no_aplica(self) -> None:
        (self.root / "_project" / "03_project_state.json").unlink()
        self.assertEqual(self._run(self._event(BAD)), {})
        self.assertEqual(self._records(), [])

    def test_estado_ilegible_falla_abierto(self) -> None:
        (self.root / "_project" / "03_project_state.json").write_text("{no es json", encoding="utf-8")
        self.assertEqual(self._run(self._event(BAD)), {})
        (record,) = self._records()
        self.assertEqual((record["result"], record["stage"]), ("failed_open", "state"))

    def test_off_solo_registra(self) -> None:
        profile = self.root / "_project" / "15_environment_profile.md"
        profile.write_text("# Perfil\n\n- `turn_close_hook`: off\n", encoding="utf-8")
        self.assertEqual(hook.hook_mode(self.root), "off")
        self.assertEqual(self._run(self._event(BAD)), {})
        (record,) = self._records("turn_close_check")
        self.assertEqual((record["result"], record["valid"], record["mode"], record["codes"]),
                         ("pass", False, "off", ["SIN_SIGUIENTE"]))

    def test_el_interruptor_exige_el_valor_exacto(self) -> None:
        profile = self.root / "_project" / "15_environment_profile.md"
        for line in ("- `turn_close_hook`: [on | off] — `off` deja el hook en solo registro", "- turn_close_hook: on",
                     "- turn_close_hook: offline", "Nota: turn_close_hook: off se explica abajo"):
            profile.write_text(f"# Perfil\n\n{line}\n", encoding="utf-8")
            self.assertEqual(hook.hook_mode(self.root), "on", line)
        profile.write_text("- **turn_close_hook**: OFF  # reversión de ADR-074\n", encoding="utf-8")
        self.assertEqual(hook.hook_mode(self.root), "off")
        profile.unlink()
        self.assertEqual(hook.hook_mode(self.root), "on")


class GycdReminderTests(HookCase):
    def test_gycd_una_sola_vez_por_sesion(self) -> None:
        first = self._run(self._event(GOOD), host="claude", gycd=True)
        second = self._run(self._event(GOOD), host="claude", gycd=True)
        other = self._run(self._event(GOOD, session_id="s2"), host="claude", gycd=True)
        self.assertIn("/gycd", first["systemMessage"])
        self.assertNotIn("systemMessage", second)
        self.assertIn("/gycd", other["systemMessage"])

    def test_gycd_tambien_en_modo_plantilla_y_junto_al_bloqueo(self) -> None:
        self._state("template")
        self.assertIn("/gycd", self._run(self._event(BAD), gycd=True)["systemMessage"])
        self._state("initialized")
        out = self._run(self._event(BAD, session_id="s3"), gycd=True)
        self.assertEqual(out["decision"], "block")
        self.assertIn("/gycd", out["systemMessage"])

    def test_el_id_de_sesion_no_escapa_del_directorio(self) -> None:
        self._run(self._event(GOOD, session_id="../../fuera"), gycd=True)
        sessions = hook.telemetry_dir(self.root) / "sessions"
        self.assertEqual([p.name for p in sessions.iterdir()], ["fuera.json"])
        self.assertFalse((self.home / "fuera.json").exists())


class LoopTests(HookCase):
    def _loop_event(self, *outputs: str, **extra) -> dict:
        return self._event(GOOD, transcript_path=self._transcript(_test_runs(*outputs)), **extra)

    def test_bajo_el_umbral_silencio(self) -> None:
        self.assertEqual(self._run(self._loop_event(RED, RED), host="claude"), {})
        self.assertEqual(self._records("loop_detected"), [])

    def test_un_verde_corta_la_cadena(self) -> None:
        self.assertEqual(self._run(self._loop_event(RED, RED, RED, GREEN), host="claude"), {})
        self.assertEqual(self._run(self._loop_event(RED, RED, GREEN, RED), host="claude"), {})

    def test_sobre_el_umbral_bloquea_una_vez(self) -> None:
        event = self._loop_event(RED, RED, RED)
        out = self._run(event, host="claude")
        self.assertEqual(out["decision"], "block")
        self.assertIn("Llevas 3 intentos sobre lo mismo", out["reason"])
        self.assertIn("/reconciliar", out["reason"])
        (record,) = self._records("loop_detected")
        self.assertEqual((record["module"], record["count"], record["kind"], record["blocked"]),
                         ("M1_auth", 3, "test", True))
        # El mismo bucle, con un rojo más y ya sin el límite de un bloqueo seguido: silencio.
        self._run(self._event(GOOD), host="codex")
        self.assertEqual(self._run(self._loop_event(RED, RED, RED, RED), host="claude"), {})
        self.assertEqual(len(self._records("loop_detected")), 1)

    def test_con_stop_hook_active_el_bucle_espera_al_turno_siguiente(self) -> None:
        self.assertEqual(self._run(self._loop_event(RED, RED, RED, stop_hook_active=True), host="claude"), {})
        self.assertEqual(self._records("loop_detected"), [])
        self.assertEqual(self._run(self._loop_event(RED, RED, RED), host="claude")["decision"], "block")

    def test_fichero_editado_y_revertido(self) -> None:
        lines: list[dict] = []
        for index in range(6):
            old, new = ("a", "b") if index % 2 == 0 else ("b", "a")
            lines += _tool_pair(index, "Edit", {"file_path": "src/auth.py", "old_string": old, "new_string": new}, "ok")
        out = self._run(self._event(GOOD, transcript_path=self._transcript(lines)), host="claude")
        self.assertEqual(out["decision"], "block")
        self.assertEqual(self._records("loop_detected")[0]["kind"], "revert")

    def test_cierre_defectuoso_y_bucle_en_un_solo_bloqueo(self) -> None:
        out = self._run(self._event(BAD, transcript_path=self._transcript(_test_runs(RED, RED, RED))), host="claude")
        self.assertIn("SIN_SIGUIENTE", out["reason"])
        self.assertIn("Llevas 3 intentos", out["reason"])
        # 5.3.2 (FW-402): con bucle, la corrección no puede ser «solo» el cierre: también va la frase para el builder.
        self.assertNotIn("Responde solo con", out["reason"])
        self.assertIn("Responde con esa frase y las líneas de cierre corregidas", out["reason"])
        self.assertLess(out["reason"].index("Llevas 3 intentos"), out["reason"].index("Responde con esa frase"))

    def test_off_registra_el_bucle_sin_bloquear(self) -> None:
        (self.root / "_project" / "15_environment_profile.md").write_text("- turn_close_hook: off\n", encoding="utf-8")
        self.assertEqual(self._run(self._loop_event(RED, RED, RED), host="claude"), {})
        self.assertEqual(self._records("loop_detected")[0]["blocked"], False)

    def test_en_codex_no_se_lee_la_transcripcion(self) -> None:
        with mock.patch.object(hook, "_tail_objects", side_effect=AssertionError("no debe leerse")):
            self.assertEqual(self._run(self._loop_event(RED, RED, RED), host="codex"), {})

    def test_la_firma_del_medidor_es_la_misma(self) -> None:
        self.assertEqual(session_metrics.trailing_same([(True, "a"), (True, "b"), (True, "b")]), (2, "b"))
        self.assertEqual(session_metrics.trailing_same([(True, "a"), (False, "")]), (0, ""))
        self.assertEqual(session_metrics.trailing_same([]), (0, ""))


class LongSessionTests(HookCase):
    def _long_event(self, lines: list[dict]) -> dict:
        return self._event(GOOD, transcript_path=self._transcript(lines))

    def test_bajo_el_umbral_silencio(self) -> None:
        self.assertEqual(self._run(self._long_event([_usage(120_000)]), host="claude"), {})
        self.assertEqual(self._records("long_session_warned"), [])

    def test_primera_compactacion_un_aviso_y_despues_silencio(self) -> None:
        event = self._long_event([_compaction("c1"), _usage(60_000)])
        first = self._run(event, host="claude")
        self.assertNotIn("decision", first)
        self.assertIn("ha compactado 1 vez", first["systemMessage"])
        self.assertIn("/gycd", first["systemMessage"])
        self.assertEqual(self._run(event, host="claude"), {})
        (record,) = self._records("long_session_warned")
        self.assertEqual((record["compactions"], record["thresholds"], record["emitted"]), (1, ["compaction"], True))

    def test_cada_compactacion_nueva_avisa_una_vez(self) -> None:
        self._run(self._long_event([_compaction("c1")]), host="claude")
        out = self._run(self._long_event([_compaction("c1"), _compaction("c2")]), host="claude")
        self.assertIn("ha compactado 2 veces", out["systemMessage"])
        # El final de la transcripción ya no trae la primera: la cuenta no retrocede.
        self.assertEqual(self._run(self._long_event([_compaction("c2"), _usage(50_000)]), host="claude"), {})

    def test_contexto_sobre_el_umbral_avisa_una_vez(self) -> None:
        event = self._long_event([_usage(150_000), _usage(999_000, sidechain=True), _usage(231_400)])
        self.assertIn("unos 231 k de contexto", self._run(event, host="claude")["systemMessage"])
        self.assertEqual(self._run(event, host="claude"), {})
        self.assertEqual(self._records("long_session_warned")[0]["context"], 231_400)

    def test_el_contexto_de_un_subagente_no_cuenta(self) -> None:
        event = self._long_event([_usage(100_000), _usage(400_000, sidechain=True)])
        self.assertEqual(self._run(event, host="claude"), {})

    def test_transcripcion_ilegible_falla_abierto_sin_aviso(self) -> None:
        broken = self.tmp / "rota.jsonl"
        broken.write_text("esto no es json\n{tampoco\n", encoding="utf-8")
        for path in (str(broken), str(self.tmp / "no_existe.jsonl"), str(self.tmp), None, "/etc/hosts"):
            with self.subTest(path=path):
                self.assertEqual(self._run(self._event(GOOD, transcript_path=path), host="claude"), {})
        stages = [r["stage"] for r in self._records("turn_close_check") if r["result"] == "failed_open"]
        self.assertEqual(stages, ["transcript"] * 5)
        self.assertEqual(self._records("long_session_warned"), [])
        # El cierre se evaluó igualmente en cada llamada.
        self.assertEqual(len([r for r in self._records("turn_close_check") if r["result"] == "pass"]), 5)

    def test_solo_se_lee_el_final(self) -> None:
        with mock.patch.object(hook, "TAIL_BYTES", 400):
            lines = [_compaction("antigua")] + [_usage(50_000 + n) for n in range(40)]
            self.assertEqual(self._run(self._long_event(lines), host="claude"), {})
        self.assertEqual(self._records("long_session_warned"), [])

    def test_off_registra_sin_avisar(self) -> None:
        (self.root / "_project" / "15_environment_profile.md").write_text("- turn_close_hook: off\n", encoding="utf-8")
        self.assertEqual(self._run(self._long_event([_compaction("c1")]), host="claude"), {})
        self.assertEqual(self._records("long_session_warned")[0]["emitted"], False)


class SafetyTests(HookCase):
    def test_la_telemetria_no_guarda_contenido_y_el_proyecto_no_se_toca(self) -> None:
        before = self._snapshot()
        message = f"{SECRET} [marcador-{SECRET}]\n\nSiguiente: /inventado-{SECRET} hacer {SECRET}\n" + MODEL
        lines = _test_runs(RED, RED, RED) + [_compaction("c1"), _usage(250_000)]
        lines += _tool_pair(99, "Bash", {"command": f"echo {SECRET}"}, SECRET)
        out = self._run(self._event(message, transcript_path=self._transcript(lines), cwd=SECRET), host="claude", gycd=True)
        self.assertEqual(out["decision"], "block")
        self.assertEqual(self._snapshot(), before, "el hook escribió dentro del proyecto")
        directory = hook.telemetry_dir(self.root)
        self.assertTrue(directory.is_relative_to(self.home / ".cache" / "framework-v4"))
        written = [p for p in directory.rglob("*") if p.is_file()]
        self.assertEqual(sorted(p.name for p in written), ["s1.json", "turn_close.jsonl"])
        for path in written:
            self.assertNotIn(SECRET, path.read_text(encoding="utf-8"), path.name)
            self.assertEqual(path.stat().st_mode & 0o077, 0, f"{path.name} legible por otros")
        self.assertEqual({r["event"] for r in self._records()},
                         {"turn_close_check", "loop_detected", "long_session_warned"})

    def test_la_telemetria_tiene_tope(self) -> None:
        with mock.patch.object(hook, "TELEMETRY_MAX_BYTES", 300):
            for _ in range(8):
                self._run(self._event(GOOD))
        directory = hook.telemetry_dir(self.root)
        self.assertTrue((directory / "turn_close.jsonl.1").exists())
        self.assertLess((directory / "turn_close.jsonl").stat().st_size, 700)

    def test_no_sigue_un_enlace_simbolico_en_la_telemetria(self) -> None:
        directory = hook.telemetry_dir(self.root)
        directory.mkdir(parents=True)
        target = self.tmp / "ajeno.txt"
        target.write_text("intacto", encoding="utf-8")
        (directory / "turn_close.jsonl").symlink_to(target)
        self.assertEqual(self._run(self._event(BAD))["decision"], "block")
        self.assertEqual(target.read_text(encoding="utf-8"), "intacto")

    def test_telemetria_imposible_no_rompe_el_turno(self) -> None:
        # Revisión de L4: sin telemetría tampoco hay estado de sesión, así que no bloquea.
        (self.home / ".cache").write_text("no es un directorio", encoding="utf-8")
        self.assertNotIn("decision", self._run(self._event(BAD)))
        self.assertEqual(self._run(self._event(GOOD)), {})

    def test_no_ejecuta_nada_del_mensaje(self) -> None:
        marker = self.tmp / "ejecutado"
        message = f"$(touch {marker}) `touch {marker}`\n\nSiguiente: /ciclo $(touch {marker}); touch {marker}\n" + MODEL
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("subprocess")), \
                mock.patch.object(os, "system", side_effect=AssertionError("os.system")):
            self._run(self._event(message, transcript_path=self._transcript(_test_runs(RED))), host="claude")
        self.assertFalse(marker.exists())


class WiringTests(HookCase):
    """Se ejecuta lo generado: la orden de la plantilla y el runner de Codex (P1)."""

    def _env(self) -> dict[str, str]:
        return {**os.environ, "HOME": str(self.home)}

    def _fixture_scripts(self) -> None:
        scripts = self.root / ".agent" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        for name in ("turn_close_hook.py", "turn_close.py", "session_metrics.py", "session_cost.py",
                     "codex_hook_runner.py", "guard_destructive_command.py"):
            shutil.copy2(SCRIPTS / name, scripts / name)

    def _stop_command(self) -> dict:
        base = json.loads((ROOT / ".agent" / "templates" / "settings.json.base").read_text(encoding="utf-8"))
        (group,) = base["hooks"]["Stop"]
        (entry,) = group["hooks"]
        return entry

    def test_la_orden_de_la_plantilla_bloquea_un_cierre_defectuoso(self) -> None:
        self._fixture_scripts()
        entry = self._stop_command()
        self.assertLess(hook.HOOK_TIMEOUT_S, entry["timeout"])
        env = {**self._env(), "CLAUDE_PROJECT_DIR": str(self.root)}

        def run(event: dict) -> dict:
            result = subprocess.run(entry["command"], shell=True, input=json.dumps(event), capture_output=True,
                                    text=True, env=env, cwd=self.tmp, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout) if result.stdout.strip() else {}

        first = run(self._event(BAD))
        self.assertEqual(first["decision"], "block")
        self.assertIn("/gycd", first["systemMessage"])
        self.assertEqual(run(self._event(GOOD, stop_hook_active=True)), {})
        self.assertNotIn("decision", run({}))  # evento vacío: no hay mensaje que validar, deja pasar
        checks = [r["result"] for r in self._records("turn_close_check") if "valid" in r]
        self.assertEqual(checks, ["blocked", "passed_after_block"])

    def test_la_orden_real_no_evalua_en_la_plantilla(self) -> None:
        entry = self._stop_command()
        env = {**self._env(), "CLAUDE_PROJECT_DIR": str(ROOT)}
        outputs = []
        for _ in range(2):
            result = subprocess.run(entry["command"], shell=True, input=json.dumps(self._event(BAD)),
                                    capture_output=True, text=True, env=env, cwd=self.tmp, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(json.loads(result.stdout) if result.stdout.strip() else {})
        self.assertEqual(list(outputs[0]), ["systemMessage"])
        self.assertEqual(outputs[1], {})

    def test_la_orden_sin_el_script_sale_con_cero(self) -> None:
        """Revisión de L4: un código 2 en `Stop` es un bloqueo; si falta el script, deja pasar (D2)."""
        empty = self.tmp / "sin_script"
        empty.mkdir()
        for project_dir in (str(empty), ""):
            result = subprocess.run(self._stop_command()["command"], shell=True, input=json.dumps(self._event(BAD)),
                                    capture_output=True, text=True, cwd=self.tmp, timeout=30,
                                    env={**self._env(), "CLAUDE_PROJECT_DIR": project_dir})
            self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)

    def test_entrada_basura_sale_con_cero_y_sin_bloquear(self) -> None:
        for raw in ("", "no es json", "[1, 2]", "null"):
            result = subprocess.run([sys.executable, str(SCRIPTS / "turn_close_hook.py"), "--host", "claude"],
                                    input=raw, capture_output=True, text=True, env=self._env(), timeout=30)
            self.assertEqual(result.returncode, 0, raw)
            self.assertNotIn("block", result.stdout, raw)

    def _codex_stop(self, event: dict) -> dict:
        result = subprocess.run([sys.executable, str(self.root / ".agent" / "scripts" / "codex_hook_runner.py"),
                                 "--hook-id", "stop"], input=json.dumps({**event, "cwd": str(self.root)}),
                                capture_output=True, text=True, env=self._env(), cwd=self.root, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_el_runner_de_codex_bloquea_y_conserva_el_recordatorio(self) -> None:
        self._fixture_scripts()
        blocked = self._codex_stop(self._event(BAD))
        self.assertEqual(list(blocked), ["decision", "reason"])
        self.assertIn("SIN_SIGUIENTE", blocked["reason"])
        self.assertIn("Sesión terminada", self._codex_stop(self._event(BAD, stop_hook_active=True))["systemMessage"])
        self.assertIn("Sesión terminada", self._codex_stop(self._event(GOOD))["systemMessage"])
        self.assertEqual({r["host"] for r in self._records()}, {"codex"})

    def test_el_runner_de_codex_sin_el_modulo_deja_pasar(self) -> None:
        self._fixture_scripts()
        (self.root / ".agent" / "scripts" / "turn_close_hook.py").unlink()
        self.assertIn("Sesión terminada", self._codex_stop(self._event(BAD))["systemMessage"])

    def test_el_fusionador_actualiza_la_orden_antigua_en_sitio(self) -> None:
        base = json.loads((ROOT / ".agent" / "templates" / "settings.json.base").read_text(encoding="utf-8"))
        old_command = ("sid=$(python3 -c \"import json,sys;print(json.load(sys.stdin).get('session_id','x'))\"); "
                       "f=\"/tmp/cc_gycd_$sid\"; [ -f \"$f\" ] || echo aviso")
        current = {"permissions": {"allow": ["Bash(ls *)"]}, "hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "mi-hook-de-proyecto.sh"},
                       {"type": "command", "command": old_command, "statusMessage": "viejo"}]}]}}
        merged, changes = ecs.merge_settings(base, current)
        stop_hooks = [h for g in merged["hooks"]["Stop"] for h in g["hooks"]]
        ours = [h for h in stop_hooks if "turn_close_hook.py" in h["command"]]
        self.assertEqual(len(ours), 1, stop_hooks)
        self.assertEqual(ours[0], self._stop_command())
        self.assertFalse([h for h in stop_hooks if "cc_gycd" in h["command"]], "quedó la orden antigua")
        self.assertIn("mi-hook-de-proyecto.sh", json.dumps(merged))
        self.assertEqual(merged["permissions"], current["permissions"])
        self.assertIn("Stop: hook del framework actualizado (cc_gycd)", changes)
        self.assertEqual(ecs.merge_settings(base, merged)[1], [], "no es idempotente")

    def test_la_configuracion_activa_coincide_con_la_plantilla(self) -> None:
        active = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(active["hooks"]["Stop"], [{"hooks": [self._stop_command()]}])
        self.assertNotIn("SubagentStop", active["hooks"])


class SecurityReviewTests(HookCase):
    """Defectos hallados en la revisión `security` de L4, cada uno reproducido en rojo."""

    def test_sin_estado_guardable_no_bloquea(self) -> None:
        """El límite de un bloqueo seguido es del hook: si no puede guardarlo, deja pasar (D2)."""
        (self.home / ".cache").write_text("no es un directorio", encoding="utf-8")
        outputs = [self._run(self._event(BAD)) for _ in range(3)]
        self.assertEqual([out.get("decision") for out in outputs], [None, None, None])
        self.assertIn("no pudo guardar su estado", outputs[0]["systemMessage"])

    def test_no_escribe_estado_si_no_cambio(self) -> None:
        """Un proyecto sin estado y sin recordatorio no deja nada bajo HOME."""
        (self.root / "_project" / "03_project_state.json").unlink()
        self.assertEqual(self._run(self._event(BAD)), {})
        self.assertFalse((self.home / ".cache").exists())

    def test_los_directorios_propios_salen_en_0700(self) -> None:
        self._state("template")  # aquí lo primero que escribe es el estado de sesión
        self._run(self._event(GOOD), gycd=True)
        directory = hook.telemetry_dir(self.root)
        for path in (directory, directory / "sessions"):
            self.assertEqual(path.stat().st_mode & 0o077, 0, f"{path.name} accesible por otros")

    def _old_foreign_file(self) -> Path:
        other = self.tmp / "ajeno"
        other.mkdir()
        old = other / "package.json"
        old.write_text("{}", encoding="utf-8")
        past = time.time() - 40 * 24 * 3600
        os.utime(old, (past, past))
        return old

    def test_sessions_enlazado_no_borra_ni_escribe_fuera(self) -> None:
        old = self._old_foreign_file()
        directory = hook.telemetry_dir(self.root)
        directory.mkdir(parents=True)
        (directory / "sessions").symlink_to(old.parent, target_is_directory=True)
        self.assertNotIn("decision", self._run(self._event(BAD)))
        self.assertEqual([p.name for p in old.parent.iterdir()], ["package.json"])

    def test_carpeta_del_proyecto_enlazada_no_escribe_fuera(self) -> None:
        old = self._old_foreign_file()
        directory = hook.telemetry_dir(self.root)
        directory.parent.mkdir(parents=True)
        directory.symlink_to(old.parent, target_is_directory=True)
        self.assertNotIn("decision", self._run(self._event(BAD)))
        self.assertEqual([p.name for p in old.parent.iterdir()], ["package.json"])

    def test_el_estado_de_sesion_no_se_lee_por_un_enlace(self) -> None:
        sessions = hook.telemetry_dir(self.root) / "sessions"
        sessions.mkdir(parents=True)
        foreign = self.tmp / "ajeno.json"
        foreign.write_text(json.dumps({"streak": 1, "dato": SECRET}), encoding="utf-8")
        (sessions / "s1.json").symlink_to(foreign)
        self.assertEqual(self._run(self._event(BAD))["decision"], "block")  # no heredó `streak` del enlace
        self.assertNotIn(SECRET, (sessions / "s1.json").read_text(encoding="utf-8"))

    def test_salida_con_stdout_que_no_es_utf8(self) -> None:
        scripts = self.root / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("turn_close_hook.py", "turn_close.py", "session_metrics.py", "session_cost.py"):
            shutil.copy2(SCRIPTS / name, scripts / name)
        result = subprocess.run([sys.executable, str(scripts / "turn_close_hook.py"), "--host", "claude"],
                                input=json.dumps(self._event(BAD)), capture_output=True, text=True, timeout=30,
                                env={**os.environ, "HOME": str(self.home), "PYTHONIOENCODING": "ascii"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SIN_SIGUIENTE", json.loads(result.stdout)["reason"])

    def test_sin_home_resoluble_no_lanza(self) -> None:
        with mock.patch.object(Path, "home", side_effect=RuntimeError("sin home")):
            self.assertNotIn("decision", self._run(self._event(BAD)))

    def test_el_motivo_del_bloqueo_tiene_tope(self) -> None:
        out = self._run(self._event("x\n\nSiguiente: /" + "A" * 200_000 + " [" + "B" * 200_000 + "]"))
        self.assertEqual(out["decision"], "block")
        self.assertLess(len(out["reason"]), 2000)

    def test_un_comando_largo_en_la_transcripcion_no_agota_el_tiempo(self) -> None:
        lines = _tool_pair(1, "Bash", {"command": "python3 decode.py " + "A" * 200_000}, "ok")
        started = time.monotonic()
        out = self._run(self._event(BAD, transcript_path=self._transcript(lines)), host="claude")
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(out["decision"], "block")

    def test_el_interruptor_ignora_ejemplos_y_gana_la_ultima_declaracion(self) -> None:
        """FW-350: un `off` dentro de un bloque de código o de un comentario no apaga el hook."""
        profile = self.root / "_project" / "15_environment_profile.md"
        for text in ("Ejemplo:\n\n```\n- turn_close_hook: off\n```\n",
                     "~~~text\nturn_close_hook: off\n~~~\n",
                     "<!--\nturn_close_hook: off\n-->\n",
                     "- turn_close_hook: off\n- turn_close_hook: on\n"):
            profile.write_text(text, encoding="utf-8")
            self.assertEqual(hook.hook_mode(self.root), "on", text)
        profile.write_text("```\nturn_close_hook: on\n```\n- turn_close_hook: on\n- `turn_close_hook`: off\n", encoding="utf-8")
        self.assertEqual(hook.hook_mode(self.root), "off")

    def test_un_resumen_con_cero_fallos_no_es_un_rojo(self) -> None:
        """FW-351: tres pasadas en verde que imprimen «0 failed» no son un bucle."""
        green = "running 12 tests\ntest result: ok. 12 passed; 0 failed; 0 ignored; 0 errors"
        lines: list[dict] = []
        for index in range(3):
            lines += _tool_pair(index, "Bash", {"command": "cargo test"}, green)
        self.assertEqual(self._run(self._event(GOOD, transcript_path=self._transcript(lines)), host="claude"), {})
        self.assertTrue(session_metrics._FAIL_RE.search("test result: FAILED. 10 passed; 2 failed"))
        self.assertTrue(session_metrics._FAIL_RE.search("Tests: 10 failed, 2 passed"))

    def test_un_bucle_que_ya_quedo_atras_no_corta(self) -> None:
        """FW-351: la racha roja debe ser lo último que se ejecutó."""
        lines = _test_runs(RED, RED, RED)
        lines += _tool_pair(50, "Bash", {"command": "python3 -m unittest tests.test_otro"}, GREEN)
        self.assertEqual(self._run(self._event(GOOD, transcript_path=self._transcript(lines)), host="claude"), {})
        lines += _tool_pair(51, "Bash", {"command": TEST_CMD}, RED, error=True)
        out = self._run(self._event(GOOD, transcript_path=self._transcript(lines), session_id="s2"), host="claude")
        self.assertEqual(out.get("decision"), "block", "si el mismo test vuelve a fallar, la racha sigue viva")


class SecondReviewTests(HookCase):
    """Defectos hallados en la segunda lectura de L4 (otro proveedor, D11), cada uno en rojo antes."""

    def test_el_tiempo_agotado_al_registrar_no_se_pierde(self) -> None:
        """Si la alarma salta mientras se escribe telemetría, el hook deja pasar y lo registra."""
        event = self._event(BAD, transcript_path=self._transcript([_compaction("c1")]))
        write = os.write
        pending = [True]

        def slow(fd: int, data: bytes) -> int:
            if pending[0] and b"long_session_warned" in data:
                pending[0] = False
                time.sleep(5)
            return write(fd, data)

        with mock.patch.object(hook, "HOOK_TIMEOUT_S", 1), mock.patch.object(os, "write", slow):
            out = self._run(event, host="claude")
        self.assertNotIn("decision", out)
        self.assertIn("_Timeout", [r.get("error") for r in self._records("turn_close_check")])

    def test_un_estado_escrito_a_medias_no_cuenta_como_guardado(self) -> None:
        write = os.write
        pending = [True]

        def short(fd: int, data: bytes) -> int:
            if pending[0] and b'"streak"' in data:
                pending[0] = False
                return write(fd, data[:1])
            return write(fd, data)

        with mock.patch.object(os, "write", short):
            out = self._run(self._event(BAD))
        self.assertNotIn("decision", out)
        self.assertIn("no pudo guardar su estado", out["systemMessage"])
        sessions = hook.telemetry_dir(self.root) / "sessions"
        self.assertEqual([p.name for p in sessions.iterdir()], [], "ni estado a medias ni temporal")

    def test_no_poder_guardar_tras_un_bloqueo_se_registra_y_se_avisa(self) -> None:
        """Con la racha sin poder volver a cero el hook queda sin efecto: no puede ser en silencio."""
        self.assertEqual(self._run(self._event(BAD))["decision"], "block")
        with mock.patch.object(hook, "_save_session", return_value=False):
            out = self._run(self._event(BAD + " Otro turno."))  # mismo mensaje sería un duplicado (L5b, FW-355)
        self.assertNotIn("decision", out)
        self.assertIn("no pudo guardar su estado", out["systemMessage"])
        stages = [r.get("stage") for r in self._records("turn_close_check") if r["result"] == "failed_open"]
        self.assertEqual(stages, ["session"])

    def test_un_estado_de_sesion_con_tipos_erroneos_se_descarta(self) -> None:
        loops = self._transcript(_test_runs(RED, RED, RED))
        for index, bad in enumerate(({"loops": None}, {"loops": "abc", "streak": "1"},
                                     {"compact_seen": 7, "compact_warned": "x", "loops": [1, None]})):
            with self.subTest(bad=bad):
                session_id = f"p{index}"
                self.assertTrue(hook._save_session(self.root, session_id, bad))
                out = self._run(self._event(GOOD, session_id=session_id, transcript_path=loops), host="claude")
                self.assertEqual(out.get("decision"), "block")
        errors = [r for r in self._records("turn_close_check") if r["result"] == "failed_open"]
        self.assertEqual(errors, [])

    def test_el_interruptor_distingue_codigo_y_comentarios(self) -> None:
        profile = self.root / "_project" / "15_environment_profile.md"
        cases = (
            ("    turn_close_hook: off\n", "on"),  # sangría de código
            ("\tturn_close_hook: off\n", "on"),
            ("````markdown\n```\nturn_close_hook: off\n```\n````\n", "on"),  # valla dentro de valla
            ("````\n```\nturn_close_hook: off\n", "on"),  # una valla corta no cierra una larga
            ("```\n~~~\nturn_close_hook: off\n", "on"),
            ("```text\n<!--\n```\nturn_close_hook: off\n<!-- fin -->\n", "off"),  # el comentario de una valla no cuenta
            ("~~~text\n<!--\n~~~\nturn_close_hook: off\n", "off"),
            ("<!-- nota --> turn_close_hook: off\n", "off"),
            ("<!--\n```\n-->\nturn_close_hook: off\n", "off"),  # la valla de un comentario no cuenta
            ("  - turn_close_hook: off\n", "off"),
        )
        for text, expected in cases:
            profile.write_text(text, encoding="utf-8")
            self.assertEqual(hook.hook_mode(self.root), expected, text)


class DoctorTests(unittest.TestCase):
    def test_version_ilegible_del_arnes_no_rompe_el_doctor(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="arnes_falso_"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        fake = folder / "claude"
        fake.write_text('#!/bin/sh\nprintf "\\377\\376 1.0.0\\n"\n', encoding="utf-8")
        fake.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{folder}{os.pathsep}{os.environ.get('PATH', '')}"}):
            self.assertIn(self.doctor._harness_version("claude"), (None, (1, 0, 0)))

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("framework_doctor_l4", SCRIPTS / "framework_doctor.py")
        cls.doctor = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.doctor
        spec.loader.exec_module(cls.doctor)

    def test_el_cableado_real_no_da_errores(self) -> None:
        errors: list[str] = []
        self.doctor.validate_wiring_feature("turn_close_hook", None, errors)
        self.assertEqual(errors, [])

    def test_version_anterior_del_arnes_avisa_sin_fallar(self) -> None:
        versions = {"claude": (2, 1, 288), "codex": (0, 162, 1)}
        errors: list[str] = []
        warnings: list[str] = []
        with mock.patch.object(self.doctor, "_harness_version", side_effect=lambda binary: versions[binary]):
            self.doctor.validate_turn_close_hook("template", errors, warnings)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("Claude Code 2.1.288", warnings[0])
        self.assertIn(hook.VERIFIED_HARNESS["claude"], warnings[0])

    def test_arnes_ausente_o_al_dia_no_avisa(self) -> None:
        for versions in ({"claude": None, "codex": None}, {"claude": (2, 1, 296), "codex": (0, 163, 0)}):
            warnings: list[str] = []
            with mock.patch.object(self.doctor, "_harness_version", side_effect=lambda binary, v=versions: v[binary]):
                self.doctor.validate_turn_close_hook("template", [], warnings)
            self.assertEqual(warnings, [])

    def test_el_doctor_avisa_si_el_hook_esta_apagado(self) -> None:
        """FW-350: el modo `off` deja rastro en la telemetría, pero el builder debe verlo."""
        for mode, expected in (("off", 1), ("on", 0)):
            warnings: list[str] = []
            with mock.patch.object(self.doctor, "_harness_version", return_value=None), \
                    mock.patch.object(hook, "hook_mode", return_value=mode):
                self.doctor.validate_turn_close_hook("initialized", [], warnings)
            self.assertEqual(len([w for w in warnings if "turn_close_hook: off" in w]), expected, warnings)

    def test_el_nucleo_respeta_su_tope(self) -> None:
        """FW-353: el tope es el de 6 KiB que fijó el builder en L2, no el tamaño de L3."""
        size = len((ROOT / ".agent" / "rules" / "runtime_core.md").read_bytes())
        self.assertLessEqual(size, 6144, "runtime_core.md supera su tope de 6 KiB")


class L5bHardeningTests(HookCase):
    """5.3.0 L5b (FW-352): guardado con descriptores, cerrojo por sesión, adaptadores y limpieza de la caché."""

    def _own_telemetry(self) -> Path:
        directory = hook.telemetry_dir(self.root)
        directory.mkdir(parents=True, mode=0o700)
        return directory

    def _own_sessions(self) -> Path:
        sessions = self._own_telemetry() / "sessions"
        sessions.mkdir(mode=0o700)
        return sessions

    def _outside(self, text: str = "intacto") -> Path:
        outside = self.tmp / "ajeno.txt"
        outside.write_text(text, encoding="utf-8")
        return outside

    # ---- Punto 1: guardado del estado de sesión

    def test_un_enlace_duro_en_el_temporal_antiguo_no_toca_el_fichero_enlazado(self) -> None:
        sessions = self._own_sessions()
        outside = self._outside()
        os.link(outside, sessions / "s1.json.tmp")
        self.assertEqual(self._run(self._event(BAD))["decision"], "block")
        self.assertEqual(outside.read_text(encoding="utf-8"), "intacto")

    def test_un_enlace_duro_en_el_estado_de_sesion_no_se_lee_ni_se_modifica(self) -> None:
        sessions = self._own_sessions()
        outside = self._outside('{"streak": 1}')
        os.link(outside, sessions / "s1.json")
        # El estado enlazado no es del hook: no hereda su racha, así que bloquea, y no lo reescribe.
        self.assertEqual(self._run(self._event(BAD))["decision"], "block")
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"streak": 1}')
        saved = json.loads((sessions / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["streak"], sorted(saved)), (1, ["streak", "turn"]))  # `turn`: la huella del turno (FW-355)
        self.assertEqual(os.stat(sessions / "s1.json").st_nlink, 1)

    def test_un_enlace_duro_en_la_telemetria_no_se_escribe_ni_se_rota(self) -> None:
        for limit in (None, 3):
            with self.subTest(limit=limit):
                directory = hook.telemetry_dir(self.root)
                shutil.rmtree(directory, ignore_errors=True)
                directory.mkdir(parents=True, mode=0o700)
                outside = self._outside("intacto\n")
                (directory / "turn_close.jsonl").unlink(missing_ok=True)
                os.link(outside, directory / "turn_close.jsonl")
                with mock.patch.object(hook, "TELEMETRY_MAX_BYTES", limit or hook.TELEMETRY_MAX_BYTES):
                    self.assertEqual(self._run(self._event(BAD, session_id=f"t{limit}"))["decision"], "block")
                self.assertEqual(outside.read_text(encoding="utf-8"), "intacto\n")
                self.assertFalse((directory / "turn_close.jsonl.1").exists(), "rotó un fichero que no era suyo")
                outside.unlink()

    def test_sessions_sustituida_entre_la_comprobacion_y_el_uso_no_redirige_la_escritura(self) -> None:
        self._own_sessions()
        directory = hook.telemetry_dir(self.root)
        sessions, moved = directory / "sessions", directory / "sessions_original"
        foreign = self.tmp / "carpeta_ajena"
        foreign.mkdir()
        real_open = os.open
        swapped: list[bool] = []

        def swap_then_open(path, flags, *args, **kwargs):
            """Justo antes de crear el temporal (ya validada la carpeta) la sustituye por un enlace."""
            if not swapped and flags & os.O_CREAT and os.fspath(path).endswith(".tmp"):
                swapped.append(True)
                sessions.rename(moved)
                sessions.symlink_to(foreign, target_is_directory=True)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(os, "open", swap_then_open):
            out = self._run(self._event(BAD))
        self.assertEqual(swapped, [True])
        self.assertEqual(list(foreign.iterdir()), [], "escribió a través del enlace")
        self.assertEqual(out["decision"], "block")
        self.assertTrue((moved / "s1.json").is_file(), "el estado debía quedar en la carpeta ya abierta")

    def test_dos_invocaciones_simultaneas_de_la_misma_sesion_bloquean_una_sola_vez(self) -> None:
        second_waiting = threading.Event()
        calls: list[int] = []
        real_acquire, real_validate = hook._acquire_lock, hook._validate_close

        def acquire(sessions_fd, name):
            calls.append(1)
            if len(calls) == 2:
                second_waiting.set()  # la segunda ya está esperando el cerrojo
            return real_acquire(sessions_fd, name)

        def hold_until_second_waits(event, root):
            """La primera decide solo cuando la segunda ya está esperando: sin cerrojo ambas bloquean."""
            second_waiting.wait(timeout=30)
            return real_validate(event, root)

        outputs: list[dict] = []
        threads = [threading.Thread(target=lambda: outputs.append(self._run(self._event(BAD)))) for _ in range(2)]
        with mock.patch.object(hook, "_validate_close", hold_until_second_waits), \
                mock.patch.object(hook, "_acquire_lock", acquire):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        self.assertEqual(sorted(out.get("decision", "-") for out in outputs), ["-", "block"])
        self.assertEqual(sorted(r["result"] for r in self._records("turn_close_check")), ["blocked", "passed_after_block"])
        self.assertNotIn("decision", self._run(self._event(BAD)))  # un tercer intento del mismo turno tampoco bloquea

    def test_cerrojo_inalcanzable_no_bloquea_y_se_registra(self) -> None:
        sessions = self._own_sessions()
        holder = os.open(sessions / "s1.lock", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        with mock.patch.object(hook, "LOCK_WAIT_S", 0.3, create=True):
            out = self._run(self._event(BAD))
        self.assertNotIn("decision", out)
        self.assertIn("Otra invocación", out["systemMessage"])
        self.assertNotIn("permisos", out["systemMessage"])
        self.assertEqual([r.get("stage") for r in self._records("turn_close_check") if r["result"] == "failed_open"], ["lock"])
        fcntl.flock(holder, fcntl.LOCK_UN)
        self.assertEqual(self._run(self._event(BAD))["decision"], "block")

    def test_sin_fcntl_degrada_sin_cerrojo_y_sin_romper(self) -> None:
        with mock.patch.object(hook, "fcntl", None, create=True):
            results = [self._run(self._event(BAD)).get("decision") for _ in range(2)]
        self.assertEqual(results, ["block", None])

    def test_el_cerrojo_se_toma_solo_donde_ya_se_escribe_estado(self) -> None:
        """H3: un proyecto en modo plantilla y sin recordatorio no deja nada bajo HOME."""
        self._state("template")
        self.assertEqual(self._run(self._event(GOOD), gycd=False), {})
        self.assertFalse((self.home / ".cache").exists())
        self.assertEqual(self._run(self._event(GOOD, agent_id="a1"), gycd=True), {})
        self.assertFalse((self.home / ".cache").exists())

    def test_el_cerrojo_no_se_queda_en_la_carpeta(self) -> None:
        self._run(self._event(BAD))
        self.assertEqual(sorted(p.name for p in (hook.telemetry_dir(self.root) / "sessions").iterdir()), ["s1.json"])

    def test_la_poda_alcanza_cerrojos_y_temporales_huerfanos_sin_seguir_enlaces(self) -> None:
        sessions = self._own_sessions()
        past = time.time() - 40 * 24 * 3600
        for name in ("viejo.json", "viejo.lock", "viejo.json.ab12cd34ef56.tmp", "viejo.json.tmp"):
            (sessions / name).write_text("{}", encoding="utf-8")
            os.utime(sessions / name, (past, past))
        for name in ("reciente.lock", "reciente.json.ab12cd34ef56.tmp", "otro.txt"):
            (sessions / name).write_text("{}", encoding="utf-8")
        old_target = self._outside()
        os.utime(old_target, (past, past))
        (sessions / "enlace.lock").symlink_to(old_target)
        os.utime(sessions / "enlace.lock", (past, past), follow_symlinks=False)
        self.assertEqual(self._run(self._event(BAD, session_id="nueva"))["decision"], "block")
        self.assertEqual(sorted(p.name for p in sessions.iterdir()),
                         ["enlace.lock", "nueva.json", "otro.txt", "reciente.json.ab12cd34ef56.tmp", "reciente.lock"])
        self.assertEqual(old_target.read_text(encoding="utf-8"), "intacto")

    # ---- Punto 4: adaptadores

    def test_los_adaptadores_citan_la_ruta_de_telemetria_con_la_huella(self) -> None:
        for name in ("claude-code.md", "codex.md"):
            text = (ROOT / ".agent" / "adapters" / name).read_text(encoding="utf-8")
            self.assertIn("~/.cache/framework-v4/<proyecto>-<huella>/turn_close.jsonl", text, name)
            self.assertNotIn("framework-v4/<proyecto>/", text, name)

    # ---- Punto 6: limpieza de la caché a petición

    def _cache(self) -> Path:
        return self.home / ".cache" / "framework-v4"

    def _folder(self, name: str, files: dict[str, str] | None = None, *, age_days: float = 200) -> Path:
        folder = self._cache() / name
        for relative, text in (files if files is not None else {"turn_close.jsonl": "{}\n", "sessions/s1.json": "{}"}).items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        folder.mkdir(parents=True, exist_ok=True)
        past = time.time() - age_days * 24 * 3600
        for path in sorted(folder.rglob("*"), reverse=True):
            os.utime(path, (past, past), follow_symlinks=False)
        os.utime(folder, (past, past))
        return folder

    def test_la_limpieza_lista_en_ensayo_y_borra_con_apply(self) -> None:
        old = self._folder("viejo-0123456789ab")
        dry = hook.prune_cache(root=self.root)
        self.assertEqual(dry["candidates"], ["viejo-0123456789ab"])
        self.assertEqual(dry["deleted"], [])
        self.assertTrue(old.is_dir(), "el ensayo no borra")
        with mock.patch.object(shutil, "rmtree", side_effect=AssertionError("rmtree")):
            done = hook.prune_cache(root=self.root, apply=True)
        self.assertEqual(done["deleted"], ["viejo-0123456789ab"])
        self.assertFalse(old.exists())

    def test_la_limpieza_respeta_la_antiguedad_pedida(self) -> None:
        folder = self._folder("medio-0123456789ab", age_days=20)
        self.assertEqual(hook.prune_cache(root=self.root)["candidates"], [])
        self.assertEqual(hook.prune_cache(root=self.root, days=10)["candidates"], ["medio-0123456789ab"])
        self.assertTrue(folder.is_dir())

    def test_la_limpieza_no_toca_lo_reciente_ni_el_proyecto_en_curso(self) -> None:
        recent = self._folder("reciente-0123456789ab", age_days=3)
        current = self._folder(hook.telemetry_dir(self.root).name)
        mixed = self._folder("mixta-0123456789ab")
        (mixed / "sessions" / "s2.json").write_text("{}", encoding="utf-8")  # una entrada reciente salva la carpeta
        result = hook.prune_cache(root=self.root, apply=True)
        self.assertEqual(result["deleted"], [])
        for folder in (recent, current, mixed):
            self.assertTrue(folder.is_dir(), folder.name)

    def test_la_limpieza_deja_la_carpeta_con_algo_desconocido(self) -> None:
        cases = {
            "fichero-0123456789ab": {"turn_close.jsonl": "{}", "notas.txt": "mio"},
            "subdir-0123456789ab": {"turn_close.jsonl": "{}", "otra/dentro.json": "{}"},
            "sesion-0123456789ab": {"sessions/s1.json": "{}", "sessions/hondo/x.json": "{}"},
            "rara-0123456789ab": {"sessions/s1.json": "{}", "sessions/notas.txt": "mio"},
            "humo-0123456789ab": {"smoke/run.jsonl": "{}", "smoke/run.txt": "mio"},
        }
        folders = [self._folder(name, files) for name, files in cases.items()]
        result = hook.prune_cache(root=self.root, apply=True)
        self.assertEqual((result["candidates"], result["deleted"], result["skipped"]), ([], [], len(cases)))
        for folder in folders:
            self.assertTrue(folder.is_dir(), folder.name)
        self.assertEqual((folder / "smoke" / "run.txt").read_text(encoding="utf-8"), "mio")

    def test_la_limpieza_no_sigue_enlaces(self) -> None:
        target = self.tmp / "destino"
        target.mkdir()
        (target / "dato.txt").write_text("intacto", encoding="utf-8")
        old = time.time() - 300 * 24 * 3600
        os.utime(target, (old, old))
        self._cache().mkdir(parents=True)
        (self._cache() / "enlace-0123456789ab").symlink_to(target, target_is_directory=True)
        inner = self._folder("dentro-0123456789ab", {"sessions/s1.json": "{}"})
        (inner / "sessions" / "s2.json").symlink_to(target / "dato.txt")
        os.utime(inner / "sessions" / "s2.json", (old, old), follow_symlinks=False)
        result = hook.prune_cache(root=self.root, apply=True)
        self.assertEqual(result["deleted"], [])
        self.assertTrue((self._cache() / "enlace-0123456789ab").is_symlink())
        self.assertEqual((target / "dato.txt").read_text(encoding="utf-8"), "intacto")
        self.assertTrue(inner.is_dir())

    def test_la_limpieza_ignora_nombres_ajenos(self) -> None:
        names = ("sin-huella", "proyecto-0123456789AB", "proyecto-0123456789a", "proyecto-0123456789abc",
                 "con espacio-0123456789ab", "-0123456789ab", "x" * 41 + "-0123456789ab")
        folders = [self._folder(name) for name in names]
        (self._cache() / "suelto-0123456789ab").write_text("fichero", encoding="utf-8")
        result = hook.prune_cache(root=self.root, apply=True)
        self.assertEqual(result["candidates"], [])
        for folder in folders:
            self.assertTrue(folder.is_dir(), folder.name)

    def test_la_limpieza_admite_smoke_y_temporales_del_hook(self) -> None:
        self._folder("completa-0123456789ab", {
            "turn_close.jsonl": "{}", "turn_close.jsonl.1": "{}", "sessions/s1.json": "{}", "sessions/s1.lock": "",
            "sessions/s1.json.ab12cd34ef56.tmp": "{}", "sessions/s1.json.tmp": "{}", "smoke/abc.jsonl": "{}"})
        self.assertEqual(hook.prune_cache(root=self.root, apply=True)["deleted"], ["completa-0123456789ab"])
        self.assertEqual(list(self._cache().iterdir()), [])

    def test_la_limpieza_acota_el_recorrido(self) -> None:
        for index in range(6):
            self._folder(f"p{index}-0123456789ab")
        result = hook.prune_cache(root=self.root, apply=True, limit=8)
        self.assertTrue(result["truncated"])
        self.assertLess(len(result["deleted"]), 6)

    def test_la_orden_de_mantenimiento_es_a_peticion_y_solo_da_nombres(self) -> None:
        old = self._folder("viejo-0123456789ab", {"turn_close.jsonl": "CONTENIDO-PRIVADO", "sessions/s1.json": "{}"})
        script = [sys.executable, str(SCRIPTS / "turn_close_hook.py")]
        env = {**os.environ, "HOME": str(self.home)}
        dry = subprocess.run([*script, "--prune-cache", "--days", "30"], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("viejo-0123456789ab", dry.stdout)
        self.assertIn("--apply", dry.stdout)
        self.assertNotIn("CONTENIDO-PRIVADO", dry.stdout)
        self.assertTrue(old.is_dir())
        done = subprocess.run([*script, "--prune-cache", "--days", "30", "--apply"], capture_output=True, text=True,
                              env=env, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(old.exists())
        # Sin --prune-cache sigue exigiendo el host, y el hook no llama a la limpieza.
        missing = subprocess.run(script, input="{}", capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(missing.returncode, 2)
        self.assertNotIn("prune_cache", (SCRIPTS / "codex_hook_runner.py").read_text(encoding="utf-8"))
        with mock.patch.object(hook, "prune_cache") as spy:
            self.assertEqual(self._run(self._event(GOOD)), {})
            self.assertEqual(self._run(self._event(BAD, session_id="s9"))["decision"], "block")
        spy.assert_not_called()


class L5bCacheDoctorTests(HookCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("framework_doctor_l5b", SCRIPTS / "framework_doctor.py")
        cls.doctor = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.doctor
        spec.loader.exec_module(cls.doctor)

    def _warnings(self) -> list[str]:
        warnings: list[str] = []
        self.doctor.validate_turn_close_cache(warnings)
        return warnings

    def test_validate_turn_close_hook_no_lee_la_cache(self) -> None:
        """Los tests y usos de `validate_turn_close_hook` no dependen del HOME de quien los lanza."""
        cache = self.home / ".cache" / "framework-v4"
        for index in range(51):
            (cache / f"p{index}-0123456789ab").mkdir(parents=True)
        warnings: list[str] = []
        with mock.patch.object(self.doctor, "_harness_version", return_value=None):
            self.doctor.validate_turn_close_hook("template", [], warnings)
        self.assertEqual([w for w in warnings if "framework-v4" in w], [])
        self.assertEqual(len(self._warnings()), 1)

    def test_el_doctor_llama_al_aviso_de_la_cache_desde_main(self) -> None:
        source = (SCRIPTS / "framework_doctor.py").read_text(encoding="utf-8")
        self.assertIn("validate_turn_close_cache(warnings)", source.split("validate_turn_close_hook(mode, errors, warnings)")[1][:200])

    def test_avisa_por_encima_del_umbral_y_calla_por_debajo(self) -> None:
        cache = self.home / ".cache" / "framework-v4"
        for index in range(50):
            (cache / f"p{index}-0123456789ab").mkdir(parents=True)
        self.assertEqual(self._warnings(), [])
        (cache / "p50-0123456789ab").mkdir()
        (cache / "no es nuestra").mkdir()
        (cache / "suelto.txt").write_text("x", encoding="utf-8")
        (warning,) = self._warnings()
        self.assertIn("51 carpetas", warning)
        self.assertIn("python3 .agent/scripts/turn_close_hook.py --prune-cache", warning)

    def test_calla_sin_carpeta_o_sin_home_resoluble(self) -> None:
        self.assertEqual(self._warnings(), [])
        with mock.patch.object(Path, "home", side_effect=RuntimeError("sin home")):
            self.assertEqual(self._warnings(), [])


class L5bRound2Tests(HookCase):
    """5.3.0 L5b, ronda 2 (revisión security): cada defecto reproducido en rojo antes de corregirlo."""

    def _evaluate_in_subprocess(self, *, timeout: float = 10) -> tuple[float, str]:
        """evaluate() en otro proceso: un `open` que se cuelga no puede colgar el test."""
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import turn_close_hook as h; "
                "print(json.dumps(h.evaluate(json.loads(sys.argv[2]), host='codex', root=sys.argv[3], gycd=False)))")
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), json.dumps(self._event(BAD)), str(self.root)],
                                    env={**os.environ, "HOME": str(self.home)}, capture_output=True, text=True,
                                    timeout=timeout)
        except subprocess.TimeoutExpired:
            self.fail(f"el hook se colgó más de {timeout} s")
        self.assertEqual(result.returncode, 0, result.stderr)
        return time.monotonic() - started, result.stdout

    # S4a
    def test_un_cerrojo_recien_liberado_se_reintenta_y_no_se_abandona(self) -> None:
        fd = hook._open_sessions_dir(self.root, create=True)
        self.addCleanup(os.close, fd)
        real = hook.is_own_regular_file
        raced: list[bool] = []

        def release_once(candidate: int) -> bool:
            """Quien tenía el cerrojo lo borra justo entre el open y la comprobación del que espera."""
            if not raced:
                raced.append(True)
                os.unlink("s1.lock", dir_fd=fd)
            return real(candidate)

        with mock.patch.object(hook, "is_own_regular_file", release_once):
            lock = hook._acquire_lock(fd, "s1.lock")
        self.assertIsNotNone(lock, "debía reintentar dentro del plazo")
        os.close(lock)

    def test_un_cerrojo_con_otro_enlace_o_que_no_es_regular_se_rechaza(self) -> None:
        fd = hook._open_sessions_dir(self.root, create=True)
        self.addCleanup(os.close, fd)
        outside = self.tmp / "ajeno.txt"
        outside.write_text("intacto", encoding="utf-8")
        os.link(outside, hook.telemetry_dir(self.root) / "sessions" / "duro.lock")
        os.mkfifo(hook.telemetry_dir(self.root) / "sessions" / "fifo.lock")
        started = time.monotonic()
        self.assertIsNone(hook._acquire_lock(fd, "duro.lock"))
        self.assertIsNone(hook._acquire_lock(fd, "fifo.lock"))
        self.assertLess(time.monotonic() - started, 1)

    # S4b
    def test_la_poda_no_borra_un_cerrojo_tomado_aunque_sea_viejo(self) -> None:
        fd = hook._open_sessions_dir(self.root, create=True)
        self.addCleanup(os.close, fd)
        os.close(os.open("s1.lock", os.O_CREAT | os.O_RDWR, 0o600, dir_fd=fd))
        old = time.time() - 40 * 24 * 3600
        os.utime("s1.lock", (old, old), dir_fd=fd)
        first = hook._acquire_lock(fd, "s1.lock")
        self.assertIsNotNone(first)
        self.addCleanup(os.close, first)
        self.assertGreater(os.fstat(first).st_mtime, time.time() - 60, "tomar el cerrojo refresca su mtime")
        os.utime("s1.lock", (old, old), dir_fd=fd)  # aunque volviera a parecer viejo
        hook._prune_sessions(fd)
        self.assertTrue((hook.telemetry_dir(self.root) / "sessions" / "s1.lock").exists())
        with mock.patch.object(hook, "LOCK_WAIT_S", 0.2):
            self.assertIsNone(hook._acquire_lock(fd, "s1.lock"), "dos titulares")

    def test_la_poda_si_borra_un_cerrojo_viejo_que_nadie_tiene(self) -> None:
        fd = hook._open_sessions_dir(self.root, create=True)
        self.addCleanup(os.close, fd)
        os.close(os.open("s1.lock", os.O_CREAT | os.O_RDWR, 0o600, dir_fd=fd))
        old = time.time() - 40 * 24 * 3600
        os.utime("s1.lock", (old, old), dir_fd=fd)
        hook._prune_sessions(fd)
        self.assertFalse((hook.telemetry_dir(self.root) / "sessions" / "s1.lock").exists())

    # S5
    def test_un_fifo_en_la_telemetria_no_cuelga_el_hook(self) -> None:
        directory = hook.telemetry_dir(self.root)
        directory.mkdir(parents=True, mode=0o700)
        os.mkfifo(directory / "turn_close.jsonl")
        elapsed, _ = self._evaluate_in_subprocess()
        self.assertLess(elapsed, 3)

    def test_un_fifo_en_el_estado_o_en_el_cerrojo_no_cuelga_el_hook(self) -> None:
        for name in ("s1.json", "s1.lock"):
            with self.subTest(name=name):
                sessions = hook.telemetry_dir(self.root) / "sessions"
                shutil.rmtree(hook.telemetry_dir(self.root), ignore_errors=True)
                sessions.mkdir(parents=True, mode=0o700)
                os.mkfifo(sessions / name)
                elapsed, output = self._evaluate_in_subprocess()
                self.assertLess(elapsed, 3)
                self.assertNotIn("Traceback", output)

    # S9a
    def test_un_nombre_terminado_en_salto_de_linea_no_es_de_la_cache(self) -> None:
        self.assertIsNone(hook._CACHE_NAME_RE.match("x-0123456789ab\n"))
        self.assertIsNotNone(hook._CACHE_NAME_RE.match("x-0123456789ab"))
        trailing = self._cache_dir() / "viejo-0123456789ab\n"
        trailing.mkdir(parents=True)
        past = time.time() - 300 * 24 * 3600
        os.utime(trailing, (past, past))
        self.assertEqual(hook.cache_folder_count(), 0)
        result = hook.prune_cache(root=self.root, apply=True)
        self.assertEqual((result["candidates"], result["skipped"]), ([], 0))
        self.assertTrue(trailing.is_dir())

    def _cache_dir(self) -> Path:
        return self.home / ".cache" / "framework-v4"

    # S9b
    def test_la_contencion_tiene_su_propio_mensaje_y_no_registra_el_fallo_de_guardado(self) -> None:
        with mock.patch.object(hook, "_acquire_lock", return_value=None):
            out = self._run(self._event(BAD))
        self.assertNotIn("decision", out)
        self.assertIn("Otra invocación", out["systemMessage"])
        self.assertNotIn("permisos", out["systemMessage"])
        stages = [(r.get("stage"), r.get("error")) for r in self._records() if r.get("result") == "failed_open"]
        self.assertEqual(stages, [("lock", "LockUnavailable")])

    def test_el_mensaje_de_permisos_sigue_siendo_el_de_siempre(self) -> None:
        (self.home / ".cache").write_text("no es un directorio", encoding="utf-8")
        self.assertIn("revisa los permisos", self._run(self._event(BAD))["systemMessage"])

    # S9c
    def test_la_limpieza_exige_al_menos_un_dia(self) -> None:
        script = [sys.executable, str(SCRIPTS / "turn_close_hook.py")]
        env = {**os.environ, "HOME": str(self.home)}
        for days in ("0", "-3"):
            result = subprocess.run([*script, "--prune-cache", "--days", days], capture_output=True, text=True,
                                    env=env, timeout=30)
            self.assertEqual(result.returncode, 2, days)
            self.assertIn("--days", result.stderr)
        self.assertEqual(subprocess.run([*script, "--prune-cache", "--days", "1"], capture_output=True, text=True,
                                        env=env, timeout=30).returncode, 0)
        with self.assertRaises(ValueError):
            hook.prune_cache(root=self.root, days=0)

    # S9d
    def test_un_padre_de_la_cache_que_no_es_propio_ni_se_escribe_ni_se_limpia(self) -> None:
        cache = self._cache_dir()
        victim = cache / "viejo-0123456789ab"
        victim.mkdir(parents=True)
        past = time.time() - 300 * 24 * 3600
        os.utime(victim, (past, past))
        parent_inode = os.stat(cache).st_ino
        real = hook._is_mine
        foreign_parent = mock.patch.object(hook, "_is_mine", lambda info: False if info.st_ino == parent_inode else real(info))
        with foreign_parent:
            self.assertIsNone(hook.open_telemetry_dir(self.root))
            self.assertEqual(self._run(self._event(BAD)).get("decision"), None)
            result = hook.prune_cache(root=self.root, apply=True)
        self.assertEqual((result["candidates"], result["deleted"]), ([], []))
        self.assertTrue(victim.is_dir())
        self.assertFalse(hook.telemetry_dir(self.root).exists())
        self.assertEqual(self._run(self._event(BAD))["decision"], "block")  # con el padre propio, como siempre


def _bad(turn: str) -> str:
    """Un cierre defectuoso distinto por turno."""
    return f"Hecho el turno {turn}. Mañana seguimos."


class L5bRound3Tests(HookCase):
    """5.3.0 L5b, ronda 3 (FW-355): la racha no se consume por una invocación duplicada; el aviso llega en Codex."""

    def _decisions(self, messages: list[str], **extra) -> list[str | None]:
        return [self._run(self._event(message, **extra)).get("decision") for message in messages]

    def test_dos_invocaciones_por_cierre_bloquean_el_primero_y_dejan_pasar_el_segundo(self) -> None:
        a, b, c = _bad("A"), _bad("B"), _bad("C")
        self.assertEqual(self._decisions([a, a, b, b, c, c]), ["block", None, None, None, "block", None])

    def test_con_una_invocacion_por_cierre_es_lo_de_siempre(self) -> None:
        self.assertEqual(self._decisions([_bad("A"), _bad("B"), _bad("C")]), ["block", None, "block"])

    def test_un_tercer_intento_del_mismo_turno_tampoco_bloquea(self) -> None:
        a = _bad("A")
        self.assertEqual(self._decisions([a, a, a, a]), ["block", None, None, None])

    def test_el_duplicado_se_registra_con_un_campo_y_sin_resultado_nuevo(self) -> None:
        a = _bad("A")
        self._decisions([a, a])
        first, second = self._records("turn_close_check")
        self.assertEqual((first["result"], second["result"]), ("blocked", "passed_after_block"))
        self.assertNotIn("duplicate", first)
        self.assertIs(second["duplicate"], True)

    def test_con_stop_hook_active_no_cambia_nada(self) -> None:
        self.assertEqual(self._decisions([_bad("A"), _bad("B")], stop_hook_active=True), [None, None])
        self.assertEqual([r["result"] for r in self._records("turn_close_check")], ["passed_after_block"] * 2)
        self.assertFalse((hook.telemetry_dir(self.root) / "sessions" / "s1.json").exists(), "no guarda estado")
        # Tras un bloqueo, el paso con la continuación marcada consume la racha como hoy.
        self.assertEqual(self._decisions([_bad("C")]), ["block"])
        self.assertEqual(self._decisions([_bad("D")], stop_hook_active=True), [None])
        self.assertEqual(self._decisions([_bad("E")]), ["block"])

    def test_la_huella_no_sale_de_la_carpeta_de_estado(self) -> None:
        a = _bad("A")
        fingerprint = hook._short_hash("s1", a)
        outputs = [self._run(self._event(a)) for _ in range(2)]
        directory = hook.telemetry_dir(self.root)
        self.assertEqual(json.loads((directory / "sessions" / "s1.json").read_text(encoding="utf-8")),
                         {"streak": 1, "turn": fingerprint})
        self.assertRegex(fingerprint, r"^[0-9a-f]{12}$")
        self.assertNotIn(fingerprint, (directory / "turn_close.jsonl").read_text(encoding="utf-8"))
        self.assertNotIn(fingerprint, json.dumps(outputs))
        self.assertNotIn("Mañana", (directory / "sessions" / "s1.json").read_text(encoding="utf-8"))

    def test_un_estado_con_turn_de_tipo_o_forma_erronea_se_descarta(self) -> None:
        for index, turn in enumerate((7, None, ["abcdef012345"], "abc", "ABCDEF012345", "abcdef0123456", "zzzzzzzzzzzz",
                                      "abcdef012345\n")):
            with self.subTest(turn=turn):
                session_id = f"t{index}"
                self.assertTrue(hook._save_session(self.root, session_id, {"streak": 1, "turn": turn}))
                self.assertEqual(hook._load_session(self.root, session_id), {"streak": 1})
        self.assertTrue(hook._save_session(self.root, "ok", {"streak": 1, "turn": "abcdef012345"}))
        self.assertEqual(hook._load_session(self.root, "ok"), {"streak": 1, "turn": "abcdef012345"})

    def test_un_cierre_correcto_sin_racha_sigue_sin_escribir_estado(self) -> None:
        self.assertEqual([self._run(self._event(GOOD)) for _ in range(3)], [{}, {}, {}])
        self.assertFalse((hook.telemetry_dir(self.root) / "sessions" / "s1.json").exists())
        # Y tras haber pasado una racha, los cierres correctos siguientes tampoco escriben.
        self._decisions([_bad("A"), _bad("B")])
        before = (hook.telemetry_dir(self.root) / "sessions" / "s1.json").read_bytes()
        self.assertEqual(self._run(self._event(GOOD)), {})
        self.assertEqual((hook.telemetry_dir(self.root) / "sessions" / "s1.json").read_bytes(), before)

    def _session_bytes(self) -> bytes:
        return (hook.telemetry_dir(self.root) / "sessions" / "s1.json").read_bytes()

    def test_el_bloqueo_por_bucle_comparte_la_racha_y_su_duplicado_no_la_consume(self) -> None:
        """Tras un bloqueo solo por bucle (cierre correcto) la segunda invocación del mismo turno ya no ve el bucle:
        sigue siendo un duplicado, no cambia el estado y no consume la racha."""
        transcript = self._transcript(_test_runs(RED, RED, RED))
        loop = self._event(GOOD, transcript_path=transcript)
        first = self._run(loop, host="claude")
        saved = self._session_bytes()
        second = self._run(loop, host="claude")
        self.assertEqual(self._session_bytes(), saved, "el duplicado no cambia el estado")
        # El turno defectuoso siguiente pasa (consume la racha) y el de después bloquea.
        third = self._run(self._event(_bad("B"), transcript_path=transcript), host="claude")
        fourth = self._run(self._event(_bad("C"), transcript_path=transcript), host="claude")
        self.assertEqual([out.get("decision") for out in (first, second, third, fourth)], ["block", None, None, "block"])
        checks = self._records("turn_close_check")
        duplicate = checks[1]
        self.assertIs(duplicate["duplicate"], True)
        self.assertEqual((duplicate["result"], duplicate["valid"]), ("pass", True), "no dice que pasó tras bloqueo")

    def test_el_duplicado_de_un_bloqueo_por_bucle_con_validacion_fallida(self) -> None:
        """Sin mensaje no hay duplicado: ese caso está en `L5bThirdReadTests`."""
        transcript = self._transcript(_test_runs(RED, RED, RED))
        event = self._event(GOOD, transcript_path=transcript)
        first = self._run(event, host="claude")
        saved = self._session_bytes()
        with mock.patch.object(hook, "_validate_close", side_effect=RuntimeError("transitorio")):
            second = self._run(event, host="claude")
        self.assertEqual((first.get("decision"), second.get("decision")), ("block", None))
        self.assertEqual(self._session_bytes(), saved)
        self.assertIsNone(self._run(self._event(_bad("B"), transcript_path=transcript), host="claude").get("decision"))
        self.assertEqual(self._run(self._event(_bad("C"), transcript_path=transcript), host="claude")["decision"], "block")

    def test_un_sondeo_remoto_que_solo_nombra_pytest_no_corta_la_sesion(self) -> None:
        lines: list[dict] = []
        for index in range(3):
            lines += _tool_pair(index, "Bash", {"command": "ssh ci pgrep -f pytest"}, "", error=True)
        out = self._run(self._event(GOOD, transcript_path=self._transcript(lines)), host="claude")
        self.assertNotIn("decision", out)
        self.assertEqual(self._records("loop_detected"), [])

    # ---- B: el aviso del hook llega en Codex

    def _codex_stop(self, event: dict) -> subprocess.CompletedProcess:
        scripts = self.root / ".agent" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        for name in ("turn_close_hook.py", "turn_close.py", "session_metrics.py", "session_cost.py",
                     "codex_hook_runner.py", "guard_destructive_command.py"):
            shutil.copy2(SCRIPTS / name, scripts / name)
        return subprocess.run([sys.executable, str(scripts / "codex_hook_runner.py"), "--hook-id", "stop"],
                              input=json.dumps({**event, "cwd": str(self.root)}).encode("utf-8"), capture_output=True,
                              env={**os.environ, "HOME": str(self.home)}, cwd=self.root, timeout=30)

    def test_en_codex_el_aviso_de_estado_no_guardado_llega_junto_al_recordatorio(self) -> None:
        (self.home / ".cache").write_text("no es un directorio", encoding="utf-8")
        result = self._codex_stop(self._event(BAD))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.isascii(), result.stdout)
        output = json.loads(result.stdout.decode("ascii"))
        self.assertEqual(list(output), ["systemMessage"])
        self.assertIn("Sesión terminada", output["systemMessage"])
        self.assertIn("no pudo guardar su estado", output["systemMessage"])

    def test_en_codex_sin_aviso_del_hook_solo_sale_el_recordatorio(self) -> None:
        output = json.loads(self._codex_stop(self._event(GOOD)).stdout.decode("ascii"))
        self.assertIn("Sesión terminada", output["systemMessage"])
        self.assertNotIn("hook de cierre", output["systemMessage"])
        blocked = json.loads(self._codex_stop(self._event(BAD)).stdout.decode("ascii"))
        self.assertEqual(list(blocked), ["decision", "reason"])


class L5bThirdReadTests(HookCase):
    """5.3.0 L5b, tercera lectura de `security`: sin mensaje no hay turno que reconocer como duplicado."""

    def _sin_mensaje(self, lines: list[dict]) -> dict:
        event = self._event(GOOD, transcript_path=self._transcript(lines))
        del event["last_assistant_message"]
        return event

    def test_sin_mensaje_la_racha_se_consume_y_el_bucle_siguiente_se_corta(self) -> None:
        otro: list[dict] = []
        for index in range(3):
            otro += _tool_pair(10 + index, "Bash", {"command": "python3 -m unittest tests.test_otro"}, RED, error=True)
        runs = [_test_runs(RED, RED, RED), _test_runs(RED, RED, RED, GREEN), _test_runs(RED, RED, RED, GREEN) + otro]
        decisions = [self._run(self._sin_mensaje(lines), host="claude").get("decision") for lines in runs]
        self.assertEqual(decisions, ["block", None, "block"])

    def test_un_mensaje_que_no_es_cadena_tampoco_es_duplicado(self) -> None:
        lines = _test_runs(RED, RED, RED)
        first = self._run(self._event(GOOD, transcript_path=self._transcript(lines)), host="claude")
        self.assertEqual(first.get("decision"), "block")
        event = self._event(GOOD, transcript_path=self._transcript(lines))
        event["last_assistant_message"] = ["no", "es", "cadena"]
        self._run(event, host="claude")
        sessions = hook.telemetry_dir(self.root) / "sessions"
        self.assertNotIn("streak", json.loads((sessions / "s1.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
