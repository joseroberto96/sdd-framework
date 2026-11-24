#!/usr/bin/env python3
"""Regresiones de la auditoría del cierre de turno sobre transcripciones (solo lectura)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import turn_close_audit as audit  # noqa: E402

PROJECT = "Mi Proyecto/con espacios"
SID = "01a0aaaa-0000-7000-8000-0000000000aa"
BUILDER = ["arrancar", "reconciliar", "gycd", "diseñar", "nuevo-bug", "nuevo-proyecto",
           "migrar-proyecto", "actualizar-framework", "ciclo"]
MANIFEST = {"commands": [{"name": n, "builder_facing": True} for n in BUILDER]
            + [{"name": "tdd", "builder_facing": False}]}
MODEL = "Modelo: strong · Esfuerzo: 🟠 alto (tier_1)"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _human(ts: str, cwd: str, text: str, **extra) -> dict:
    return {"type": "user", "timestamp": ts, "cwd": cwd, "isSidechain": False,
            "message": {"role": "user", "content": text}, **extra}


def _assistant(ts: str, cwd: str, mid: str, text: str | None = None, tool: bool = False, **extra) -> dict:
    blocks = []
    if text is not None:
        blocks.append({"type": "text", "text": text})
    if tool:
        blocks.append({"type": "tool_use", "id": "t-" + mid, "name": "Bash", "input": {}})
    return {"type": "assistant", "timestamp": ts, "cwd": cwd, "isSidechain": False, "uuid": "u-" + mid,
            "message": {"id": mid, "role": "assistant", "model": "claude-x", "content": blocks}, **extra}


def _tool_result(ts: str, cwd: str) -> dict:
    return {"type": "user", "timestamp": ts, "cwd": cwd, "isSidechain": False,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}


def _codex_meta(cwd: str, source: str, ts: str, rid: str, session_id: str = SID) -> dict:
    return {"timestamp": ts, "type": "session_meta",
            "payload": {"id": rid, "session_id": session_id, "cwd": cwd, "thread_source": source}}


def _codex_user(ts: str, text: str) -> dict:
    return {"timestamp": ts, "type": "event_msg",
            "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": text}]}}}


def _codex_done(ts: str, turn: str, message: str | None) -> dict:
    return {"timestamp": ts, "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": turn, "last_agent_message": message}}


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = audit.main(argv)
    return code, out.getvalue()


class TranscriptFixture(unittest.TestCase):
    """Claude: multi.jsonl (6 cierres), dup.jsonl (repite m2), subagente y otro proyecto. Codex: 2 cierres."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cwd = str(self.tmp / PROJECT)
        Path(self.cwd).mkdir(parents=True)
        self.other = str(self.tmp / "otro")
        Path(self.other).mkdir()
        (self.tmp / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        c = self.cwd
        close_ok = "Hecho.\n\nSiguiente: /ciclo Implementar el motor en src/motor.py\n" + MODEL
        folder = self.tmp / "claude" / "-carpeta-ambigua"
        multi = [
            _human("2026-10-01T10:00:00Z", c, "<command-message>arrancar</command-message>\n<command-name>/arrancar</command-name>"),
            _assistant("2026-10-01T10:00:05Z", c, "m1a", "Voy a mirar el estado.", tool=True),
            _tool_result("2026-10-01T10:00:06Z", c),
            _human("2026-10-01T10:00:07Z", c, "<system-reminder>x</system-reminder>", isMeta=True),
            {**_human("2026-10-01T10:00:08Z", c, "<task-notification>fin</task-notification>"), "origin": {"kind": "task-notification"}},
            _assistant("2026-10-01T10:00:09Z", c, "m1b", close_ok),
            _human("2026-10-02T10:00:00Z", c, "hola"),
            _assistant("2026-10-02T10:00:05Z", c, "m2", "Todo bien, sin cierre."),
            _human("2026-10-02T11:00:00Z", c, "sigue"),
            _assistant("2026-10-02T11:00:05Z", c, "m2b", None, tool=True),  # turno interrumpido: sin texto final
            _human("2026-10-03T10:00:00Z", c, "otra"),
            _assistant("2026-10-03T10:00:05Z", c, "m3", "Hecho.\nSiguiente comando: /ciclo algo\nPrompt sugerido: continúa"),
            _human("2026-10-04T10:00:00Z", c, "otra más"),
            _assistant("2026-10-04T10:00:05Z", c, "m4", "Hecho.\n```\nSiguiente: /ciclo Hacer algo\n```"),
            _human("2026-10-05T10:00:00Z", c, "y otra"),
            _assistant("2026-10-05T10:00:05Z", c, "m5", "Siguiente: /ciclo Uno\nSiguiente: /ciclo Dos\n" + MODEL),
            _human("2026-10-06T10:00:00Z", c, "última"),
            _assistant("2026-10-06T10:00:05Z", c, "m6", "Siguiente: `/ciclo Hacer algo`\n" + MODEL + "\nUn párrafo después."),
            _assistant("2026-10-06T10:00:06Z", c, "side", "Siguiente: /ciclo basura", isSidechain=True),
        ]
        _write(folder / "multi.jsonl", multi)
        _write(folder / "multi" / "subagents" / "agent-1.jsonl", [
            _human("2026-10-06T11:00:00Z", c, "subagente"),
            _assistant("2026-10-06T11:00:05Z", c, "sub1", "sin cierre de subagente"),
        ])
        _write(folder / "dup.jsonl", [
            _human("2026-10-02T11:59:00Z", c, "hola otra vez"),
            _assistant("2026-10-02T12:00:00Z", c, "m2", "Todo bien, sin cierre."),
        ])
        _write(self.tmp / "claude" / "-otro" / "x.jsonl", [
            _human("2026-10-02T10:00:00Z", self.other, "x"),
            _assistant("2026-10-02T10:00:05Z", self.other, "o1", "sin cierre"),
        ])
        os.utime(folder / "multi.jsonl", (1, 1_791_700_000))
        os.utime(folder / "dup.jsonl", (1, 1_791_700_100))  # la copia posterior gana

        home = self.tmp / "codex"
        day = home / "sessions" / "2026" / "10" / "02"
        _write(day / "rollout-root.jsonl", [
            _codex_meta(c, "user", "2026-10-02T09:00:00Z", SID),
            _codex_user("2026-10-02T09:00:01Z", "[$source-command-arrancar](/x/SKILL.md) inicia"),
            _codex_done("2026-10-02T09:05:00Z", "turn-1", "Hecho.\nSiguiente: /ciclo Construir el módulo B con todos los gates"),
            _codex_done("2026-10-02T09:10:00Z", "turn-2", "Listo, sin línea de cierre."),
            _codex_done("2026-10-02T09:11:00Z", "turn-3", None),
        ])
        _write(day / "rollout-sub.jsonl", [
            _codex_meta(c, "subagent", "2026-10-02T09:01:00Z", "sub-1"),
            _codex_done("2026-10-02T09:02:00Z", "turn-sub", "resultado del subagente"),
        ])
        _write(day / "rollout-guard.jsonl", [
            _codex_meta(c, "guardian_review", "2026-10-02T09:03:00Z", "g-1"),
            _codex_done("2026-10-02T09:03:30Z", "turn-g", '{"outcome":"allow"}'),
        ])
        _write(day / "rollout-otro.jsonl", [
            _codex_meta(self.other, "user", "2026-10-02T09:00:00Z", "otro", "otro"),
            _codex_done("2026-10-02T09:05:00Z", "turn-o", "sin cierre"),
        ])
        self.base = ["--claude-dir", str(self.tmp / "claude"), "--codex-home", str(home),
                     "--manifest", str(self.tmp / "manifest.json")]

    def report(self, *extra: str) -> dict:
        code, out = run(self.base + ["--json", *extra])
        self.assertEqual(code, 0)
        return json.loads(out)


class ClaudeTests(TranscriptFixture):
    def test_counts_validity_and_codes(self) -> None:
        total = self.report("--agent", "claude", "--project", self.cwd)["total"]
        self.assertEqual(total["cierres_total"], 6)
        self.assertEqual(total["cierres_validos"], 1)
        codes = total["defectos_por_codigo"]
        self.assertEqual(codes["SIN_SIGUIENTE"], 2)
        self.assertEqual(codes["FORMATO_HEREDADO"], 1)
        self.assertEqual(codes["EN_BLOQUE_DE_CODIGO"], 1)
        self.assertEqual(codes["VARIAS_LINEAS_SIGUIENTE"], 1)
        self.assertEqual(codes["COMILLAS_EN_COMANDO"], 1)
        self.assertEqual(codes["NO_ES_LO_ULTIMO"], 1)
        self.assertEqual((total["sin_siguiente"], total["formato_heredado"]), (2, 1))

    def test_closing_is_last_text_before_real_human_message(self) -> None:
        cierres = self.report("--agent", "claude", "--project", self.cwd)["cierres"]
        first = min(cierres, key=lambda c: c["ts"])
        self.assertTrue(first["valido"])
        self.assertIn("Implementar el motor", first["linea_de_cierre"])

    def test_end_of_session_is_flagged_and_interrupted_turn_is_counted(self) -> None:
        data = self.report("--agent", "claude", "--project", self.cwd)
        ends = [c for c in data["cierres"] if c["fin_de_sesion"]]
        self.assertEqual(sorted(c["sesion"] for c in ends), ["dup", "multi"])
        self.assertEqual(data["total"]["sesiones"]["turnos_sin_texto_final"], 1)

    def test_repeated_message_is_deduplicated_keeping_last_appearance(self) -> None:
        cierres = self.report("--agent", "claude", "--project", self.cwd)["cierres"]
        copies = [c for c in cierres if c["ts"].startswith("2026-10-02T1")]
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0]["sesion"], "dup")

    def test_subagent_sidechain_and_other_project_are_ignored(self) -> None:
        sessions = audit.collect_sessions("claude", self.tmp / "claude", self.tmp / "nada", [self.cwd], None)
        self.assertEqual(sorted(s.session_id for s in sessions), ["dup", "multi"])
        cierres = self.report("--agent", "claude")["cierres"]
        self.assertFalse(any("basura" in c["linea_de_cierre"] for c in cierres))
        self.assertFalse(any(c["sesion"] == "agent-1" for c in cierres))

    def test_project_with_spaces_and_subpath(self) -> None:
        self.assertEqual(self.report("--agent", "claude", "--project", self.cwd)["total"]["cierres_total"], 6)
        self.assertEqual(self.report("--agent", "claude", "--project", self.cwd + "/sub")["total"]["cierres_total"], 0)

    def test_session_start_and_arrancar_forwarding(self) -> None:
        sessions = self.report("--agent", "claude", "--project", self.cwd)["total"]["sesiones"]
        self.assertEqual(sessions["total"], 2)
        self.assertEqual(sessions["primer_mensaje"], {"comando": 1, "texto_libre": 1})
        self.assertEqual(sessions["abren_con_arrancar"], 1)
        self.assertEqual(sessions["arrancar_primer_cierre_solo_ciclo"], 1)

    def test_commands_ciclo_verbs_models_and_lengths(self) -> None:
        total = self.report("--agent", "claude", "--project", self.cwd)["total"]
        self.assertEqual(total["comandos"], {"/ciclo": 3})
        self.assertEqual(total["ciclo"]["lineas"], 3)
        self.assertEqual(total["ciclo"]["primer_verbo"]["implementar"], 1)
        self.assertEqual(total["modelo_esfuerzo"]["con_linea"], 3)
        self.assertEqual(total["modelo_esfuerzo"]["pares"], {"strong · alto": 3})
        self.assertEqual(total["instruccion_longitud"]["maximo"], len("Implementar el motor en src/motor.py"))

    def test_since_and_until_use_closing_timestamp_until_is_exclusive(self) -> None:
        since = self.report("--agent", "claude", "--project", self.cwd, "--since", "2026-10-04")["total"]
        self.assertEqual(since["cierres_total"], 3)
        until = self.report("--agent", "claude", "--project", self.cwd, "--until", "2026-10-03")["total"]
        self.assertEqual(until["cierres_total"], 2)  # m1b (10-01) y la copia de m2 (10-02)
        both = self.report("--agent", "claude", "--project", self.cwd, "--since", "2026-10-03", "--until", "2026-10-05")["total"]
        self.assertEqual(both["cierres_total"], 2)  # 10-03 y 10-04; 10-05 queda fuera


