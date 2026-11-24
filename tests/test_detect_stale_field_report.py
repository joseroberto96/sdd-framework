#!/usr/bin/env python3
"""Tests unitarios de detect_stale_field_report.py (FW-220).

A diferencia de validate_no_stubs.py (cableado dentro de closure_gate y
ejercitado indirectamente por gate_calibration/test_gates_de_cierre_muerden),
detect_stale_field_report.py no tiene otra vía de invocación aparte del
wiring nuevo en framework_doctor.py — necesita tests unitarios propios, tanto
del parser (formato irregular de field_report.md, ver docstring del módulo)
como de la correlación con git.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import detect_stale_field_report as detector  # noqa: E402

_CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True, env=_CLEAN_ENV,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")


class SplitTableRowTests(unittest.TestCase):
    def test_simple_row(self):
        self.assertEqual(
            detector.split_table_row("| a | b | c |"), ["a", "b", "c"]
        )

    def test_backtick_with_pipe_inside_not_split(self):
        line = "| **FW-1** | `grep -rh 'x' dir/` cita un `|` | pendiente-de-cosecha |"
        cells = detector.split_table_row(line)
        self.assertEqual(len(cells), 3)
        self.assertIn("`", cells[1])


class ParseOpenRowsTests(unittest.TestCase):
    def test_standard_open_row_detected_with_since(self):
        text = "| FW | Síntoma | Estado |\n|---|---|---|\n| **FW-100** | s | pendiente-de-cosecha (2026-01-01) |\n"
        rows = detector.parse_open_rows(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].fw_id, "100")
        self.assertEqual(rows[0].since, "2026-01-01")

    def test_closed_row_not_detected(self):
        text = "| **FW-100** | s | cosechada-2026-01-01 |\n"
        self.assertEqual(detector.parse_open_rows(text), [])

    def test_row_without_bold_id_not_detected(self):
        """Reproduce el caso real FW-208..FW-212: `| FW-208 |` sin negrita,
        tabla de 4 columnas sin columna Estado — fuera de alcance v1."""
        text = "| FW-208 | síntoma | evidencia | mejora propuesta |\n"
        self.assertEqual(detector.parse_open_rows(text), [])

    def test_last_cell_not_starting_with_literal_not_detected(self):
        """Reproduce el caso real FW-215: la última celda es un párrafo largo
        de decisión técnica que termina en '· tier_1', no un literal abierto."""
        text = "| **FW-215** | s | e | decisión larga ... · tier_1 |\n"
        self.assertEqual(detector.parse_open_rows(text), [])

    def test_narrative_section_no_rows(self):
        text = (
            "## FW-194 — un fallo de sintaxis quedó oculto (fricción cruzada)\n\n"
            "**Qué pasó.** Prosa sin fila de tabla.\n\n**Estado:** cosechada-2026-08-21\n"
        )
        self.assertEqual(detector.parse_open_rows(text), [])

    def test_row_without_date_since_none(self):
        text = "| **FW-100** | s | candidata |\n"
        rows = detector.parse_open_rows(text)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].since)

    def test_empty_text_no_rows(self):
        self.assertEqual(detector.parse_open_rows(""), [])

    def test_diferida_literal_detected(self):
        text = "| **FW-7** | s | diferida-2026-01-01 (nota) |\n"
        rows = detector.parse_open_rows(text)
        self.assertEqual(len(rows), 1)


class FindStaleCandidatesTests(unittest.TestCase):
    def test_commit_after_since_matches(self):
        rows = [detector.OpenRow("100", 1, "pendiente-de-cosecha (2026-01-01)", "2026-01-01")]
        commits = [("abc123", "2026-02-01", "fix: cerrar FW-100")]
        findings = detector.find_stale_candidates(rows, commits)
        self.assertEqual(len(findings), 1)
        self.assertIn("FW-100", findings[0])

    def test_commit_before_since_does_not_count(self):
        rows = [detector.OpenRow("100", 1, "pendiente-de-cosecha (2026-01-01)", "2026-01-01")]
        commits = [("abc123", "2025-12-01", "fix: cerrar FW-100")]
        self.assertEqual(detector.find_stale_candidates(rows, commits), [])

    def test_prefix_collision_does_not_match(self):
        """FW-1005 no debe matchear la fila FW-100 (límite de palabra)."""
        rows = [detector.OpenRow("100", 1, "pendiente-de-cosecha (2026-01-01)", "2026-01-01")]
        commits = [("abc123", "2026-02-01", "fix: algo de FW-1005")]
        self.assertEqual(detector.find_stale_candidates(rows, commits), [])

    def test_since_none_any_commit_counts(self):
        rows = [detector.OpenRow("100", 1, "candidata", None)]
        commits = [("abc123", "2020-01-01", "fix: cerrar FW-100")]
        findings = detector.find_stale_candidates(rows, commits)
        self.assertEqual(len(findings), 1)

    def test_case_insensitive_match(self):
        rows = [detector.OpenRow("100", 1, "candidata", None)]
        commits = [("abc123", "2020-01-01", "fix: cerrar fw-100")]
        findings = detector.find_stale_candidates(rows, commits)
        self.assertEqual(len(findings), 1)

    def test_project_prefix_commit_messages_ignored(self):
        """B6: detect_stale_field_report ignora menciones con prefijo de proyecto (BOTÓN FW-, B2, B7, etc.)."""
        rows = [
            detector.OpenRow("084", 255, "pendiente-de-cosecha (2026-07-05)", "2026-07-05"),
            detector.OpenRow("085", 258, "pendiente-de-cosecha (2026-07-05)", "2026-07-05"),
            detector.OpenRow("111", 575, "pendiente-de-cosecha (2026-08-01)", "2026-08-01"),
        ]
        real_commits = [
            ("7f080fa", "2026-07-23", "merge: S2 (BOTÓN FW-085) — resuelve conflicto en pre_commit.sh con FW-106"),
            ("65f645f", "2026-07-23", "S2: suite selectiva en pre-commit para commits de solo-artefactos (BOTÓN FW-085)"),
            ("f6b0f82", "2026-08-01", "fix: gate de lockfile acepta pin exacto sin lockfile en Python (B2, FW-084/FW-010pt2)"),
            ("100250b", "2026-08-01", "fix: mensajes de error e allowlist mas precisos (B7, FW-111/FW-102/GW-045 + FW-110)"),
        ]
        findings = detector.find_stale_candidates(rows, real_commits)
        self.assertEqual(
            findings,
            [],
            f"Los commits de proyectos no deben marcar candidatas de la plantilla: {findings}",
        )


class GitIntegrationTests(unittest.TestCase):
    def test_real_repo_detects_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            fr = root / "field_report.md"
            fr.write_text(
                "| **FW-1** | s | pendiente-de-cosecha (2020-01-01) |\n", encoding="utf-8"
            )
            _git(root, "add", "-A")
            _git(root, "commit", "-q", "-m", "chore: init")
            _git(root, "commit", "--allow-empty", "-q", "-m", "fix: cerrar FW-1")
            findings = detector.stale_field_report_candidates(fr, root)
            self.assertEqual(len(findings), 1)
            self.assertIn("FW-1", findings[0])

    def test_git_unavailable_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)  # sin `git init` -> no es un repo git
            fr = root / "field_report.md"
            fr.write_text(
                "| **FW-1** | s | pendiente-de-cosecha (2020-01-01) |\n", encoding="utf-8"
            )
            self.assertEqual(detector.stale_field_report_candidates(fr, root), [])

    def test_missing_field_report_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            self.assertEqual(
                detector.stale_field_report_candidates(root / "no-existe.md", root), []
            )


class MainCliTests(unittest.TestCase):
    def test_findings_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            fr = root / "field_report.md"
            fr.write_text(
                "| **FW-1** | s | pendiente-de-cosecha (2020-01-01) |\n", encoding="utf-8"
            )
            _git(root, "add", "-A")
            _git(root, "commit", "-q", "-m", "chore: init")
            _git(root, "commit", "--allow-empty", "-q", "-m", "fix: cerrar FW-1")
            original_root = detector.ROOT
            try:
                detector.ROOT = root
                rc = detector.main(["detect_stale_field_report.py", "--file", str(fr)])
            finally:
                detector.ROOT = original_root
            self.assertEqual(rc, 1)

    def test_clean_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            fr = root / "field_report.md"
            fr.write_text("| **FW-1** | s | cosechada-2020-01-01 |\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-q", "-m", "chore: init")
            rc = detector.main(["detect_stale_field_report.py", "--file", str(fr)])
            self.assertEqual(rc, 0)

    def test_missing_file_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc = detector.main(
                ["detect_stale_field_report.py", "--file", str(root / "no-existe.md")]
            )
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
