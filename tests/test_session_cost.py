#!/usr/bin/env python3
"""Regresiones del medidor de coste de sesión (solo lectura)."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import session_cost as sc  # noqa: E402

PROJECT = "Mi Proyecto/con espacios"  # sufijo relativo al tmp: la ruta real lleva espacios
SID = "01a0aaaa-0000-7000-8000-000000000001"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _meta(cwd: str, thread_id: str, source: str, ts: str, session_id: str = SID) -> dict:
    return {
        "timestamp": ts, "type": "session_meta",
        "payload": {"id": thread_id, "session_id": session_id, "cwd": cwd, "thread_source": source},
    }


def _call(ts: str, name: str, call_id: str, args: str, kind: str = "function_call", ns: str | None = None) -> dict:
    payload = {"type": kind, "name": name, "call_id": call_id,
               "arguments" if kind == "function_call" else "input": args}
    if ns:
        payload["namespace"] = ns
    return {"timestamp": ts, "type": "response_item", "payload": payload}


def _out(ts: str, call_id: str, kind: str = "function_call_output") -> dict:
    return {"timestamp": ts, "type": "response_item", "payload": {"type": kind, "call_id": call_id}}


def _event(ts: str, ptype: str, **extra) -> dict:
    return {"timestamp": ts, "type": "event_msg", "payload": {"type": ptype, **extra}}


def _tokens(ts: str, n: int) -> dict:
    return _event(ts, "token_count", info={"last_token_usage": {"input_tokens": n}})


class CodexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cwd = str(self.tmp / PROJECT)
        Path(self.cwd).mkdir(parents=True)
        self.home = self.tmp / "codex"
        day = self.home / "sessions" / "2026" / "09" / "30"
        _write(day / "rollout-root.jsonl", [
            _meta(self.cwd, SID, "user", "2026-09-30T10:00:00Z"),
            _event("2026-09-30T10:00:01Z", "task_started"),
            _tokens("2026-09-30T10:00:02Z", 39000),
            _call("2026-09-30T10:01:00Z", "exec", "c1", "cat GUARDRAILS_MASTER.md CLAUDE.md", "custom_tool_call"),
            _out("2026-09-30T10:01:01Z", "c1", "custom_tool_call_output"),
            _call("2026-09-30T10:02:00Z", "spawn_agent", "c2", "{}", ns="collaboration"),
            _call("2026-09-30T10:03:00Z", "wait_agent", "c3", '{"timeout_ms":10000}', ns="collaboration"),
            _out("2026-09-30T10:08:00Z", "c3"),
            _call("2026-09-30T10:08:10Z", "exec", "c4",
                  "python3 .agent/scripts/state_manager.py mutate x", "custom_tool_call"),
            _call("2026-09-30T10:08:20Z", "exec", "c5", "python3 .agent/scripts/state_manager.py query y", "custom_tool_call"),
            {"timestamp": "2026-09-30T10:09:00Z", "type": "compacted", "payload": {"message": ""}},
            _tokens("2026-09-30T10:10:00Z", 120000),
            _event("2026-09-30T10:10:05Z", "task_started"),
            _tokens("2026-09-30T10:11:00Z", 50000),
        ])
        _write(day / "rollout-child.jsonl", [
            _meta(self.cwd, "child-1", "subagent", "2026-09-30T10:02:10Z"),
            _call("2026-09-30T10:02:20Z", "exec", "d1", "cat CLAUDE.md", "custom_tool_call"),
        ])
        _write(day / "rollout-guardian.jsonl", [
            _meta(self.cwd, "guard-1", "guardian_review", "2026-09-30T10:04:00Z"),
            _event("2026-09-30T10:04:01Z", "task_started"),
            _event("2026-09-30T10:04:02Z", "task_started"),
            _event("2026-09-30T10:04:03Z", "task_started"),
        ])
        _write(day / "rollout-otro.jsonl", [
            _meta(str(self.tmp / "otro"), "other", "user", "2026-09-30T10:00:00Z", "other"),
            _event("2026-09-30T10:00:01Z", "task_started"),
        ])

    def test_root_children_and_reviewer_are_grouped(self) -> None:
        sessions = sc.collect_codex(self.home, self.cwd, None)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.session_id, SID)
        self.assertEqual(s.files, 3)
        self.assertEqual(s.turns, 2)
        self.assertEqual(s.subagents, 1)
        self.assertEqual(s.compactions, 1)
        self.assertEqual((s.context_initial, s.context_max), (39000, 120000))
        self.assertEqual(s.wait_min, 5.0)
        self.assertEqual(s.reviewer_evals, 3)
        self.assertEqual(s.doc_reads, {"GUARDRAILS_MASTER.md": 1, "CLAUDE.md": 2})
        self.assertEqual(s.state_calls, {"mutate": 1, "query": 1})
        self.assertEqual(s.duration_min, 11.0)

    def test_project_filter_uses_cwd_with_spaces(self) -> None:
        self.assertEqual(sc.collect_codex(self.home, self.cwd + "/sub", None), [])
        self.assertEqual(len(sc.collect_codex(self.home, str(self.tmp), None)), 2)

    def test_archived_root_is_found(self) -> None:
        (self.home / "archived_sessions").mkdir()
        root = self.home / "sessions" / "2026" / "09" / "30" / "rollout-root.jsonl"
        root.rename(self.home / "archived_sessions" / "rollout-root.jsonl")
        sessions = sc.collect_codex(self.home, self.cwd, None)
        self.assertEqual(sessions[0].turns, 2)
        self.assertEqual(sessions[0].reviewer_evals, 3)

    def test_idle_gaps_do_not_count_as_active(self) -> None:
        day = self.home / "sessions" / "2026" / "09" / "30"
        _write(day / "rollout-root.jsonl", [
            _meta(self.cwd, SID, "user", "2026-09-30T10:00:00Z"),
            _event("2026-09-30T10:05:00Z", "task_started"),
            _event("2026-09-30T20:00:00Z", "task_started"),
            _event("2026-09-30T20:03:00Z", "task_started"),
        ])
        s = sc.collect_codex(self.home, self.cwd, None)[0]
        self.assertEqual(s.active_min, 8.0)
        self.assertGreater(s.duration_min, 600)


class ClaudeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cwd = str(self.tmp / PROJECT)
        Path(self.cwd).mkdir(parents=True)
        self.root = self.tmp / "claude"
        folder = self.root / "-nombre-ambiguo-con-guiones"
        use = lambda tid, ts, name, inp: {  # noqa: E731
            "type": "assistant", "timestamp": ts, "cwd": self.cwd,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
                        "usage": {"input_tokens": 2, "cache_read_input_tokens": 1000,
                                  "cache_creation_input_tokens": 500}},
        }
        _write(folder / "abc123.jsonl", [
            {"type": "user", "timestamp": "2026-09-30T10:00:00Z", "cwd": self.cwd,
             "message": {"role": "user", "content": "hola"}},
            use("t1", "2026-09-30T10:00:10Z", "Read", {"file_path": "/x/AGENTS.md"}),
            use("t2", "2026-09-30T10:00:20Z", "Bash", {"command": "python3 .agent/scripts/state_manager.py mutate z"}),
            use("t3", "2026-09-30T10:00:30Z", "Agent", {"prompt": "p"}),
            use("t4", "2026-09-30T10:01:00Z", "Monitor", {}),
            {"type": "user", "timestamp": "2026-09-30T10:04:00Z", "cwd": self.cwd,
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t4"}]}},
            {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-09-30T10:05:00Z", "cwd": self.cwd},
            {"type": "user", "timestamp": "2026-09-30T10:06:00Z", "cwd": self.cwd, "isCompactSummary": True,
             "message": {"role": "user", "content": "resumen"}},
        ])
        (folder / "abc123" / "subagents").mkdir(parents=True)
        (folder / "abc123" / "subagents" / "agent-1.jsonl").write_text("{}\n")
        _write(folder / "zzz.jsonl", [
            {"type": "user", "timestamp": "2026-09-30T10:00:00Z", "cwd": str(self.tmp / "otro"),
             "message": {"role": "user", "content": "x"}},
        ])

    def test_claude_session_metrics(self) -> None:
        sessions = sc.collect_claude(self.root, self.cwd, None)
        self.assertEqual([s.session_id for s in sessions], ["abc123"])
        s = sessions[0]
        self.assertEqual(s.turns, 1)  # ni tool_result ni resumen de compactación
        self.assertEqual(s.subagents, 1)
        self.assertEqual(s.files, 2)
        self.assertEqual(s.compactions, 1)
        self.assertEqual(s.context_initial, 1502)
        self.assertEqual(s.wait_min, 3.0)
        self.assertEqual(s.doc_reads, {"AGENTS.md": 1})
        self.assertEqual(s.state_calls, {"mutate": 1})


class CliTests(unittest.TestCase):
    def test_json_and_current_line_empty_without_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = ["--codex-home", tmp + "/c", "--claude-dir", tmp + "/p"]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(sc.main(base + ["--json"]), 0)
            self.assertEqual(json.loads(out.getvalue()), [])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(sc.main(base + ["--current-line", "--project", tmp]), 0)
            self.assertEqual(out.getvalue(), "")

    def test_current_line_summarises_latest_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp) / PROJECT)
            Path(cwd).mkdir(parents=True)
            _write(Path(tmp) / "p" / "dir" / "s.jsonl", [
                {"type": "user", "timestamp": "2026-09-30T10:00:00Z", "cwd": cwd,
                 "message": {"role": "user", "content": "hola"}},
            ])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                sc.main(["--codex-home", tmp + "/c", "--claude-dir", tmp + "/p", "--current-line", "--project", cwd,
                         "--max-age-min", "0"])
            self.assertIn("Coste de sesión (claude)", out.getvalue())
            out = io.StringIO()  # por defecto, una sesión antigua no se atribuye a la actual
            with contextlib.redirect_stdout(out):
                sc.main(["--codex-home", tmp + "/c", "--claude-dir", tmp + "/p", "--current-line", "--project", cwd])
            self.assertEqual(out.getvalue(), "")


def _asst(ts: str, cwd: str, msg_id: str, model: str, usage: dict, blocks: list[dict], uuid: str | None = None) -> dict:
    row = {"type": "assistant", "timestamp": ts, "cwd": cwd,
           "message": {"id": msg_id, "model": model, "content": blocks, "usage": usage}}
    if uuid:
        row["uuid"] = uuid
    return row


def _usage(inp: int, cread: int, ccreate: int, out: int) -> dict:
    return {"input_tokens": inp, "cache_read_input_tokens": cread, "cache_creation_input_tokens": ccreate,
            "output_tokens": out}


class ByModelTests(unittest.TestCase):
    """5.3.0 L1: deduplicación por message.id, compactaciones por uuid, --by-model y ctx_medio."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cwd = str(self.tmp / PROJECT)
        Path(self.cwd).mkdir(parents=True)
        self.root = self.tmp / "claude"
        folder = self.root / "-carpeta"
        text = [{"type": "text", "text": "hola"}]
        tool = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]
        m1_a = _asst("2026-10-01T10:00:10Z", self.cwd, "msg_1", "claude-opus-5-5", _usage(10, 1000, 100, 50), text, "u-a")
        m1_b = _asst("2026-10-01T10:00:11Z", self.cwd, "msg_1", "claude-opus-5-5", _usage(10, 1000, 100, 50), tool, "u-b")
        m2 = _asst("2026-10-01T10:05:00Z", self.cwd, "msg_2", "claude-opus-5-5", _usage(20, 2000, 0, 60), text, "u-c")
        synthetic = _asst("2026-10-01T10:06:00Z", self.cwd, "msg_3", "<synthetic>", _usage(0, 0, 0, 0), text, "u-d")
        boundary = {"type": "system", "subtype": "compact_boundary", "uuid": "cb-1", "timestamp": "2026-10-01T10:07:00Z",
                    "cwd": self.cwd}
        _write(folder / "sess1.jsonl", [
            {"type": "user", "timestamp": "2026-10-01T10:00:00Z", "cwd": self.cwd, "uuid": "u-0",
             "message": {"role": "user", "content": "hola"}},
            m1_a, m1_b, m2, synthetic, boundary,
            # al reanudar, Claude copia lo anterior con los mismos ids y uuids
            m1_a, m1_b, m2, boundary,
        ])
        sub = folder / "sess1" / "subagents"
        haiku = _asst("2026-10-01T10:02:00Z", self.cwd, "msg_h", "claude-haiku-5-5", _usage(5, 500, 0, 10), text)
        _write(sub / "agent-1.jsonl", [haiku, haiku])
        _write(sub / "agent-2.jsonl", [
            _asst("2026-10-01T10:03:00Z", self.cwd, "msg_s", "claude-sonnet-5-5", _usage(7, 700, 70, 20), text),
        ])

    def test_by_model_counts_each_message_once_and_separates_subagents(self) -> None:
        s = sc.collect_claude(self.root, self.cwd, None, with_models=True)[0]
        root = s.by_model["root"]
        self.assertEqual(set(root), {"claude-opus-5-5"})  # el <synthetic> no cuenta
        self.assertEqual(root["claude-opus-5-5"], {
            "input": 30, "cache_read": 3000, "cache_creation": 100, "output": 110, "turns": 2, "input_total": 3130})
        subs = s.by_model["subagents"]
        self.assertEqual(subs["claude-haiku-5-5"]["input_total"], 505)  # repetido dos veces, una sola cuenta
        self.assertEqual(subs["claude-haiku-5-5"]["turns"], 1)
        self.assertEqual(subs["claude-sonnet-5-5"]["input_total"], 777)
        self.assertEqual(s.by_model["totals"]["subagents"]["input_total"], 1282)
        self.assertEqual(s.by_model["totals"]["subagents"]["output"], 30)
        self.assertEqual(s.by_model["totals"]["root"]["input_total"], 3130)

    def test_without_flag_by_model_stays_empty_but_ctx_medio_is_set(self) -> None:
        s = sc.collect_claude(self.root, self.cwd, None)[0]
        self.assertEqual(s.by_model, {})
        self.assertEqual(s.ctx_medio, 1565)  # media de 1110 y 2020 por mensaje deduplicado
        self.assertEqual(s.context_max, 2020)  # el significado de context_max no cambia

    def test_duplicated_compaction_boundary_counts_once(self) -> None:
        self.assertEqual(sc.collect_claude(self.root, self.cwd, None)[0].compactions, 1)

    def test_by_model_cli_text_and_json(self) -> None:
        base = ["--codex-home", str(self.tmp / "c"), "--claude-dir", str(self.root), "--project", self.cwd, "--by-model"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sc.main(base), 0)
        text = out.getvalue()
        self.assertIn("claude-opus-5-5", text)
        self.assertIn("subagentes", text)
        self.assertIn("pared", text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sc.main(base + ["--json"])
        data = json.loads(out.getvalue())
        self.assertEqual(data[0]["by_model"]["totals"]["root"]["input_total"], 3130)
        self.assertIn("duration_min", data[0])
        self.assertIn("active_min", data[0])

    def test_table_gains_ctx_medio_column_and_old_fields_remain(self) -> None:
        s = sc.collect_claude(self.root, self.cwd, None)[0]
        table = sc.format_table([s])
        self.assertTrue(table.splitlines()[0].startswith("agente  sesión    inicio"))
        self.assertIn("ctx_medio", table.splitlines()[0])
        for name in ("agent", "session_id", "cwd", "duration_min", "active_min", "turns", "subagents", "wait_min",
                     "wait_by_tool_min", "compactions", "context_initial", "context_max", "doc_reads", "state_calls",
                     "reviewer_evals", "files"):
            self.assertTrue(hasattr(s, name), name)

    def test_current_line_unchanged_in_shape(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sc.main(["--codex-home", str(self.tmp / "c"), "--claude-dir", str(self.root), "--current-line",
                     "--project", self.cwd, "--max-age-min", "0"])
        line = out.getvalue()
        self.assertTrue(line.startswith("Coste de sesión (claude):"))
        self.assertIn("compactaciones, contexto máx", line)
        self.assertTrue(line.rstrip().endswith("llamadas a state_manager."))

    def test_subcommand_delegation(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sc.main(["codex-effort-cache", "--codex-home", str(self.tmp / "nada")])
        self.assertEqual(code, 0)
        self.assertIn("sin muestra", out.getvalue())

    def test_duplicated_user_turns_and_tool_use_after_compaction_count_once(self) -> None:
        """FW-347 / FW-312: al compactar o reanudar, Claude repite eventos con el mismo uuid/tool_use.id."""
        folder = self.root / "-carpeta"
        user_1 = {"type": "user", "timestamp": "2026-10-01T10:00:00Z", "cwd": self.cwd, "uuid": "u-p1",
                  "message": {"role": "user", "content": "turno uno"}}
        user_2 = {"type": "user", "timestamp": "2026-10-01T10:02:00Z", "cwd": self.cwd, "uuid": "u-p2",
                  "message": {"role": "user", "content": "turno dos"}}
        asst_agent = _asst("2026-10-01T10:00:10Z", self.cwd, "msg_ag", "claude-opus-5-5", _usage(10, 500, 0, 20),
                           [{"type": "tool_use", "id": "tu-agent-1", "name": "Agent", "input": {"prompt": "x"}}], "u-a1")
        asst_read = _asst("2026-10-01T10:00:20Z", self.cwd, "msg_rd", "claude-opus-5-5", _usage(10, 600, 0, 20),
                          [{"type": "tool_use", "id": "tu-read-1", "name": "Read", "input": {"file_path": "/x/AGENTS.md"}}], "u-a2")
        boundary = {"type": "system", "subtype": "compact_boundary", "uuid": "cb-1", "timestamp": "2026-10-01T10:03:00Z",
                    "cwd": self.cwd}
        _write(folder / "sess_dedup.jsonl", [
            user_1, asst_agent, asst_read, user_2, boundary,
            # Repetidos tras compactar con el mismo uuid / tool_use.id:
            user_1, asst_agent, asst_read, user_2, boundary,
        ])
        sessions = {s.session_id: s for s in sc.collect_claude(self.root, self.cwd, None)}
        s = sessions["sess_dedup"]
        self.assertEqual(s.turns, 2)
        self.assertEqual(s.subagents, 1)
        self.assertEqual(s.doc_reads, {"AGENTS.md": 1})
        self.assertEqual(s.compactions, 1)

    def test_provisional_output_tokens_estimated_from_content_across_message_lines(self) -> None:
        """FW-347: cuando usage.output_tokens es el valor provisional de streaming, se estima por contenido."""
        folder = self.root / "-carpeta"
        sub = folder / "sess1" / "subagents"
        # Mismo message.id partido en dos líneas (thinking + tool_use) con output_tokens provisional = 8,
        # más una copia repetida con el mismo uuid tras reanudar.
        line_think = _asst("2026-10-01T10:04:00Z", self.cwd, "msg_stream", "claude-sonnet-5-5",
                           _usage(10, 1000, 0, 8), [{"type": "thinking", "thinking": "t" * 400}], "u-st-1")
        line_tool = _asst("2026-10-01T10:04:01Z", self.cwd, "msg_stream", "claude-sonnet-5-5",
                          _usage(10, 1000, 0, 8),
                          [{"type": "tool_use", "id": "tu-w", "name": "Write", "input": {"content": "c" * 800}}], "u-st-2")
        _write(sub / "agent-2.jsonl", [line_think, line_tool, line_think, line_tool])
        s = sc.collect_claude(self.root, self.cwd, None, with_models=True)[0]
        sonnet = s.by_model["subagents"]["claude-sonnet-5-5"]
        self.assertEqual(sonnet["turns"], 1)
        # 400 chars de thinking + len("Write") + len(json de input > 800) => > 1200 chars => > 300 tokens (>> 8)
        self.assertGreaterEqual(sonnet["output"], 300)
        self.assertLess(sonnet["output"], 400)


class CodexByModelTests(unittest.TestCase):
    def test_codex_tokens_by_model_root_and_children(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp) / PROJECT)
            Path(cwd).mkdir(parents=True)
            home = Path(tmp) / "codex"
            day = home / "sessions" / "2026" / "10" / "01"

            def ctx(ts: str, model: str) -> dict:
                return {"timestamp": ts, "type": "turn_context", "payload": {"model": model, "effort": "medium"}}

            def count(ts: str, total: int, last_in: int, cached: int, out: int) -> dict:
                return _event(ts, "token_count", info={
                    "total_token_usage": {"total_tokens": total},
                    "last_token_usage": {"input_tokens": last_in, "cached_input_tokens": cached,
                                         "cache_write_input_tokens": 0, "output_tokens": out}})

            _write(day / "rollout-root.jsonl", [
                _meta(cwd, SID, "user", "2026-10-01T10:00:00Z"),
                _event("2026-10-01T10:00:01Z", "task_started"),
                ctx("2026-10-01T10:00:02Z", "gpt-x"),
                count("2026-10-01T10:00:03Z", 1100, 1000, 800, 100),
                count("2026-10-01T10:00:04Z", 1100, 1000, 800, 100),  # repetido: mismo total acumulado
                count("2026-10-01T10:01:00Z", 3300, 2000, 1800, 200),
            ])
            _write(day / "rollout-child.jsonl", [
                _meta(cwd, "child-1", "subagent", "2026-10-01T10:00:30Z"),
                ctx("2026-10-01T10:00:31Z", "gpt-y"),
                count("2026-10-01T10:00:40Z", 550, 500, 0, 50),
            ])
            s = sc.collect_codex(home, cwd, None, with_models=True)[0]
            root = s.by_model["root"]["gpt-x"]
            self.assertEqual((root["input_total"], root["cache_read"], root["input"], root["output"], root["turns"]),
                             (3000, 2600, 400, 300, 2))
            self.assertEqual(s.by_model["subagents"]["gpt-y"]["input_total"], 500)
            self.assertEqual(s.ctx_medio, 1500)
            plain = sc.collect_codex(home, cwd, None)[0]
            self.assertEqual(plain.by_model, {})


if __name__ == "__main__":
    unittest.main()