class CodexTests(TranscriptFixture):
    def test_root_only_subagent_and_reviewer_ignored(self) -> None:
        data = self.report("--agent", "codex", "--project", self.cwd)
        self.assertEqual(data["total"]["cierres_total"], 2)
        self.assertEqual(data["total"]["cierres_validos"], 1)
        self.assertEqual(data["total"]["sin_siguiente"], 1)
        self.assertEqual({c["agente"] for c in data["cierres"]}, {"codex"})
        self.assertEqual(sum(1 for c in data["cierres"] if c["fin_de_sesion"]), 1)

    def test_first_message_is_command_from_skill_link(self) -> None:
        sessions = self.report("--agent", "codex", "--project", self.cwd)["total"]["sesiones"]
        self.assertEqual(sessions["abren_con_arrancar"], 1)
        self.assertEqual(sessions["arrancar_primer_cierre_solo_ciclo"], 1)

    def test_all_agents_and_per_project_breakdown(self) -> None:
        data = self.report("--agent", "all", "--project", self.cwd, "--project", self.other)
        self.assertEqual(data["total"]["cierres_total"], 6 + 2 + 1 + 1)
        self.assertEqual(data["proyectos"][self.cwd]["cierres_total"], 8)
        self.assertEqual(data["proyectos"][self.other]["cierres_total"], 2)
        self.assertEqual(data["total"]["por_agente"]["codex"]["cierres"], 3)

    def test_table_output_is_default(self) -> None:
        code, out = run(self.base + ["--agent", "claude", "--project", self.cwd])
        self.assertEqual(code, 0)
        self.assertIn("Cierres: 6", out)
        self.assertIn("SIN_SIGUIENTE", out)


