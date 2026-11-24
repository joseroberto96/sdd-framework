#!/usr/bin/env python3
"""Regresiones de los subcomandos de medición de la 5.3.0 L1 (solo lectura)."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import session_cost as sc  # noqa: E402
import session_metrics as sm  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "session_metrics"
# Proyecto fixture con espacios en la ruta: lleva state_log, mapa y estado.
FIXTURE_PROJECT = str(FIXTURES / "proyecto con espacios")
PROJECT = "Mi Proyecto/con espacios"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def run(argv: list[str]) -> dict | str:
    """Ejecuta el CLI de session_cost y devuelve el JSON (si pide --json) o el texto."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = sc.main(argv)
    assert code == 0, out.getvalue()
    return json.loads(out.getvalue()) if "--json" in argv else out.getvalue()


class Builder:
    """Escribe una transcripción raíz de Claude con ids y uuids consecutivos."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.rows: list[dict] = []
        self.n = 0

    def _uid(self) -> str:
        self.n += 1
        return f"u{self.n}"

    def user(self, ts: str, text: str) -> None:
        self.rows.append({"type": "user", "timestamp": ts, "cwd": self.cwd, "uuid": self._uid(),
                          "message": {"role": "user", "content": text}})

    def meta(self, ts: str, text: str) -> None:
        self.rows.append({"type": "user", "timestamp": ts, "cwd": self.cwd, "uuid": self._uid(), "isMeta": True,
                          "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    def say(self, ts: str, text: str) -> None:
        self.rows.append({"type": "assistant", "timestamp": ts, "cwd": self.cwd, "uuid": self._uid(),
                          "message": {"id": f"m{self.n}", "model": "claude-opus-5-5", "usage": {"input_tokens": 1},
                                      "content": [{"type": "text", "text": text}]}})

    def use(self, ts: str, tid: str, name: str, data: dict) -> None:
        self.rows.append({"type": "assistant", "timestamp": ts, "cwd": self.cwd, "uuid": self._uid(),
                          "message": {"id": f"m{self.n}", "model": "claude-opus-5-5", "usage": {"input_tokens": 1},
                                      "content": [{"type": "tool_use", "id": tid, "name": name, "input": data}]}})

    def result(self, ts: str, tid: str, text: str, error: bool = False) -> None:
        self.rows.append({"type": "user", "timestamp": ts, "cwd": self.cwd, "uuid": self._uid(),
                          "message": {"role": "user", "content": [
                              {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}})

    def write(self, root: Path, name: str = "s1") -> Path:
        path = root / "-carpeta" / f"{name}.jsonl"
        _write(path, self.rows)
        return path


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.claude = self.tmp / "claude"
        self.codex = self.tmp / "codex"
        self.cwd = str(self.tmp / PROJECT)  # ruta real con espacios
        Path(self.cwd).mkdir(parents=True)

    def base(self, command: str, *extra: str, cwd: str | None = None) -> list[str]:
        return [command, "--claude-dir", str(self.claude), "--codex-home", str(self.codex),
                "--project", cwd or self.cwd, *extra]


class NormLoadTests(TmpCase):
    def test_norm_files_sizes_and_per_turn(self) -> None:
        (Path(self.cwd) / "AGENTS.md").write_text("a" * 300, encoding="utf-8")
        (Path(self.cwd) / "CLAUDE.md").write_text("b" * 100, encoding="utf-8")
        b = Builder(self.cwd)
        b.user("2026-10-02T10:30:00Z", "hola")
        b.use("2026-10-02T10:30:10Z", "t1", "Read", {"file_path": f"{self.cwd}/.agent/rules/runtime_core.md"})
        b.result("2026-10-02T10:30:11Z", "t1", "x" * 400)
        b.use("2026-10-02T10:30:20Z", "t2", "Read", {"file_path": f"{self.cwd}/src/app.py"})  # no es norma
        b.result("2026-10-02T10:30:21Z", "t2", "y" * 999)
        b.user("2026-10-02T10:31:00Z", "otro turno")
        b.use("2026-10-02T10:31:10Z", "t3", "Bash", {"command": "cat AGENTS.md CLAUDE.md"})
        b.result("2026-10-02T10:31:11Z", "t3", "z" * 800)  # reparto por tamaño en disco: 600 + 200
        b.use("2026-10-02T10:31:20Z", "t4", "Skill", {"skill": "ciclo"})
        b.result("2026-10-02T10:31:21Z", "t4", "Launching skill: ciclo")
        b.meta("2026-10-02T10:31:22Z", "<!-- GENERATED: framework wrapper -->\n\n# /ciclo\n\nFuente canónica: x " + "w" * 100)
        b.write(self.claude)
        data = run(self.base("norm-load", "--json"))
        self.assertEqual(len(data["sesiones"]), 1)
        s = data["sesiones"][0]
        self.assertEqual(s["turns"], 2)
        files = {f["file"]: f for f in s["top_files"]}
        self.assertEqual(files[".agent/rules/runtime_core.md"]["tokens"], 100)  # 400 caracteres / 4
        self.assertEqual(files["AGENTS.md"]["tokens"], 150)  # 600 / 4
        self.assertEqual(files["CLAUDE.md"]["tokens"], 50)  # 200 / 4
        self.assertIn(".claude/commands/ciclo.md", files)
        self.assertNotIn("src/app.py", files)
        wrapper_chars = len("<!-- GENERATED: framework wrapper -->\n\n# /ciclo\n\nFuente canónica: x " + "w" * 100)
        self.assertEqual(s["norm_tokens"], 100 + 150 + 50 + sm.est_tokens(wrapper_chars))
        self.assertEqual(s["per_turn_tokens"], [0, 100, 200 + sm.est_tokens(wrapper_chars)])
        self.assertEqual(data["token_chars"], 4)

    def test_size_falls_back_to_current_file_when_result_is_empty(self) -> None:
        (Path(self.cwd) / "GUARDRAILS_MASTER.md").write_text("g" * 80, encoding="utf-8")
        b = Builder(self.cwd)
        b.user("2026-10-02T10:30:00Z", "hola")
        b.use("2026-10-02T10:30:10Z", "t1", "Read", {"file_path": f"{self.cwd}/GUARDRAILS_MASTER.md"})
        b.result("2026-10-02T10:30:11Z", "t1", "")
        b.write(self.claude)
        s = run(self.base("norm-load", "--json"))["sesiones"][0]
        self.assertEqual(s["norm_tokens"], 20)

    def test_module_attribution_uses_state_log_window_and_project_with_spaces(self) -> None:
        b = Builder(FIXTURE_PROJECT)
        b.user("2026-10-02T10:30:00Z", "dentro del ciclo de A_primero")
        b.use("2026-10-02T10:30:10Z", "t1", "Read", {"file_path": f"{FIXTURE_PROJECT}/AGENTS.md"})
        b.result("2026-10-02T10:30:11Z", "t1", "q" * 400)
        b.use("2026-10-02T13:00:10Z", "t2", "Read", {"file_path": f"{FIXTURE_PROJECT}/CLAUDE.md"})  # fuera de ciclo
        b.result("2026-10-02T13:00:11Z", "t2", "q" * 400)
        b.write(self.claude)
        data = run(self.base("norm-load", "--json", cwd=FIXTURE_PROJECT))
        self.assertEqual([m["module"] for m in data["por_modulo"]], ["A_primero"])
        self.assertEqual(data["por_modulo"][0]["tokens"], 100)
        self.assertEqual(data["atribucion_modulo_pct"], 50.0)
        text = run(self.base("norm-load", cwd=FIXTURE_PROJECT))
        self.assertIn("Top 10 ficheros de norma", text)


class ReadWeightTests(TmpCase):
    def test_classes_and_percentages(self) -> None:
        b = Builder(self.cwd)
        b.user("2026-10-02T10:30:00Z", "p" * 100)  # builder 100
        b.say("2026-10-02T10:30:05Z", "a" * 200)  # asistente 200
        b.use("2026-10-02T10:30:10Z", "t1", "Read", {"file_path": f"{self.cwd}/src/x.py"})
        b.result("2026-10-02T10:30:11Z", "t1", "r" * 300)  # lectura 300
        b.use("2026-10-02T10:30:12Z", "t2", "Bash", {"command": "pytest -q"})
        b.result("2026-10-02T10:30:13Z", "t2", "s" * 400)  # shell 400
        b.use("2026-10-02T10:30:14Z", "t3", "mcp__x__y", {})
        b.result("2026-10-02T10:30:15Z", "t3", "o" * 50)  # otros 50
        b.use("2026-10-02T10:30:16Z", "t4", "Read", {"file_path": f"{self.cwd}/AGENTS.md"})
        b.result("2026-10-02T10:30:17Z", "t4", "n" * 150)  # norma 150 (no cuenta como lectura)
        b.write(self.claude)
        s = run(self.base("read-weight", "--json"))["sesiones"][0]
        chars = {k: v["chars"] for k, v in s["clases"].items()}
        self.assertEqual(chars, {"lectura": 300, "shell": 400, "otros": 50, "norma": 150, "builder": 100, "asistente": 200})
        self.assertEqual(s["chars_total"], 1200)
        self.assertEqual(s["clases"]["shell"]["pct"], 33.3)
        self.assertEqual(s["clases"]["lectura"]["tokens"], 75)
        self.assertIn("%norma", run(self.base("read-weight")))


class LoopsTests(TmpCase):
    def _session(self, name: str, runs: list[bool]) -> None:
        b = Builder(self.cwd)
        b.user("2026-10-02T10:00:00Z", "arregla")
        for i, failed in enumerate(runs):
            tid = f"t{i}"
            b.use(f"2026-10-02T10:0{i}:10Z", tid, "Bash", {"command": 'cd "x y" && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -5'})
            out = ("FAILED tests/test_a.py::test_uno - AssertionError\n1 failed in 0.%ds" % i) if failed else "3 passed in 0.1s"
            b.result(f"2026-10-02T10:0{i}:11Z", tid, out)
        b.write(self.claude, name)

    def test_repeated_red_runs_distribution_and_threshold(self) -> None:
        self._session("rojo", [True, True, True, True, False, True])
        self._session("limpio", [True, False])
        data = run(self.base("loops", "--json"))
        self.assertEqual(data["sesiones"], 2)
        self.assertEqual(data["umbral"], 3)
        self.assertEqual(data["sesiones_con_tests_rojos_seguidos"], 1)
        self.assertEqual(data["distribucion_rojos_seguidos"], {"1": 2, "4": 1})  # 1+1 sueltos y una cadena de 4
        self.assertEqual(data["distribucion_rojos_misma_firma"], {"1": 2, "4": 1})  # los dígitos de la cola no cuentan
        self.assertEqual(data["marcadas"][0]["session_id"], "rojo")
        self.assertEqual(data["marcadas"][0]["comandos"][0]["command"], ".venv/bin/python -m pytest tests/ -q")
        strict = run(self.base("loops", "--json", "--threshold", "5"))
        self.assertEqual(strict["sesiones_con_tests_rojos_seguidos"], 0)
        self.assertEqual(strict["marcadas"], [])

    def test_edit_then_revert_and_write_back_to_old_content(self) -> None:
        b = Builder(self.cwd)
        b.user("2026-10-02T10:00:00Z", "prueba")
        path = f"{self.cwd}/src/a.py"
        steps = [("Edit", {"file_path": path, "old_string": "A", "new_string": "B"}),
                 ("Edit", {"file_path": path, "old_string": "B", "new_string": "A"}),  # revert 1
                 ("Edit", {"file_path": path, "old_string": "A", "new_string": "C"}),
                 ("Edit", {"file_path": path, "old_string": "C", "new_string": "A"}),  # revert 2
                 ("Write", {"file_path": f"{self.cwd}/b.txt", "content": "uno"}),
                 ("Write", {"file_path": f"{self.cwd}/b.txt", "content": "dos"}),
                 ("Write", {"file_path": f"{self.cwd}/b.txt", "content": "uno"})]  # vuelve al contenido anterior
        for i, (name, data) in enumerate(steps):
            b.use(f"2026-10-02T10:0{i}:10Z", f"e{i}", name, data)
            b.result(f"2026-10-02T10:0{i}:11Z", f"e{i}", "ok")
        b.write(self.claude)
        data = run(self.base("loops", "--json", "--threshold", "2"))
        self.assertEqual(data["distribucion_reverts_por_fichero"], {"1": 1, "2": 1})
        self.assertEqual(data["sesiones_con_ediciones_revertidas"], 1)
        self.assertEqual(data["marcadas"][0]["ficheros"][0], {"file": path, "reverts": 2})


class ReceiptsTests(unittest.TestCase):
    def test_counts_by_closed_module_since_default(self) -> None:
        data = run(["receipts", "--project", FIXTURE_PROJECT, "--json"])["proyectos"][0]
        self.assertEqual(data["since"], "2026-10-01")
        self.assertEqual(data["modulos_cerrados"], 2)  # C_viejo cerró en septiembre; D_abierto no cerró
        self.assertEqual(data["refresh_approval_receipt_acciones"], 3)  # 2 de A + 1 de D desde octubre
        mods = {m["module"]: m for m in data["modulos"]}
        self.assertEqual((mods["A_primero"]["approval_record"], mods["A_primero"]["approval_refresh"],
                          mods["A_primero"]["review_seals"]), (1, 2, 2))
        self.assertTrue(mods["A_primero"]["approval_resealed"] and mods["A_primero"]["review_resealed"])
        self.assertEqual(mods["B_segundo"]["approval_seals"], 1)
        self.assertFalse(mods["B_segundo"]["approval_resealed"] or mods["B_segundo"]["review_resealed"])
        self.assertEqual(data["modulos_con_aprobacion_sellada_mas_de_una_vez"], 1)
        self.assertEqual(data["modulos_con_revision_sellada_mas_de_una_vez"], 1)

    def test_since_option_widens_the_window(self) -> None:
        data = run(["receipts", "--project", FIXTURE_PROJECT, "--json", "--since", "2026-09-01"])["proyectos"][0]
        self.assertEqual(data["modulos_cerrados"], 3)
        self.assertEqual(data["refresh_approval_receipt_acciones"], 4)

    def test_text_output(self) -> None:
        text = run(["receipts", "--project", FIXTURE_PROJECT])
        self.assertIn("2 módulos cerrados", text)
        self.assertIn("A_primero", text)


class ModuleCostTests(TmpCase):
    def test_tokens_turns_and_minutes_per_closed_module(self) -> None:
        cwd = FIXTURE_PROJECT

        def asst(ts: str, mid: str, usage: dict) -> dict:
            return {"type": "assistant", "timestamp": ts, "cwd": cwd, "uuid": mid,
                    "message": {"id": mid, "model": "claude-opus-5-5", "usage": usage, "content": []}}

        def use(a: int, b: int, c: int, d: int) -> dict:
            return {"input_tokens": a, "cache_read_input_tokens": b, "cache_creation_input_tokens": c, "output_tokens": d}

        folder = self.claude / "-carpeta"
        _write(folder / "s1.jsonl", [
            {"type": "user", "timestamp": "2026-10-02T09:00:00Z", "cwd": cwd, "uuid": "p0",
             "message": {"role": "user", "content": "antes del ciclo"}},
            asst("2026-10-02T09:01:00Z", "fuera", use(1, 1, 1, 1)),
            {"type": "user", "timestamp": "2026-10-02T10:10:00Z", "cwd": cwd, "uuid": "p1",
             "message": {"role": "user", "content": "turno 1 de A"}},
            asst("2026-10-02T10:11:00Z", "a1", use(10, 1000, 90, 50)),
            asst("2026-10-02T10:11:01Z", "a1", use(10, 1000, 90, 50)),  # repetido: cuenta una vez
            {"type": "user", "timestamp": "2026-10-02T10:30:00Z", "cwd": cwd, "uuid": "p2",
             "message": {"role": "user", "content": "turno 2 de A"}},
            asst("2026-10-02T10:31:00Z", "a2", use(0, 2000, 0, 25)),
            {"type": "user", "timestamp": "2026-10-03T09:15:00Z", "cwd": cwd, "uuid": "p3",
             "message": {"role": "user", "content": "turno de B"}},
            asst("2026-10-03T09:16:00Z", "b1", use(5, 500, 0, 5)),
        ])
        _write(folder / "s1" / "subagents" / "agent-1.jsonl", [
            asst("2026-10-02T10:20:00Z", "sub1", use(3, 300, 0, 7)),
            {"type": "assistant", "timestamp": "2026-10-02T10:25:00Z", "cwd": cwd, "uuid": "sub2-a",
             "message": {"id": "sub2", "model": "claude-sonnet-5-5", "usage": use(2, 200, 0, 3),
                         "content": [{"type": "text", "text": "x" * 400}]}},
        ])
        data = run(["module-cost", "--project", FIXTURE_PROJECT, "--claude-dir", str(self.claude),
                    "--codex-home", str(self.codex), "--since", "2026-10-01", "--json"])
        mods = {m["module"]: m for m in data["proyectos"][0]["modulos"]}
        self.assertEqual(set(mods), {"A_primero", "B_segundo"})
        a = mods["A_primero"]
        self.assertEqual(a["wall_min"], 120.0)
        self.assertEqual(a["turns"], 2)
        self.assertEqual(a["input_root"], 1100 + 2000)
        self.assertEqual(a["output_root"], 75)
        self.assertEqual(a["input_sub"], 303 + 202)
        self.assertEqual(a["output_sub"], 7 + 100)  # sub2: 400 chars / 4 = 100 > 3 provisionales
        self.assertEqual(mods["B_segundo"]["wall_min"], 60.0)
        self.assertEqual(mods["B_segundo"]["input_root"], 505)
        self.assertEqual(mods["B_segundo"]["turns"], 1)
        # activos: solo huecos de hasta 15 min entre eventos dentro de la ventana de A
        self.assertGreater(a["active_min"], 0)
        self.assertLessEqual(a["active_min"], a["wall_min"])

    def test_codex_activity_is_included(self) -> None:
        cwd = FIXTURE_PROJECT
        day = self.codex / "sessions" / "2026" / "10" / "03"
        _write(day / "rollout-a.jsonl", [
            {"timestamp": "2026-10-03T09:00:00Z", "type": "session_meta",
             "payload": {"id": "cx1", "session_id": "cx1", "cwd": cwd, "thread_source": "user"}},
            {"timestamp": "2026-10-03T09:05:00Z", "type": "event_msg", "payload": {"type": "task_started"}},
            {"timestamp": "2026-10-03T09:06:00Z", "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"total_tokens": 900},
                "last_token_usage": {"input_tokens": 800, "cached_input_tokens": 700, "output_tokens": 100}}}},
        ])
        data = run(["module-cost", "--project", FIXTURE_PROJECT, "--claude-dir", str(self.claude),
                    "--codex-home", str(self.codex), "--since", "2026-10-03", "--json"])
        b = {m["module"]: m for m in data["proyectos"][0]["modulos"]}["B_segundo"]
        self.assertEqual((b["turns"], b["input_root"], b["output_root"]), (1, 800, 100))


class IndependentModulesTests(unittest.TestCase):
    def test_conflicts_by_prefix_glob_and_not_evaluable(self) -> None:
        data = run(["independent-modules", "--project", FIXTURE_PROJECT, "--json"])
        project = data["proyectos"][0]
        self.assertIn("COTA SUPERIOR", data["nota"])
        phases = {f["fase"]: f for f in project["fases"]}
        p1 = phases["phase_01"]
        self.assertEqual(p1["modulos"], 5)
        self.assertEqual(p1["pares_en_conflicto"], 2)
        self.assertEqual(p1["independientes"], 1)  # solo m5: `*.md` y _project/** no se comparan
        pairs = {(c["a"], c["b"]) for c in p1["conflictos"]}
        self.assertEqual(pairs, {("m1", "m2"), ("m3", "m4")})
        kinds = {(c["a"], c["b"]): c["tipo"] for c in p1["conflictos"]}
        self.assertEqual(kinds[("m1", "m2")], "escribe/lee")
        self.assertEqual(kinds[("m3", "m4")], "escribe/escribe")
        self.assertEqual(phases["phase_02"]["modulos"], 1)  # m6 comparte ruta con m1 pero es otra fase
        self.assertEqual(phases["phase_02"]["pares_en_conflicto"], 0)
        ne = project["no_evaluables"]
        self.assertEqual(ne["lista_sin_entrada"], ["m7_sin_mapa"])
        self.assertEqual(ne["lista_sin_rutas"], ["m8_sin_rutas"])
        self.assertEqual((ne["sin_entrada_en_el_mapa"], ne["entrada_sin_lee_ni_escribe"]), (1, 1))
        self.assertNotIn("m9_formato_libre", json.dumps(project))  # el formato libre no se interpreta

    def test_path_prefix_rules(self) -> None:
        self.assertEqual(sm.path_prefix("app/api/*.py"), ("app", "api"))
        self.assertEqual(sm.path_prefix("./app/a.py:10-20"), ("app", "a.py"))
        self.assertIsNone(sm.path_prefix("*.md"))
        self.assertIsNone(sm.path_prefix("**/x.py"))
        self.assertIsNone(sm.path_prefix("_project/04_active_scope.md"))
        self.assertIsNone(sm.path_prefix("."))

    def test_missing_map_is_reported_not_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = run(["independent-modules", "--project", tmp, "--json"])["proyectos"][0]
            self.assertFalse(data["mapa_existe"])
            self.assertEqual(data["fases"], [])


class CodexEffortTests(TmpCase):
    @staticmethod
    def _turn(ts: str, turn_id: str, effort: str, total: int, inp: int, cached: int, model: str = "gpt-x") -> list[dict]:
        return [
            {"timestamp": ts, "type": "turn_context", "payload": {"turn_id": turn_id, "model": model, "effort": effort}},
            {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"total_tokens": total},
                "last_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": 10}}}},
        ]

    def _rollout(self, name: str, sid: str, turns: list[list[dict]]) -> None:
        rows = [{"timestamp": "2026-10-01T10:00:00Z", "type": "session_meta",
                 "payload": {"id": sid, "session_id": sid, "cwd": self.cwd, "thread_source": "user"}}]
        for t in turns:
            rows += t
        _write(self.codex / "sessions" / "2026" / "10" / "01" / f"rollout-{name}.jsonl", rows)

    def test_effort_change_vs_control(self) -> None:
        self._rollout("a", "sess-a", [
            self._turn("2026-10-01T10:01:00Z", "t1", "medium", 1100, 1000, 900),
            self._turn("2026-10-01T10:02:00Z", "t2", "high", 2300, 1200, 100),   # cambio: 0.9 -> 0.083
            self._turn("2026-10-01T10:03:00Z", "t3", "high", 3600, 1300, 200),   # control: 0.083 -> 0.154
        ])
        # hilo bifurcado: copia los mismos turnos, no debe contarse dos veces
        self._rollout("b", "sess-a-fork", [
            self._turn("2026-10-01T10:01:00Z", "t1", "medium", 1100, 1000, 900),
            self._turn("2026-10-01T10:02:00Z", "t2", "high", 2300, 1200, 100),
        ])
        data = run(["codex-effort-cache", "--codex-home", str(self.codex), "--json"])
        self.assertEqual(data["cambios"], 1)
        self.assertEqual(data["sesiones_con_cambio"], 1)
        self.assertEqual(data["pares_control"], 1)
        change = data["detalle"][0]
        self.assertEqual((change["de"], change["a"]), ("medium", "high"))
        self.assertEqual((change["cached_antes"], change["cached_despues"]), (900, 100))
        self.assertEqual(change["delta_ratio"], round(100 / 1200 - 0.9, 3))
        self.assertAlmostEqual(data["control_delta_ratio_mediana"], round(200 / 1300 - 100 / 1200, 3), places=3)
        self.assertNotIn("sin muestra", run(["codex-effort-cache", "--codex-home", str(self.codex)]))

    def test_without_effort_change_says_sin_muestra(self) -> None:
        self._rollout("a", "sess-a", [
            self._turn("2026-10-01T10:01:00Z", "t1", "medium", 1100, 1000, 900),
            self._turn("2026-10-01T10:02:00Z", "t2", "medium", 2300, 1200, 1100),
        ])
        text = run(["codex-effort-cache", "--codex-home", str(self.codex)])
        self.assertIn("sin muestra", text)
        data = run(["codex-effort-cache", "--codex-home", str(self.codex), "--json"])
        self.assertEqual(data["cambios"], 0)

    def test_model_change_is_not_an_effort_change(self) -> None:
        self._rollout("a", "sess-a", [
            self._turn("2026-10-01T10:01:00Z", "t1", "medium", 1100, 1000, 900, "gpt-x"),
            self._turn("2026-10-01T10:02:00Z", "t2", "high", 2300, 1200, 100, "gpt-y"),
        ])
        self.assertEqual(run(["codex-effort-cache", "--codex-home", str(self.codex), "--json"])["cambios"], 0)


class CliTests(unittest.TestCase):
    def test_session_metrics_script_entry_and_help(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sm.main(["independent-modules", "--project", FIXTURE_PROJECT]), 0)
        self.assertIn("phase_01", out.getvalue())
        self.assertEqual(set(sm.COMMANDS), set(sc.SUBCOMMANDS))

    def test_old_cli_options_still_parse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = sc.main(["--agent", "claude", "--project", tmp, "--since", "2026-01-01", "--session", "zz",
                                "--json", "--codex-home", tmp + "/c", "--claude-dir", tmp + "/p"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue()), [])


class TestCommandRuleTests(unittest.TestCase):
    """5.3.0 L5b (FW-352): qué orden cuenta como lanzar un test. Se corrige la regla, no caso a caso."""

    MARKED = (
        # Lo que ya se reconocía y no puede perderse.
        "python -m pytest", "python3 -m pytest tests/ -q", "python3 -m unittest tests.test_auth",
        ".venv/bin/python -m pytest tests/ -q", "python3.11 -m pytest", "python -u -m pytest",
        "pytest", "pytest tests/ -q", "py.test tests", "vitest run", "jest --watch=false", "tox -e py312",
        "npm test", "npm run test", "yarn test", "pnpm test", "pnpm run test", "node --test", "cargo test", "go test ./...",
        "cd app && pytest", "make build; pytest", "make build\npytest", "make build || pytest", "(pytest tests)",
        "cd app && (pytest -q)", "pytest -q | tail -5", "make | pytest",
        "FOO=1 pytest", "PYTHONPATH=. DEBUG=1 pytest -x", "env FOO=1 pytest", "env -u FOO pytest", "time pytest",
        "sudo pytest", "sudo -u ci pytest", "uv run pytest", "uv run --with pytest-cov pytest", "poetry run pytest",
        "pipenv run pytest", "npx jest", "npx --no-install vitest", "pnpm exec vitest", "yarn jest", "yarn run jest",
        "python -m coverage run -m pytest", "coverage run -m pytest", "timeout 60 pytest", "xargs pytest",
        # (a) El ejecutor por ruta absoluta o relativa.
        "/ruta/.venv/bin/pytest tests/ -q", "./node_modules/.bin/jest", "/usr/bin/env pytest",
        "cd app && /ruta/.venv/bin/pytest -x", "FOO=1 /ruta/.venv/bin/pytest", "uv run /ruta/bin/pytest",
        "bash -c 'cd app && pytest'",
        # Intérprete entre comillas (con espacios en la ruta) o en una variable, con opciones antes de -m.
        '"/ruta con espacios/.venv/bin/python" -m pytest tests/unit -q',
        "'/ruta con espacios/bin/python3.12' -m pytest",
        'cd "/a b/c" && "/a b/.venv/bin/python" -m pytest tests',
        "$PY -m pytest tests -q", '"$PY" -m pytest', "${PY} -m pytest", "$P -B -m pytest -q", "$p -m pytest --version",
        "python3 -B -m pytest", "python -W error -m pytest", "$P -B -m pytest",
        "for p in a b; do $p -m pytest --version; done",
        'PYTHONPATH="$PWD/src" "$PY" -m pytest', 'PYTHONPATH=$S/src "$V" -m pytest',
        'time PYTHONPATH="$PWD/src" "$PY" -m pytest', 'env -C "$M" "$PY" -m pytest', "run(){ $PY -m pytest; }; run",
        "PY=.venv/bin/python; $PY -B -m pytest -q", "[ -x $PY ] || PY=python3; $PY -m pytest",
        'sh -c "$PY -m pytest"', "bash -lc 'python3 -m pytest'",
        # -m unittest con las mismas formas que -m pytest (FW-354).
        "python3 -B -m unittest discover -s tests", ".venv/bin/python -B -m unittest", "$PY -m unittest", "$P -B -m unittest",
        "python3.11 -m unittest", '"$PY" -B -m unittest tests.test_x', '"/a b/.venv/bin/python" -m unittest discover',
        "python -W error -m unittest", "time python3 -B -m unittest tests.test_a 2>&1 | tail -5",
        # Un ejecutor suelto tras un envoltorio que ejecuta dentro de otro entorno (FW-354).
        "docker compose exec app pytest -q", "docker exec -it app pytest", "docker run --rm img pytest",
        "podman exec app pytest", "kubectl exec pod -- pytest", "ssh host pytest -q", "nix develop -c pytest",
        "hatch run pytest", "pdm run pytest", "rye run pytest", "docker exec app python -m pytest",
        "docker exec app python3 -B -m unittest", "docker exec app env FOO=1 pytest", "ssh host python3 -m pytest tests",
        "docker compose run --rm app jest", "kubectl exec -it pod -- /venv/bin/pytest",
        "docker exec -u root app pytest", "docker exec -e FOO=1 app pytest -x", "kubectl exec -n ns pod -- pytest",
        "ssh -p 22 host pytest", "ssh user@host env FOO=1 pytest", "docker run --rm -v /a:/b img python3 -m pytest",
        "docker compose exec app python -m pytest", "docker exec app time pytest",
        # pytest.main / unittest.main dentro de un programa de python inline (FW-354).
        'python3 -c "import pytest; pytest.main([\'-q\'])"', "python -c 'import unittest; unittest.main()'",
        "python3 - <<'EOF'\nimport pytest\nraise SystemExit(pytest.main(['tests']))\nEOF",
        "python3 <<EOF\nimport unittest\nunittest.main(module='x')\nEOF",
        "$PY - <<'PY'\nimport pytest; pytest.main([])\nPY", 'for P in 1 2; do R=$(.venv/bin/python - "$P" <<\'PY\' 2>&1 | tail -3\npytest.main([])\nPY\n); done', "cd x && python3 -B - <<'EOF'\nimport pytest\npytest.main(['-q'])\nEOF",
        # Un separador dentro de comillas no corta, y el resto de la orden sigue contando.
        'echo "a;b" && pytest', "grep 'a|b' x.txt; pytest", 'cd "dir (v2)" && pytest',
    )
    NOT_MARKED = (
        # (b) Un programa que solo nombra al ejecutor.
        "python3 herramienta.py pytest", "./informe.sh --runner pytest", "python3 decode.py jest",
        "python -m pip install pytest", "pip install pytest", "npm install jest", "node script.js vitest",
        "make pytest", "ruby run.rb --tool tox",
        # Un separador dentro de comillas no abre una etapa ni un segmento nuevos.
        "pgrep -fl 'ci_smoke.sh|unittest|pytest' | head", 'ps aux | grep -E "sleep 1[0-9]|until grep|pytest" | grep -v grep',
        "./tool 'x | pytest'", './tool "x && pytest"', "./tool '(pytest)'", './tool "a; pytest"', "./tool 'a || jest'",
        # Lo que lanza un ejecutor dentro de otro entorno solo cuenta si ese ejecutor es el comando.
        "ssh host grep pytest", "docker exec app which pytest", "docker build -t pytest .",
        "docker run --rm img echo pytest", "docker exec app python3 herramienta.py pytest", "kubectl get pods",
        "docker pull ghcr.io/org/pytest", "ssh host cat pytest.ini", "nix develop -c echo pytest",
        # Tras el envoltorio y su destino, solo cuenta el ejecutor que es el comando remoto (FW-354, ronda 4).
        "ssh host grep -rn pytest tests", "docker exec app pip install pytest", "ssh ci pgrep -f pytest",
        "docker exec app python3 -m pip install pytest", "docker run --rm --name pytest img ls",
        "docker exec app ls pytest", "kubectl exec pod -- ls pytest", "ssh host echo pytest", "ssh host which pytest",
        "docker exec app python3 -c 'import pytest'", "docker run img sh -c 'echo pytest'", "ssh host tail -f pytest.log",
        "kubectl exec pod -- python3 herramienta.py pytest", "nix develop -c python3 herramienta.py pytest",
        # pytest.main / unittest.main solo cuentan dentro de un programa de python inline, no al buscarlos o escribirlos.
        'grep "pytest.main(" tests', "rg pytest.main", 'echo "pytest.main([])"', "cat <<'EOF'\nimport pytest\npytest.main([])\nEOF",
        "python3 script.py <<'EOF'\npytest.main([])\nEOF", 'python3 -c "print(1)"', "python3 - <<'EOF'\nprint(1)\nEOF",
        'sed -i "s/pytest.main(/x(/" tests/a.py',
        "python3 - <<'EOF'\nprint(1)\nEOF\ncat > t.py <<'EOF'\nunittest.main()\nEOF",  # el cuerpo es solo el del intérprete
        # -m solo cuenta tras un intérprete de python o una variable.
        "git commit -m pytest", "otra_cosa -m pytest", "otra_cosa -B -m unittest", "$PY pytest", "$PY herramienta.py pytest",
        '$PY -c "import pytest"', "$PY script.py -m pytest", "python3 script.py -m pytest", "python3 -m pip install pytest",
        '"$PY" -m pip show pytest', "which pytest", "pip3 show pytest", "ls venv/bin/pytest", 'python3 -c "import pytest"',
        # Lo que ya no se reconocía.
        "ls", "echo pytest", "grep -rn pytest tests", "cat pytest.ini", "which pytest", "python3 -m pip list",
        "python3 -m unittest_extra", "npm run build", "cargo build", "go build ./...",
        "mypy src", "",
    )

    def test_lo_que_marca(self) -> None:
        for command in self.MARKED:
            with self.subTest(command=command):
                self.assertTrue(sm._is_test_command(command))

    def test_lo_que_no_marca(self) -> None:
        for command in self.NOT_MARKED:
            with self.subTest(command=command):
                self.assertFalse(sm._is_test_command(command))

    def test_los_heredocs_de_una_misma_linea_se_resuelven_en_orden(self) -> None:
        """Los cuerpos de varios heredocs de una línea van uno tras otro: cada uno se examina una sola vez."""
        self.assertFalse(sm._is_test_command("python3 - <<A ; cat <<B\nprint(1)\nA\npytest.main([])\nB"))
        self.assertTrue(sm._is_test_command("cat <<A ; python3 - <<B\nx\nA\npytest.main([])\nB"))
        self.assertTrue(sm._is_test_command("python3 - <<A ; cat <<B\npytest.main([])\nA\ny\nB"))
        self.assertFalse(sm._is_test_command("python3 - <<A\nprint(1)\n"))  # sin cierre: el resto es el cuerpo
        self.assertTrue(sm._is_test_command("python3 - <<A\npytest.main([])\n"))
        self.assertTrue(sm._is_test_command("python3 - <<A\nx\n  A  \ny\nA\n: ; python3 - <<B\nunittest.main()\nB"))
        self.assertFalse(sm._is_test_command("python3 - <<A ; cat <<AA\nx\nA\npytest.main([])\nAA"))

    def test_la_busqueda_tras_un_envoltorio_de_entorno_no_se_reabre(self) -> None:
        """Una sola pasada: tres niveles de `bash -c` sobre una cadena de envoltorios no multiplican las evaluaciones."""
        chain = "ssh " + "env ssh " * 6
        level2 = chain + "bash -c x"
        level1 = chain + "bash -c '" + level2 + "'"
        level0 = chain + 'bash -c "' + level1.replace("'", "\\'") + '"'
        calls = [0]
        real = sm._is_test_command

        def spy(command, depth=0):
            calls[0] += 1
            return real(command, depth)

        with mock.patch.object(sm, "_is_test_command", spy):
            started = time.monotonic()
            sm._is_test_command(level0)
        self.assertLessEqual(calls[0], 50, calls[0])
        self.assertLess(time.monotonic() - started, 2)
        calls[0] = 0
        with mock.patch.object(sm, "_is_test_command", spy):
            sm._is_test_command(level2)
        self.assertLessEqual(calls[0], 50, calls[0])

    def test_un_argumento_enorme_no_cuesta_tiempo_cuadratico(self) -> None:
        import time

        huge = (
            "python3 decode.py " + "A" * 300_000,
            "pytest " + "A" * 300_000,
            "./informe.sh " + "A " * 150_000,
            "FOO=" + "A" * 300_000 + " pytest",
            "A=1 " * 75_000 + "pytest",
            "env " * 75_000 + "pytest",
            "uv run " * 40_000 + "pytest",
            "(" * 300_000 + "pytest",
            "npm " + " " * 300_000 + "x",
            'python3 decode.py "' + "A" * 300_000 + '"',
            "./tool '" + "A " * 150_000 + "| pytest'",
            './tool "' + "A;" * 150_000 + '"',
            "./tool '" + "A" * 300_000,  # comilla sin cerrar
            './tool "' + "A" * 300_000,
            "${" * 100_000,
            "'" * 300_000,
            '"\\"' * 100_000,
            "a|" * 20_000 + "b",
            "a | " * 20_000 + "pytest",
            "a && " * 20_000 + "b",
            "(" * 100_000 + ")" * 100_000,
            "docker exec " + "a " * 150_000 + "pytest",
            "ssh host " + "grep " * 60_000 + "pytest",
            "python3 -c 'pytest.main(" + "A" * 300_000,
            "python3 - <<'EOF'\n" + "x\n" * 100_000 + "EOF",
            "docker " * 50_000 + "exec pytest",
            "ssh " + "env ssh " * 6 + "bash -c '" + "x " * 500_000 + "'",  # un guion de 1 MB tras una cadena de envoltorios
            ": 'pytest.main(' ; " + "python3 - <<X ; " * 3000 + "\n" + "y" * 1_000_000,  # 3000 heredocs, 1 MB de cuerpo
            ": 'pytest.main(' ; " + "python3 - <<X1 ; " * 50 + "\n" + "y\n" * 500_000,
            ": 'pytest.main(' ; " + "python3 - <<X ; " * 20_000 + "y" * 4_000_000,  # sin salto de línea tras los heredocs
            ": 'pytest.main(' ; cat " + " ".join(f"<<d{i:x}" for i in range(100_000)) + "\n",  # 100 000 delimitadores distintos
            "python3 - <<y\n" + "y" * 1_000_000 + " pytest.main(",  # el delimitador se repite un millón de veces en una línea
        )
        for command in huge:
            with self.subTest(command=command[:30]):
                started = time.monotonic()
                sm._is_test_command(command)
                self.assertLess(time.monotonic() - started, 2)


if __name__ == "__main__":
    unittest.main()
