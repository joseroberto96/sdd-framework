#!/usr/bin/env python3
"""tests/test_token_baseline.py — Tests unitarios para la herramienta token_baseline.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.tools.token_baseline import (
    categorize_tool_use,
    classify_bash_command,
    find_engine_read_episodes,
    format_markdown_report,
    inspect_disk_state_sizes,
    parse_session_file,
    run_full_analysis,
    text_content,
    text_len,
)


class TestTokenBaselineTool(unittest.TestCase):
    def test_text_helpers(self):
        self.assertEqual(text_len("hello"), 5)
        self.assertEqual(text_len([{"text": "foo"}, {"text": "bar"}]), 6)
        self.assertEqual(text_len(123), 0)

        self.assertEqual(text_content("single string"), "single string")
        self.assertEqual(text_content([{"text": "line1"}, {"text": "line2"}]), "line1\nline2")
        self.assertEqual(text_content(None), "")

    def test_categorize_tool_use(self):
        self.assertEqual(
            categorize_tool_use("Read", {"file_path": "/Users/x/.agent/workflows/ciclo.md"}),
            "lectura framework",
        )
        self.assertEqual(
            categorize_tool_use("Read", {"file_path": "/Users/x/GUARDRAILS_MASTER.md"}),
            "lectura framework",
        )
        self.assertEqual(
            categorize_tool_use("Read", {"file_path": "/Users/x/CLAUDE.md"}),
            "lectura framework",
        )
        self.assertEqual(
            categorize_tool_use("Read", {"file_path": "/Users/x/_project/03_project_state.json"}),
            "lectura estado/_project",
        )
        self.assertEqual(
            categorize_tool_use("Read", {"file_path": "/Users/x/_feedback/field_report.md"}),
            "lectura estado/_project",
        )
        self.assertEqual(
            categorize_tool_use("Read", {"file_path": "/Users/x/src/app.py"}),
            "lectura producto",
        )
        self.assertEqual(categorize_tool_use("Bash", {"command": "ls"}), "bash")
        self.assertEqual(categorize_tool_use("mcp__state_manager__mutate_state", {}), "mcp estado")
        self.assertEqual(categorize_tool_use("Skill", {"skill": "ciclo"}), "skill harness")
        self.assertEqual(categorize_tool_use("Grep", {}), "búsqueda")
        self.assertEqual(categorize_tool_use("Edit", {}), "edición")
        self.assertEqual(categorize_tool_use("Agent", {}), "subagente")
        self.assertEqual(categorize_tool_use(None, {}), "otras")

    def test_classify_bash_command(self):
        cls, det = classify_bash_command('cd "/foo/bar" && python3 .agent/scripts/framework_doctor.py')
        self.assertEqual(cls, "ejecuta script framework")
        self.assertEqual(det, "framework_doctor.py")

        cls, det = classify_bash_command("cat .agent/workflows/cierre-modulo.md")
        self.assertEqual(cls, "inspección → framework")
        self.assertEqual(det, "cierre-modulo.md")

        cls, det = classify_bash_command("cat _project/03_project_state.json")
        self.assertEqual(cls, "inspección → estado")
        self.assertEqual(det, "03_project_state.json")

        cls, det = classify_bash_command("cat src/models/trade.py")
        self.assertEqual(cls, "inspección → producto")
        self.assertEqual(det, "cat")

        cls, det = classify_bash_command("pytest tests/")
        self.assertEqual(cls, "tests producto")

        cls, det = classify_bash_command("git status --short")
        self.assertEqual(cls, "git")

    def test_parse_session_file_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_file = Path(tmpdir) / "test_session.jsonl"
            lines = [
                {
                    "type": "assistant",
                    "message": {
                        "usage": {
                            "input_tokens": 100,
                            "cache_read_input_tokens": 50,
                            "cache_creation_input_tokens": 20,
                            "output_tokens": 10,
                        },
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Read",
                                "input": {"file_path": "/proj/_project/03_project_state.json"},
                            },
                            {
                                "type": "tool_use",
                                "id": "t2",
                                "name": "Skill",
                                "input": {"skill": "ciclo"},
                            },
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t1",
                                "content": '{"project": "test"}',
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {"content": "/ciclo"},
                },
            ]
            with session_file.open("w", encoding="utf-8") as f:
                for line in lines:
                    f.write(json.dumps(line) + "\n")

            parsed = parse_session_file(session_file, "TestProject")
            self.assertIsNotNone(parsed)
            assert parsed is not None
            self.assertEqual(parsed.turns, 1)
            self.assertEqual(parsed.input_tokens, 100)
            self.assertEqual(parsed.cache_read_tokens, 50)
            self.assertEqual(parsed.cache_creation_tokens, 20)
            self.assertEqual(parsed.output_tokens, 10)
            self.assertEqual(parsed.tool_chars_by_category["lectura estado/_project"], len('{"project": "test"}'))
            self.assertEqual(parsed.command_invocations["skill:ciclo"], 1)
            self.assertEqual(parsed.command_invocations["user_slash:/ciclo"], 1)

    def test_find_engine_read_episodes_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = Path(tmpdir) / "-Projects-TestApp"
            proj_dir.mkdir()
            session_file = proj_dir / "sess1.jsonl"
            lines = [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Voy a invocar la mutación"},
                            {"type": "tool_use", "id": "m1", "name": "mcp__state_manager__mutate_state", "input": {"action": "bad"}},
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "tool_result", "tool_use_id": "m1", "is_error": True, "content": "ERROR: acción inválida 'bad'"}
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Revisaré el motor para ver las acciones permitidas"},
                            {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/app/.agent/scripts/state_core.py"}},
                        ]
                    },
                },
            ]
            with session_file.open("w", encoding="utf-8") as f:
                for line in lines:
                    f.write(json.dumps(line) + "\n")

            episodes = find_engine_read_episodes(Path(tmpdir), target_projects=("TestApp",))
            self.assertEqual(len(episodes), 1)
            self.assertEqual(episodes[0]["proyecto"], "TestApp")
            self.assertEqual(episodes[0]["fichero"], "state_core.py")
            self.assertIn("ERROR: acción inválida", episodes[0]["error_previo"])
            self.assertIn("Revisaré el motor", episodes[0]["agente_dijo"])

    def test_inspect_disk_state_sizes_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = Path(tmpdir) / "group" / "my_app" / "_project"
            proj_dir.mkdir(parents=True)
            (proj_dir / "03_project_state.json").write_text('{"project": "my_app"}')
            (proj_dir / "core.md").write_text("# Core")

            sizes = inspect_disk_state_sizes(Path(tmpdir))
            self.assertEqual(len(sizes), 1)
            self.assertEqual(sizes[0]["project"], "my_app")
            self.assertIn("03_project_state.json", sizes[0]["sizes_bytes"])

    def test_format_markdown_report_structure(self):
        analysis = {
            "metadata": {
                "transcripts_root": "/tmp/transcripts",
                "projects_root": "/tmp/projects",
                "target_projects": ["App1"],
                "total_sessions_analyzed": 1,
            },
            "project_summaries": {
                "App1": {
                    "sessions_count": 1,
                    "median_turns": 10,
                    "max_turns": 10,
                    "median_context_tokens": 50000,
                    "total_input_and_cache_tokens": 500000,
                    "total_cache_creation_tokens": 10000,
                    "total_output_tokens": 2000,
                    "big_files_read": {},
                }
            },
            "cache_dimension": {
                "total_cache_creation_tokens": 10000,
                "total_cache_read_tokens": 50000,
                "total_uncached_input_tokens": 1000,
                "cache_read_ratio": 5.0,
                "cache_read_pct": 83.3,
                "cache_create_pct": 16.7,
                "effective_cost_creation": 12500.0,
                "effective_cost_read": 5000.0,
                "effective_cost_total": 18500.0,
                "effective_cost_creation_pct": 67.6,
                "effective_cost_read_pct": 27.0,
                "median_first_turn_creation": 8000,
                "median_hot_turn_creation": 1000,
                "cold_resumes_count": 1,
                "cold_resumes_creation_tokens": 2000,
                "cold_resumes_pct_of_total_creation": 20.0,
                "cold_resumes": [
                    {
                        "session": "sess1234",
                        "timestamp": "2026-09-17T15:00:00Z",
                        "gap_hours": 2.5,
                        "creation_tokens": 2000,
                    }
                ],
            },
            "tool_chars_breakdown": {"bash": 1000, "lectura producto": 500},
            "fw_files_read_chars": {},
            "bash_classes": {"inspección → estado": {"chars": 203, "calls": 2, "top_details": {}}},
            "mcp_state_stats": {"mutate_state": {"calls": 1, "total_chars": 100, "median_chars": 100, "max_chars": 100, "overflows": 0}},
            "command_invocations": {"skill:ciclo": 1},
            "workflow_origins": {"ciclo.md": {"skill_tool": 1}},
            "engine_read_episodes_count": 1,
            "engine_read_episodes_sample": [],
            "disk_states": [],
        }
        report = format_markdown_report(analysis)
        self.assertIn("# Línea Base de Consumo de Tokens", report)
        self.assertIn("Matriz de las 10 Dimensiones", report)
        self.assertIn("20,3 %", report)
        self.assertIn("no comparable entre cortes", report)
        self.assertIn("## 9. Dimensión de Caché de Prompt (H4)", report)
        self.assertIn("sess1234", report)

    def test_parse_session_file_with_since_until(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            f_early = Path(tmpdir) / "early.jsonl"
            f_mid = Path(tmpdir) / "mid.jsonl"
            f_late = Path(tmpdir) / "late.jsonl"

            def write_sess(path: Path, first_ts: str):
                lines = [
                    {"type": "queue-operation", "timestamp": first_ts},
                    {
                        "type": "assistant",
                        "timestamp": first_ts,
                        "message": {
                            "usage": {
                                "input_tokens": 10,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0,
                                "output_tokens": 5,
                            }
                        },
                    },
                ]
                with path.open("w", encoding="utf-8") as h:
                    for l in lines:
                        h.write(json.dumps(l) + "\n")

            write_sess(f_early, "2026-09-10T12:00:00Z")
            write_sess(f_mid, "2026-09-17T12:00:00Z")
            write_sess(f_late, "2026-09-20T12:00:00Z")

            # Sin filtro: los 3 parsean
            self.assertIsNotNone(parse_session_file(f_early, "P"))
            self.assertIsNotNone(parse_session_file(f_mid, "P"))
            self.assertIsNotNone(parse_session_file(f_late, "P"))

            # Filtro since
            self.assertIsNone(parse_session_file(f_early, "P", since="2026-09-15T00:00:00Z"))
            self.assertIsNotNone(parse_session_file(f_mid, "P", since="2026-09-15T00:00:00Z"))
            self.assertIsNotNone(parse_session_file(f_late, "P", since="2026-09-15T00:00:00Z"))

            # Filtro until
            self.assertIsNotNone(parse_session_file(f_early, "P", until="2026-09-18T00:00:00Z"))
            self.assertIsNotNone(parse_session_file(f_mid, "P", until="2026-09-18T00:00:00Z"))
            self.assertIsNone(parse_session_file(f_late, "P", until="2026-09-18T00:00:00Z"))

            # Rango cerrado
            self.assertIsNone(parse_session_file(f_early, "P", since="2026-09-15T00:00:00Z", until="2026-09-18T00:00:00Z"))
            self.assertIsNotNone(parse_session_file(f_mid, "P", since="2026-09-15T00:00:00Z", until="2026-09-18T00:00:00Z"))
            self.assertIsNone(parse_session_file(f_late, "P", since="2026-09-15T00:00:00Z", until="2026-09-18T00:00:00Z"))

    def test_run_full_analysis_with_project_and_date_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            p1 = root / "-Users-x-Projects-Personales-AlphaApp"
            p2 = root / "-Users-x-Projects-Personales-BetaApp"
            p1.mkdir(parents=True)
            p2.mkdir(parents=True)

            def make_file(p: Path, name: str, ts: str):
                lines = [
                    {"type": "queue-operation", "timestamp": ts},
                    {
                        "type": "assistant",
                        "timestamp": ts,
                        "message": {
                            "usage": {
                                "input_tokens": 10,
                                "cache_read_input_tokens": 5,
                                "cache_creation_input_tokens": 1,
                                "output_tokens": 2,
                            }
                        },
                    },
                ]
                f = p / name
                with f.open("w", encoding="utf-8") as h:
                    for l in lines:
                        h.write(json.dumps(l) + "\n")

            make_file(p1, "s1.jsonl", "2026-09-16T10:00:00Z")  # fuera de rango
            make_file(p1, "s2.jsonl", "2026-09-17T10:00:00Z")  # dentro de rango
            make_file(p2, "s3.jsonl", "2026-09-17T11:00:00Z")  # otro proyecto

            res = run_full_analysis(
                transcripts_root=root,
                projects_root=root / "nonexistent",
                project="AlphaApp",
                since="2026-09-17T00:00:00Z",
                until="2026-09-18T00:00:00Z",
            )
            self.assertEqual(res["metadata"]["total_sessions_analyzed"], 1)
            self.assertIn("Personales-AlphaApp", res["project_summaries"])
            self.assertNotIn("Personales-BetaApp", res["project_summaries"])

    def test_cache_dimension_synthetic_gaps_and_ratios(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj_dir = root / "-Users-x-Projects-Personales-CacheApp"
            proj_dir.mkdir(parents=True)
            sess_file = proj_dir / "cache_sess.jsonl"

            # 3 turnos:
            # Turno 1: 10:00:00Z -> primer turno, creación 10000
            # Turno 2: 10:05:00Z (gap 5 min < 10 min) -> turno caliente, creación 500
            # Turno 3: 12:00:00Z (gap 1h55m = 1.92h > 1h) -> reanudación en frío, creación 5000
            events = [
                {"type": "queue-operation", "timestamp": "2026-09-18T10:00:00Z"},
                {
                    "type": "assistant",
                    "timestamp": "2026-09-18T10:00:00Z",
                    "message": {
                        "usage": {
                            "input_tokens": 100,
                            "cache_read_input_tokens": 500,
                            "cache_creation_input_tokens": 10000,
                            "output_tokens": 50,
                        }
                    },
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-09-18T10:05:00Z",
                    "message": {
                        "usage": {
                            "input_tokens": 50,
                            "cache_read_input_tokens": 10000,
                            "cache_creation_input_tokens": 500,
                            "output_tokens": 30,
                        }
                    },
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-09-18T12:00:00Z",
                    "message": {
                        "usage": {
                            "input_tokens": 50,
                            "cache_read_input_tokens": 8000,
                            "cache_creation_input_tokens": 5000,
                            "output_tokens": 40,
                        }
                    },
                },
            ]
            with sess_file.open("w", encoding="utf-8") as h:
                for ev in events:
                    h.write(json.dumps(ev) + "\n")

            res = run_full_analysis(
                transcripts_root=root,
                projects_root=root / "nonexistent",
                project="CacheApp",
            )
            c = res["cache_dimension"]
            self.assertEqual(c["total_cache_creation_tokens"], 15500)
            self.assertEqual(c["total_cache_read_tokens"], 18500)
            self.assertEqual(c["total_uncached_input_tokens"], 200)
            self.assertEqual(c["median_first_turn_creation"], 10000)
            self.assertEqual(c["median_hot_turn_creation"], 500)
            self.assertEqual(c["cold_resumes_count"], 1)
            self.assertEqual(c["cold_resumes_creation_tokens"], 5000)
            self.assertEqual(c["cold_resumes_pct_of_total_creation"], round(5000 / 15500 * 100, 1))

            # Verificar cálculo de coste efectivo
            expected_cost_create = 15500 * 1.25
            expected_cost_read = 18500 * 0.1
            expected_cost_uncached = 200 * 1.0
            expected_cost_total = expected_cost_create + expected_cost_read + expected_cost_uncached
            self.assertEqual(c["effective_cost_creation"], expected_cost_create)
            self.assertEqual(c["effective_cost_read"], expected_cost_read)
            self.assertEqual(c["effective_cost_total"], expected_cost_total)

            report = format_markdown_report(res)
            self.assertIn("## 9. Dimensión de Caché de Prompt (H4)", report)
            self.assertIn("15,500 tokens", report)
            self.assertIn("18,500 tokens", report)
            self.assertIn("10,000 tokens", report)
            self.assertIn("500 tokens", report)
            self.assertIn("1.9 h", report)


if __name__ == "__main__":
    unittest.main()