class CliTests(unittest.TestCase):
    def test_invalid_arguments_exit_two(self) -> None:
        self.assertEqual(run(["--since", "ayer"])[0], 2)
        self.assertEqual(run(["--until", "2026-13-40"])[0], 2)
        self.assertEqual(run(["--agent", "gemini"])[0], 2)
        self.assertEqual(run(["--manifest", "/no/existe.json"])[0], 2)

    def test_empty_sources_are_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run(["--claude-dir", tmp + "/p", "--codex-home", tmp + "/c", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["total"]["cierres_total"], 0)


class TemplatesAndVocabularyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        wf = self.root / ".agent" / "workflows"
        wf.mkdir(parents=True)
        (wf / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (wf / "bien.md").write_text(
            "# Bien\n\nCerrar con:\nSiguiente: /ciclo <instrucción concreta del siguiente módulo>\n"
            f"{MODEL}\n`Siguiente: ninguno — bloqueo breve`\nLa línea `Siguiente:` es obligatoria.\n", encoding="utf-8")
        (wf / "mal.md").write_text(
            "# Mal\n\nSiguiente: [comando activo] — continuar flujo actual.\n"
            "- Siguiente: `/tdd` Aplicar el cambio.\n" + MODEL + "\n"
            "Siguiente: /ciclo Hacer <algo>\n" + MODEL + "\n", encoding="utf-8")
        (wf / "sin.md").write_text("# Sin plantilla\n", encoding="utf-8")
        self.base = ["--root", str(self.root)]

    def test_templates_counts_and_locations(self) -> None:
        code, out = run(self.base + ["--templates", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["plantillas"]
        self.assertEqual(data["lineas_plantilla"], 5)
        self.assertEqual(data["lineas_defectuosas"], 3)
        self.assertEqual((data["workflows_afectados"], data["workflows_con_plantilla"], data["workflows_total"]), (1, 2, 3))
        self.assertEqual(data["menciones_en_prosa"], 1)
        found = {(d["fichero"], d["linea"]): d["codigos"] for d in data["defectuosas"]}
        self.assertEqual(found[(".agent/workflows/mal.md", 3)], ["SIN_COMANDO", "MARCADOR"])
        self.assertEqual(found[(".agent/workflows/mal.md", 4)], ["COMILLAS_EN_COMANDO", "COMANDO_INTERNO"])
        self.assertEqual(found[(".agent/workflows/mal.md", 6)], ["MARCADOR"])

    def test_templates_report_block_and_model_line_defects(self) -> None:
        """5.3.0 L3: la auditoría dice lo mismo que el doctor (bloque de código y línea de modelo)."""
        wf = self.root / ".agent" / "workflows"
        (wf / "forma.md").write_text(
            "# Forma\n\n```\nSiguiente: /ciclo Hacer el módulo X\n```\n\n"
            "Siguiente: /ciclo Cerrar el módulo X\n\nOtro texto\n"
            "Siguiente: ninguno — sin más pasos\n", encoding="utf-8")
        code, out = run(self.base + ["--templates", "--json"])
        self.assertEqual(code, 0)
        found = {(d["fichero"], d["linea"]): d["codigos"] for d in json.loads(out)["plantillas"]["defectuosas"]}
        self.assertEqual(found[(".agent/workflows/forma.md", 4)], ["EN_BLOQUE_DE_CODIGO", "SIN_MODELO"])
        self.assertEqual(found[(".agent/workflows/forma.md", 7)], ["SIN_MODELO"])
        self.assertNotIn((".agent/workflows/forma.md", 10), found)

    def test_templates_text_output(self) -> None:
        code, out = run(self.base + ["--templates"])
        self.assertEqual(code, 0)
        self.assertIn("3 de 5", out)
        self.assertIn("mal.md:3", out)

    def test_vocabulary_counts_active_surfaces_only(self) -> None:
        r = self.root
        (r / "AGENTS.md").write_text("# Framework v4\nFramework v4 otra vez\n", encoding="utf-8")
        (r / "CHANGELOG.md").write_text("Framework v4 histórico\n", encoding="utf-8")
        (r / "docs").mkdir()
        (r / "docs" / "x.md").write_text("Framework v4\n", encoding="utf-8")
        (r / "tests").mkdir()
        (r / "tests" / "x.md").write_text("Framework v4\n", encoding="utf-8")
        (r / ".agent" / "skills" / "imported").mkdir(parents=True)
        (r / ".agent" / "skills" / "a.md").write_text("usa /revisar aquí\n", encoding="utf-8")
        (r / ".agent" / "skills" / "imported" / "b.md").write_text("/revisar\n", encoding="utf-8")
        (r / ".agent" / "docs").mkdir()
        (r / ".agent" / "docs" / "extended_protocol.md").write_text("Low effort\n", encoding="utf-8")
        (r / ".agent" / "docs" / "field_report.md").write_text("Low effort\n", encoding="utf-8")
        (r / ".claude" / "commands").mkdir(parents=True)
        (r / ".claude" / "commands" / "c.md").write_text("Low effort y Low effort\n", encoding="utf-8")
        (r / "llms.txt").write_text("ESFUERZO: alto\n", encoding="utf-8")
        code, out = run(self.base + ["--vocabulary", "--json"])
        self.assertEqual(code, 0)
        terms = json.loads(out)["vocabulario"]["terminos"]
        self.assertEqual(terms["Framework v4"], {"total": 2, "ficheros": {"AGENTS.md": 2}})
        self.assertEqual(terms["/revisar"]["ficheros"], {".agent/skills/a.md": 1})
        self.assertEqual(terms["Low effort"]["total"], 3)
        self.assertEqual(set(terms["Low effort"]["ficheros"]), {".agent/docs/extended_protocol.md", ".claude/commands/c.md"})
        self.assertEqual(terms["ESFUERZO:"]["ficheros"], {"llms.txt": 1})
        self.assertEqual(terms["caveman.md"]["total"], 0)

    def test_vocabulary_creates_no_files(self) -> None:
        before = sorted(p.name for p in self.root.rglob("*"))
        run(self.base + ["--vocabulary"])
        self.assertEqual(sorted(p.name for p in self.root.rglob("*")), before)
        self.assertFalse(list(self.root.rglob("retired_vocabulary.json")))


class HumanCriterionTests(unittest.TestCase):
    def test_non_human_entries(self) -> None:
        cwd = "/x"
        cases = [
            _tool_result("t", cwd),
            _human("t", cwd, "x", isMeta=True),
            _human("t", cwd, "resumen", isCompactSummary=True),
            _human("t", cwd, "x", isSidechain=True),
            _human("t", cwd, "<task-notification>x</task-notification>"),
            _human("t", cwd, "<system-reminder>x</system-reminder>"),
            _human("t", cwd, "<local-command-stdout>ok</local-command-stdout>"),
            _human("t", cwd, "<command-name>/model</command-name>"),
            _human("t", cwd, "[Request interrupted by user for tool use]"),
            {**_human("t", cwd, "texto"), "origin": {"kind": "peer"}},
        ]
        for obj in cases:
            self.assertFalse(audit.is_human_message(obj), obj)

    def test_human_entries(self) -> None:
        cwd = "/x"
        for obj in (
            _human("t", cwd, "hola"),
            {**_human("t", cwd, "hola"), "origin": {"kind": "human"}},
            _human("t", cwd, "<command-message>ciclo</command-message>\n<command-name>/ciclo</command-name>"),
            _human("t", cwd, "<bash-input>git status</bash-input>"),
        ):
            self.assertTrue(audit.is_human_message(obj), obj)

    def test_classify_first_message(self) -> None:
        self.assertEqual(audit.classify_first_message("<command-name>/arrancar</command-name>"), ("comando", "arrancar"))
        self.assertEqual(audit.classify_first_message("/diseñar algo"), ("comando", "diseñar"))
        self.assertEqual(audit.classify_first_message("[$source-command-gycd](x) y más"), ("comando", "gycd"))
        self.assertEqual(audit.classify_first_message("\n# Files pasted by the user:\n\n## My request:\n/ciclo ya"), ("comando", "ciclo"))
        self.assertEqual(audit.classify_first_message("hola, ¿qué tal?"), ("texto_libre", ""))


if __name__ == "__main__":
    unittest.main()
