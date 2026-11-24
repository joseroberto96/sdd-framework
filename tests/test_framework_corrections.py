#!/usr/bin/env python3
"""Regresiones para las correcciones de integridad y contexto de 4.35.6."""

from __future__ import annotations

import builtins
import copy
import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import context_sources
import context_measurement
import framework_doctor
import projections
import state_core
import state_manager
import state_queries
import validate_closure
import workflow_receipts

if __package__:
    from .test_state_queries import fixture_state, section
else:
    from test_state_queries import fixture_state, section


def complete_implementation_sources() -> dict:
    sections = {
        "scope": {"path": "_project/04_active_scope.md", "selector": "module_scope",
                  "content": "## M1\nScope\n", "module_id": "M1"},
        "acceptance": {"path": "_project/08_module_acceptance.md", "selector": "module_acceptance",
                       "content": "## M1\nAC\n", "module_id": "M1"},
        "preserve": section(".agent/workflows/cambio-seguro.md", "PRESERVAR\n"),
        "dependencies": section(".agent/rules/architecture_constraints.md", "DEPENDENCIES\n"),
        "invariants": section("_project/03_invariants.md", "INVARIANTS\n"),
        "contracts": section("_project/03_contracts.md", "CONTRACTS\n"),
        "security": section(".agent/workflows/cambio-seguro.md", "SECURITY\n"),
        "ui_states": section(".agent/workflows/enriquecer-ticket.md", "UI_STATES\n"),
        "nfr": section("_project/10_arch_constraints.md", "NFR\n"),
        "destructive": section("AGENTS.md", "GUARD\n"),
        "approval": section(".agent/workflows/tdd.md", "APPROVAL\n"),
        "specs": section(".agent/skills/spec_contracts.md", "SPECS\n"),
        "preserve_evidence": {
            "path": "_project/11_file_dependencies.md", "selector": "preserve_evidence",
            "content": (
                "### M1 — Dependencias\n"
                "preserva:\n"
                "- Flujo existente — evidencia: src/handler.py y tests/test_handler.py\n"
            ),
            "module_id": "M1",
        },
        "ui_evidence": {
            "path": "_project/08_module_acceptance.md", "selector": "ui_evidence",
            "content": (
                "### UI_STATES\n"
                "- Ideal — datos cargados y flujo feliz — evidencia: AC-1\n"
                "- Vacío — guía para crear el primer registro — evidencia: AC-2\n"
                "- Cargando — indicador visible durante la operación — evidencia: .agent/scripts/state_manager.py\n"
                "- Error — mensaje accionable y reintento — evidencia: .agent/scripts/context_sources.py\n"
                "- Parcial — advertencia de datos degradados — evidencia: AC-3\n"
            ),
            "module_id": "M1",
        },
        "nfr_evidence": {
            "path": "_project/10_arch_constraints.md", "selector": "nfr_evidence",
            "content": (
                "### M1 — NFR\n"
                "- Objetivo: respuesta de consulta\n"
                "- Métrica: latencia p95\n"
                "- Umbral: < 500ms\n"
                "- Verificación: tests/test_performance.py\n"
            ),
            "module_id": "M1",
        },
    }
    return {
        "hashes": {value["path"]: "fixture-v1" for value in sections.values()},
        "sections": sections,
        "applicability": {
            "UI_STATES": {"status": "n/a", "reason": "módulo sin UI"},
            "NFR": {"status": "n/a", "reason": "sin NFR adicionales"},
        },
    }


def real_context_root() -> tuple[tempfile.TemporaryDirectory, Path]:
    """Copy the real source catalog into an isolated workspace fixture."""
    holder = tempfile.TemporaryDirectory()
    root = Path(holder.name)
    (root / "_project").mkdir(parents=True)
    for relative in (
        ".agent/workflows/cambio-seguro.md",
        ".agent/workflows/tdd.md",
        ".agent/workflows/enriquecer-ticket.md",
        ".agent/workflows/verificar-ejecutable.md",
        ".agent/workflows/preflight-review.md",
        ".agent/workflows/cierre-modulo.md",
        ".agent/workflows/reconciliar.md",
        ".agent/workflows/publicar-github.md",
        ".agent/rules/architecture_constraints.md",
        ".agent/skills/spec_contracts.md",
        ".agent/docs/specs/progressive_context_obligations.json",
        "AGENTS.md",
    ):
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (root / ".agent" / "workflows" / "manifest.json").write_text(
        json.dumps({"commands": [{"name": "ciclo", "builder_facing": True}]}),
        encoding="utf-8",
    )
    return holder, root


def module_scope_and_acceptance(root: Path, module: str = "M1") -> None:
    (root / "_project" / "04_active_scope.md").write_text(
        f"# Scope\n\n## Módulo actual: {module}\nScope de {module}\n",
        encoding="utf-8",
    )
    (root / "_project" / "08_module_acceptance.md").write_text(
        f"## {module}\n- [ ] AC-1\n",
        encoding="utf-8",
    )


def runtime_context_root(state: dict) -> tuple[tempfile.TemporaryDirectory, Path]:
    """Create an isolated copy that exercises the real CLI/MCP loader path."""
    holder = tempfile.TemporaryDirectory()
    root = Path(holder.name)
    shutil.copytree(
        ROOT / ".agent", root / ".agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(ROOT / "_project", root / "_project")
    shutil.copy2(ROOT / "AGENTS.md", root / "AGENTS.md")
    (root / "_project" / "03_project_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return holder, root


def runtime_module_docs(
    root: Path, module: str = "M1", *, ui_states: str = "n/a", nfr: str = "n/a"
) -> None:
    (root / "_project" / "04_active_scope.md").write_text(
        f"# Scope\n\n## Módulo actual: {module}\nScope de {module}\n",
        encoding="utf-8",
    )
    ui_reason = "módulo sin superficie visual" if ui_states == "n/a" else "flujo de UI cubierto"
    nfr_reason = "sin NFR adicionales" if nfr == "n/a" else "umbral NFR cubierto en tests"
    (root / "_project" / "08_module_acceptance.md").write_text(
        f"## {module}\n"
        "- [x] AC-1 — evidencia: tests/test_framework_corrections.py\n"
        "- [x] AC-2 — evidencia: tests/test_state_queries.py\n"
        "- [x] AC-3 — evidencia: tests/test_projections_render.py\n"
        f"UI_STATES: {ui_states} — {ui_reason}\n"
        f"NFR: {nfr} — {nfr_reason}\n"
        "✅ Verificado con cliente\n",
        encoding="utf-8",
    )
    if ui_states == "required":
        with (root / "_project" / "08_module_acceptance.md").open("a", encoding="utf-8") as stream:
            stream.write(
                "### UI_STATES\n"
                "- Ideal — datos cargados y flujo feliz — evidencia: AC-1\n"
                "- Vacío — guía para crear el primer registro — evidencia: AC-2\n"
                "- Cargando — indicador visible durante la operación — evidencia: .agent/scripts/state_manager.py\n"
                "- Error — mensaje accionable y reintento — evidencia: .agent/scripts/context_sources.py\n"
                "- Parcial — advertencia de datos degradados — evidencia: AC-3\n"
            )
    dependencies = root / "_project" / "11_file_dependencies.md"
    with dependencies.open("a", encoding="utf-8") as stream:
        stream.write(
            f"\n### {module} — Contexto de prueba\n"
            "preserva:\n"
            "- Contrato existente — evidencia: .agent/scripts/state_manager.py\n"
        )
    if nfr == "required":
        constraints = root / "_project" / "10_arch_constraints.md"
        with constraints.open("a", encoding="utf-8") as stream:
            stream.write(
                f"\n## NFR del módulo: {module}\n"
                "- Objetivo: respuesta de consulta\n"
                "- Métrica: latencia p95\n"
                "- Umbral: < 500ms\n"
                "- Verificación: .agent/scripts/context_measurement.py\n"
            )


def write_valid_resolution_history(root: Path, state: dict, *, count: int) -> None:
    """Escribe un histórico real de rechazos resueltos y sellados."""
    rejections = []
    for index in range(count):
        identifier = f"CRR-{index + 1:04d}"
        timestamp = f"2026-09-09T00:00:{index:02d}Z"
        resolved_at = f"2026-09-09T01:00:{index:02d}Z"
        rejections.append({
            "id": identifier,
            "module_id": "M1",
            "reason": "ac_incomplete",
            "timestamp": timestamp,
            "resolved_at": resolved_at,
            "resolution": "AC corregido en el fixture",
            "evidence": "tests/test_framework_corrections.py",
            "resolution_event": {
                "action": "resolve_closure_rejections",
                "timestamp": resolved_at,
                "id": identifier,
            },
        })
    state["closure_rejections"] = rejections
    state_path = root / "_project" / "03_project_state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths = {
        "WORKSPACE_ROOT": root,
        "STATE_FILE_PATH": state_path,
        "CORE_FILE_PATH": root / "_project" / "core.md",
        "BUGS_FILE_PATH": root / "_project" / "06_bug_fixes.md",
        "CLARIFICATIONS_FILE_PATH": root / "_project" / "07_pending_clarifications.md",
        "DECISIONS_FILE_PATH": root / "_project" / "03_arch_decisions.md",
        "SESSION_BRIEF_PATH": root / "_project" / "00_session_brief.json",
    }
    with mock.patch.multiple(state_core, **paths):
        manager = state_core.StateManager()
        for rejection in rejections:
            manager.append_event(
                "add_closure_rejection",
                {"module_id": "M1", "reason": rejection["reason"]},
                "rejection registrado",
                rejection["timestamp"],
            )
            manager.append_event(
                "resolve_closure_rejections",
                {
                    "module_id": "M1",
                    "resolution": rejection["resolution"],
                    "evidence": rejection["evidence"],
                    "resolved_ids": [rejection["id"]],
                },
                "rejection resuelto",
                rejection["resolved_at"],
            )


def run_cli_query(root: Path, view: str, module: str | None = None, cursor: str | None = None):
    command = [
        sys.executable, "-B", ".agent/scripts/state_manager.py", "query",
        "--view", view,
    ]
    if module is not None:
        command.extend(("--module-id", module))
    if cursor is not None:
        command.extend(("--cursor", cursor))
    return subprocess.run(
        command, cwd=root, capture_output=True, text=True, timeout=30, check=False,
    )


def run_mcp_query(
    root: Path, view: str, module: str | None = None, cursor: str | None = None
) -> tuple[subprocess.CompletedProcess, dict, list[dict]]:
    arguments = {"view": view}
    if module is not None:
        arguments["module_id"] = module
    if cursor is not None:
        arguments["cursor"] = cursor
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "get_work_context", "arguments": arguments}},
    ]
    process = subprocess.run(
        [sys.executable, "-B", ".agent/scripts/state_manager.py"], cwd=root,
        input="\n".join(json.dumps(request, ensure_ascii=False) for request in requests) + "\n",
        capture_output=True, text=True, timeout=30, check=False,
    )
    responses = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    call = next(response for response in responses if response.get("id") == 3)
    text = call["result"]["content"][0]["text"]
    return process, json.loads(text), responses


@contextlib.contextmanager
def temporary_manager(root: Path, state: dict):
    project = root / "_project"
    project.mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "workflows" / "manifest.json").write_text(
        json.dumps({"commands": [{"name": "ciclo", "builder_facing": True}]}),
        encoding="utf-8",
    )
    state_path = project / "03_project_state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    paths = {
        "WORKSPACE_ROOT": root,
        "STATE_FILE_PATH": state_path,
        "CORE_FILE_PATH": project / "core.md",
        "BUGS_FILE_PATH": project / "06_bug_fixes.md",
        "CLARIFICATIONS_FILE_PATH": project / "07_pending_clarifications.md",
        "DECISIONS_FILE_PATH": project / "03_arch_decisions.md",
        "SESSION_BRIEF_PATH": project / "00_session_brief.json",
    }
    with mock.patch.multiple(state_core, **paths):
        yield state_manager.StateManager()


class ContextIntegrityCorrectionTests(unittest.TestCase):
    def test_hidden_markdown_examples_cannot_become_preserve_evidence(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            hidden_inventories = {
                "html_comment": (
                    "<!--\n"
                    "### M1 — Dependencias\n"
                    "preserva:\n"
                    "- Contrato existente — evidencia: .agent/scripts/state_manager.py\n"
                    "-->\n"
                ),
                "tilde_fence": (
                    "~~~markdown\n"
                    "### M1 — Dependencias\n"
                    "preserva:\n"
                    "- Contrato existente — evidencia: .agent/scripts/state_manager.py\n"
                    "~~~\n"
                ),
                "unclosed_backtick_fence": (
                    "```markdown\n"
                    "### M1 — Dependencias\n"
                    "preserva:\n"
                    "- Contrato existente — evidencia: .agent/scripts/state_manager.py\n"
                ),
            }
            dependency_path = root / "_project" / "11_file_dependencies.md"
            for name, content in hidden_inventories.items():
                with self.subTest(name=name):
                    dependency_path.write_text(content, encoding="utf-8")
                    process = run_cli_query(root, "implementation", "M1")
                    self.assertEqual(process.returncode, 0, process.stderr)
                    result = json.loads(process.stdout)
                    self.assertEqual(result["gate_status"], "NEEDS_INPUT")
                    self.assertFalse(result["required_context_complete"])
                    self.assertTrue(any(
                        ref.get("obligation") == "PRESERVE"
                        and ref.get("reason") in {"missing_evidence", "missing_source"}
                        for ref in result["refs"]
                    ), result["refs"])
        finally:
            holder.cleanup()

    def test_visible_markdown_lines_preserve_original_positions_and_hide_examples(self):
        content = (
            "<!-- oculto\n"
            "## Falso\n"
            "-->\n"
            "~~~markdown\n"
            "## También falso\n"
            "~~~\n"
            "    ## Código indentado\n"
            "## Real\n"
        )
        visible = state_queries.visible_markdown_lines(content)
        self.assertEqual(visible[-1].number, 8)
        self.assertEqual(visible[-1].text, "## Real")
        self.assertNotIn("## Falso", [line.text for line in visible])
        self.assertNotIn("## También falso", [line.text for line in visible])
        self.assertNotIn("## Código indentado", [line.text for line in visible])

    def test_real_shared_sources_are_not_selected_by_module_name(self):
        holder, root = real_context_root()
        try:
            module_scope_and_acceptance(root)
            loaded = context_sources.load_sources(
                root, "implementation", "M1", explicit=True
            )
            for name in (
                "preserve", "dependencies", "approval", "destructive",
                "specs", "security",
            ):
                with self.subTest(name=name):
                    source = loaded["sections"][name]
                    self.assertIsInstance(source["content"], str)
                    self.assertTrue(source["content"].strip())
                    self.assertNotIn("module_id", source)
        finally:
            holder.cleanup()

    def test_unselectable_shared_source_is_missing_instead_of_full_document_pass(self):
        holder, root = real_context_root()
        try:
            module_scope_and_acceptance(root)
            (root / ".agent" / "workflows" / "tdd.md").write_text(
                "# Instrucciones genéricas\ncontenido sin selector normativo\n",
                encoding="utf-8",
            )
            loaded = context_sources.load_sources(root, "implementation", "M1")
            self.assertIsNone(loaded["sections"]["approval"]["content"])
            result = state_queries.query_context(
                fixture_state(), view="implementation", module_id="M1", sources=loaded
            )
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            self.assertTrue(any(
                ref.get("obligation") == "APPROVAL" and ref.get("reason") == "missing_source"
                for ref in result["refs"]
            ))
        finally:
            holder.cleanup()

    def test_explicit_scope_mismatch_reports_both_requested_and_declared_ids(self):
        holder, root = real_context_root()
        try:
            module_scope_and_acceptance(root, "M1")
            loaded = context_sources.load_sources(
                root, "startup", "M2", explicit=True
            )
            result = state_queries.query_context(
                fixture_state(), view="startup", module_id="M2", sources=loaded
            )
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            conflicts = [
                ref for ref in result["refs"] if ref.get("reason") == "scope_conflict"
            ]
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["requested_module_id"], "M2")
            self.assertEqual(conflicts[0]["declared_module_id"], "M1")
        finally:
            holder.cleanup()

    def test_missing_or_unselectable_explicit_scope_cannot_return_pass(self):
        holder, root = real_context_root()
        try:
            (root / "_project" / "04_active_scope.md").write_text(
                "# Scope\n\n## M3\nScope de otro módulo\n", encoding="utf-8"
            )
            (root / "_project" / "08_module_acceptance.md").write_text(
                "## M1\n- [ ] AC-1\n", encoding="utf-8"
            )
            loaded = context_sources.load_sources(
                root, "startup", "M1", explicit=True
            )
            result = state_queries.query_context(
                fixture_state(), view="startup", module_id="M1", sources=loaded
            )
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            self.assertTrue(any(
                ref.get("reason") in {"scope_conflict", "missing_source", "wrong_module"}
                for ref in result["refs"]
            ))
        finally:
            holder.cleanup()

    def test_duplicate_normative_markers_are_ambiguous(self):
        holder, root = real_context_root()
        try:
            module_scope_and_acceptance(root)
            source = root / ".agent" / "workflows" / "cambio-seguro.md"
            source.write_text(
                "<!-- CONTEXT_SOURCE: preserve_inventory -->\n"
                "preserve one\n"
                "<!-- END_CONTEXT_SOURCE: preserve_inventory -->\n"
                "<!-- CONTEXT_SOURCE: preserve_inventory -->\n"
                "preserve two\n"
                "<!-- END_CONTEXT_SOURCE: preserve_inventory -->\n",
                encoding="utf-8",
            )
            with self.assertRaises(state_queries.QueryError) as error:
                context_sources.load_sources(root, "implementation", "M1")
            self.assertEqual(error.exception.code, "SOURCE_AMBIGUOUS")
        finally:
            holder.cleanup()

    def test_two_visible_headings_are_ambiguous(self):
        holder, root = real_context_root()
        try:
            module_scope_and_acceptance(root)
            (root / ".agent" / "rules" / "architecture_constraints.md").write_text(
                "## Dependencias\nprimera\n\n## Dependencias\nsegunda\n",
                encoding="utf-8",
            )
            with self.assertRaises(state_queries.QueryError) as error:
                context_sources.load_sources(root, "implementation", "M1")
            self.assertEqual(error.exception.code, "SOURCE_AMBIGUOUS")
        finally:
            holder.cleanup()

    def test_commented_duplicate_heading_does_not_shadow_visible_heading(self):
        holder, root = real_context_root()
        try:
            module_scope_and_acceptance(root)
            (root / ".agent" / "rules" / "architecture_constraints.md").write_text(
                "<!--\n## Dependencias\nfalsa\n-->\n\n## Dependencias\nreal\n",
                encoding="utf-8",
            )
            loaded = context_sources.load_sources(root, "implementation", "M1")
            content = loaded["sections"]["dependencies"]["content"]
            self.assertIn("real", content)
            self.assertNotIn("falsa", content)
        finally:
            holder.cleanup()

    def test_unclosed_comment_and_mismatched_fence_hide_until_eof(self):
        content = (
            "<!-- comentario sin cierre\n"
            "## Falso por comentario\n"
        )
        visible = state_queries.visible_markdown_lines(content)
        self.assertNotIn("## Falso por comentario", [line.text for line in visible])

        content = (
            "~~~markdown\n"
            "## Falso por fence\n"
            "```\n"
            "## Sigue oculto\n"
        )
        visible = state_queries.visible_markdown_lines(content)
        self.assertNotIn("## Falso por fence", [line.text for line in visible])
        self.assertNotIn("## Sigue oculto", [line.text for line in visible])

        content = "~~~markdown\n## Oculto\n~~~~\n## Visible\n"
        visible = state_queries.visible_markdown_lines(content)
        self.assertIn("## Visible", [line.text for line in visible])

    def test_applicability_inside_comment_or_fence_is_not_authoritative(self):
        content = (
            "<!-- UI_STATES: required — falso -->\n"
            "~~~markdown\nNFR: required — falso\n~~~\n"
        )
        applicability = state_queries.parse_applicability(content)
        self.assertEqual(applicability["UI_STATES"]["status"], "missing")
        self.assertEqual(applicability["NFR"]["status"], "missing")

    def test_closure_loads_cumulative_sources_once(self):
        holder, root = real_context_root()
        try:
            module_scope_and_acceptance(root)
            with mock.patch.object(
                context_sources, "_read_stable", wraps=context_sources._read_stable
            ) as read_stable:
                loaded = context_sources.load_sources(root, "closure", "M1")
            expected = {
                "scope", "acceptance", "preserve", "dependencies", "invariants",
                "contracts", "approval", "destructive", "specs", "security",
                "execution", "closure",
            }
            self.assertTrue(expected.issubset(loaded["sections"]))
            self.assertEqual(
                len(loaded["hashes"]),
                len(set(loaded["hashes"])),
                "cada archivo fuente debe leerse y aparecer una sola vez en hashes",
            )
            for name in expected:
                self.assertEqual(
                    loaded["sections"][name]["path"],
                    loaded["sections"][name]["path"],
                )
            read_paths = [call.args[0] for call in read_stable.call_args_list]
            self.assertEqual(
                len(read_paths), len(set(read_paths)),
                "un archivo compartido no debe leerse dos veces en la misma consulta",
            )
        finally:
            holder.cleanup()

    def test_real_implementation_query_derives_context_plan_once_and_keeps_it_private(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            with temporary_manager(root, state) as manager:
                with mock.patch.object(
                    state_queries, "context_plan", wraps=state_queries.context_plan
                ) as planner:
                    result = state_manager.get_work_context(
                        manager, view="implementation", module_id="M1"
                    )
            self.assertEqual(planner.call_count, 1)
            self.assertNotIn("plan", result)
            persisted = json.loads(
                (root / "_project" / "03_project_state.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("plan", persisted)
        finally:
            holder.cleanup()

    def test_query_rejects_incomplete_canonical_state(self):
        with self.assertRaises(state_queries.QueryError) as error:
            state_queries.query_context(
                {"modules": {}, "module_statuses": {}}, view="startup"
            )
        self.assertEqual(error.exception.code, "INVALID_STATE")

    def test_query_rejects_retired_codex_pointer(self):
        state = fixture_state()
        state["session"]["codex_execution"] = {"packet": "legacy"}
        with self.assertRaises(state_queries.QueryError) as error:
            state_queries.query_context(state, view="startup")
        self.assertEqual(error.exception.code, "LEGACY_MIGRATION_REQUIRED")

    def test_non_string_target_returns_structured_invalid_target(self):
        with self.assertRaises(state_queries.QueryError) as error:
            state_queries.query_context(fixture_state(), view="startup", module_id=123)
        self.assertEqual(error.exception.code, "INVALID_TARGET")

    def test_missing_tier_is_not_a_passing_implementation_context(self):
        state = fixture_state()
        state["work_tiers"].pop("M1")
        result = state_queries.query_context(
            state,
            view="implementation",
            module_id="M1",
            sources={
                "hashes": {"scope.md": "v1"},
                "sections": {"scope": section("scope.md", "## M1\nScope\n", module_id="M1")},
            },
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertFalse(result["required_context_complete"])
        self.assertIn("TIER", {ref.get("obligation") for ref in result["refs"]})

    def test_missing_implementation_obligations_are_explicit_refs(self):
        result = state_queries.query_context(
            fixture_state(),
            view="implementation",
            module_id="M1",
            sources={
                "hashes": {"scope.md": "v1"},
                "sections": {"scope": section("scope.md", "## M1\nScope\n", module_id="M1")},
            },
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        obligations = {ref.get("obligation") for ref in result["refs"]}
        self.assertTrue({"APPROVAL", "PRESERVE", "DESTRUCTIVE", "DEPENDENCIES", "SPECS", "SECURITY"}.issubset(obligations))
        self.assertFalse(result["required_context_complete"])

    def test_obligation_matrix_cannot_be_disabled_by_an_empty_source_payload(self):
        with self.assertRaises(state_queries.QueryError) as error:
            state_queries.query_context(
                fixture_state(),
                view="implementation",
                module_id="M1",
                sources={"hashes": {}, "sections": {}, "obligations": []},
            )
        self.assertEqual(error.exception.code, "SOURCE_MISSING")

    def test_required_sections_cover_every_active_mandatory_source(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "sin superficie",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sections = {
            "scope": section("_project/04_active_scope.md", "scope", module_id="M1"),
            "acceptance": section("_project/08_module_acceptance.md", "acceptance", module_id="M1"),
            "preserve": section(".agent/workflows/cambio-seguro.md", "preserve"),
            "dependencies": section(".agent/rules/architecture_constraints.md", "dependencies"),
            "invariants": section("_project/03_invariants.md", "invariants"),
            "contracts": section("_project/03_contracts.md", "contracts"),
            "approval": section(".agent/workflows/tdd.md", "approval"),
            "destructive": section("AGENTS.md", "destructive"),
            "specs": section(".agent/skills/spec_contracts.md", "specs"),
            "security": section(".agent/workflows/cambio-seguro.md", "security"),
        }
        result = state_queries.query_context(
            state,
            view="implementation",
            module_id="M1",
            sources={
                "hashes": {value["path"]: "v1" for value in sections.values()},
                "sections": sections,
                "applicability": {
                    "UI_STATES": {"status": "n/a", "reason": "no hay UI"},
                    "NFR": {"status": "n/a", "reason": "sin NFR adicionales"},
                },
            },
        )
        self.assertTrue({
            "scope", "acceptance", "preserve", "dependencies", "invariants",
            "approval", "destructive", "specs", "security",
        }.issubset(set(result["required_sections"])))
        self.assertNotIn("contracts", result["required_sections"])

    def test_required_sections_include_contracts_when_api_contract_required(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "required", "justification": "requiere api",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sections = {
            "scope": section("_project/04_active_scope.md", "scope", module_id="M1"),
            "acceptance": section("_project/08_module_acceptance.md", "acceptance", module_id="M1"),
            "preserve": section(".agent/workflows/cambio-seguro.md", "preserve"),
            "dependencies": section(".agent/rules/architecture_constraints.md", "dependencies"),
            "invariants": section("_project/03_invariants.md", "invariants"),
            "contracts": section("_project/03_contracts.md", "contracts"),
            "approval": section(".agent/workflows/tdd.md", "approval"),
            "destructive": section("AGENTS.md", "destructive"),
            "specs": section(".agent/skills/spec_contracts.md", "specs"),
            "security": section(".agent/workflows/cambio-seguro.md", "security"),
            "api_contract_spec": section("_project/02_technical_plan/api_contract.yaml", "api_contract_spec"),
        }
        result = state_queries.query_context(
            state,
            view="implementation",
            module_id="M1",
            sources={
                "hashes": {value["path"]: "v1" for value in sections.values()},
                "sections": sections,
                "applicability": {
                    "UI_STATES": {"status": "n/a", "reason": "no hay UI"},
                    "NFR": {"status": "n/a", "reason": "sin NFR adicionales"},
                },
            },
        )
        self.assertIn("contracts", result["required_sections"])

    def test_all_fragments_of_a_required_section_are_counted_until_delivered(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "sin superficie",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sections = {
            name: section(path, name, module_id="M1" if name in {"scope", "acceptance"} else None)
            for name, path in {
                "scope": "_project/04_active_scope.md",
                "acceptance": "_project/08_module_acceptance.md",
                "preserve": ".agent/workflows/cambio-seguro.md",
                "dependencies": ".agent/rules/architecture_constraints.md",
                "invariants": "_project/03_invariants.md",
                "contracts": "_project/03_contracts.md",
                "approval": ".agent/workflows/tdd.md",
                "destructive": "AGENTS.md",
                "specs": ".agent/skills/spec_contracts.md",
                "security": ".agent/workflows/cambio-seguro.md",
            }.items()
        }
        sections["security"]["content"] = "seguridad🙂漢字\n" * 5000
        complete_sources = complete_implementation_sources()
        sections.update({
            name: copy.deepcopy(complete_sources["sections"][name])
            for name in ("preserve_evidence", "ui_evidence", "nfr_evidence")
        })
        sources = {
            "hashes": {value["path"]: "v1" for value in sections.values()},
            "sections": sections,
            "applicability": {
                "UI_STATES": {"status": "n/a", "reason": "no hay UI"},
                "NFR": {"status": "n/a", "reason": "sin NFR adicionales"},
            },
        }
        pages = []
        cursor = None
        while True:
            page = state_queries.query_context(
                state, view="implementation", module_id="M1", cursor=cursor, sources=sources
            )
            pages.append(page)
            if page["complete"]:
                break
            cursor = page["next_cursor"]
        security_items = [
            item for page in pages for item in page["data"]["items"]
            if item["id"].startswith("security:")
        ]
        self.assertGreater(len(security_items), 1)
        self.assertTrue(all("path" in item and "selector" in item and "obligation_ids" in item
                            for item in security_items))
        self.assertTrue(pages[-1]["required_context_complete"])

    def test_missing_ui_and_nfr_applicability_is_not_inferred_as_not_applicable(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "sin superficie",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["sections"].pop("ui_states", None)
        sources["sections"].pop("nfr", None)
        sources.pop("applicability", None)
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertTrue({"UI_STATES", "NFR"}.issubset(
            {ref.get("obligation") for ref in result["refs"]}
        ))

    def test_explicit_na_applicability_needs_justification_and_then_is_satisfied(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "sin superficie",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["applicability"] = {
            "UI_STATES": {"status": "n/a", "reason": "módulo sin interfaz"},
            "NFR": {"status": "n/a", "reason": "no agrega requisitos no funcionales"},
        }
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources
        )
        self.assertNotIn("UI_STATES", {ref.get("obligation") for ref in result["refs"]})
        self.assertNotIn("NFR", {ref.get("obligation") for ref in result["refs"]})

    def test_transport_source_error_cannot_leave_required_context_complete(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "fixture", "classified_at": "2026-09-09T00:00:00Z",
            "classified_by": "fixture",
        }
        result = state_queries.query_context(
            state, view="implementation", module_id="M1",
            sources=complete_implementation_sources(),
            validation={"source_errors": ["filesystem validator failed"]},
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertFalse(result["required_context_complete"])
        self.assertTrue(any(ref.get("reason") == "validation_error" for ref in result["refs"]))

    def test_multiple_validator_reasons_are_preserved_in_pending_reference(self):
        state = fixture_state()
        state["work_tiers"]["M1"]["tier"] = "tier_2"
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        problems = [
            "APPROVE_RECEIPT stale: cambió scope/ACs",
            "APPROVE_RECEIPT sin evidencia",
        ]
        result = state_queries.query_context(
            state, view="implementation", module_id="M1",
            sources=complete_implementation_sources(),
            validation={"problems": {"APPROVAL": problems}},
        )
        approval_refs = [ref for ref in result["refs"] if ref.get("obligation") == "APPROVAL"]
        self.assertEqual(len(approval_refs), 1)
        self.assertEqual(approval_refs[0].get("validation_reasons"), problems)

    def test_malformed_obligation_matrix_cannot_disable_canonical_controls(self):
        baseline = [dict(item) for item in state_queries.DEFAULT_OBLIGATIONS]
        variants = []
        variants.append(baseline + [dict(baseline[0])])
        invalid_activation = [dict(item) for item in baseline]
        invalid_activation[0]["activate_before"] = "never"
        variants.append(invalid_activation)
        invalid_delivery = [dict(item) for item in baseline]
        invalid_delivery[0]["delivery"] = "optional"
        variants.append(invalid_delivery)
        altered_baseline = [dict(item) for item in baseline]
        altered_baseline[1]["delivery"] = "summary"
        variants.append(altered_baseline)
        for obligations in variants:
            with self.subTest(obligations=obligations):
                with self.assertRaises(state_queries.QueryError) as error:
                    state_queries.query_context(
                        fixture_state(), view="startup",
                        sources={"hashes": {}, "sections": {}, "obligations": obligations},
                    )
                self.assertEqual(error.exception.code, "SOURCE_MISSING")

    def test_matrix_rejects_unknown_selector_or_unimplemented_obligation(self):
        baseline = [dict(item) for item in state_queries.DEFAULT_OBLIGATIONS]
        unknown_selector = [dict(item) for item in baseline]
        unknown_selector[0]["selector"] = "not_registered"
        unimplemented = baseline + [{
            "id": "UNWIRED", "source": "AGENTS.md", "selector": "canonical_state",
            "activate_before": "startup", "delivery": "summary",
        }]
        for obligations in (unknown_selector, unimplemented):
            with self.subTest(obligations=obligations):
                with self.assertRaises(state_queries.QueryError) as error:
                    state_queries.query_context(
                        fixture_state(), view="startup",
                        sources={"hashes": {}, "sections": {}, "obligations": obligations},
                    )
                self.assertEqual(error.exception.code, "SOURCE_MISSING")

    def test_duplicate_document_fragment_merges_obligation_ids_once(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["sections"]["preserve_alias"] = {
            **copy.deepcopy(sources["sections"]["preserve"]),
            "obligation_ids": ["SECURITY"],
        }
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources
        )
        preserved = [
            item for item in result["data"]["items"]
            if item.get("path") == ".agent/workflows/cambio-seguro.md"
            and item.get("content") == "PRESERVAR\n"
        ]
        self.assertEqual(len(preserved), 1)
        self.assertEqual(set(preserved[0]["obligation_ids"]), {"PRESERVE", "SECURITY"})

    def test_module_source_cannot_be_reclassified_as_shared(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["sections"]["scope"]["source_kind"] = "shared"
        with self.assertRaises(state_queries.QueryError) as error:
            state_queries.query_context(
                state, view="implementation", module_id="M1", sources=sources
            )
        self.assertEqual(error.exception.code, "SOURCE_MISSING")

    def test_cursor_is_invalidated_when_applicability_changes(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["sections"]["security"]["content"] = "seguridad\n" * 5000
        first = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources
        )
        self.assertFalse(first["complete"])
        changed = copy.deepcopy(sources)
        changed["applicability"]["UI_STATES"] = {
            "status": "required", "reason": "la superficie ahora aplica"
        }
        with self.assertRaises(state_queries.QueryError) as error:
            state_queries.query_context(
                state, view="implementation", module_id="M1",
                cursor=first["next_cursor"], sources=changed,
            )
        self.assertEqual(error.exception.code, "STALE_CURSOR")

    def test_empty_or_wrong_module_source_does_not_satisfy_required_obligation(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "sin superficie",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["sections"]["scope"]["content"] = "   \n"
        sources["sections"]["scope"]["module_id"] = "M2"
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertTrue(any(ref.get("reason") in {"empty_source", "wrong_module"}
                            for ref in result["refs"]))

    def test_missing_preserve_evidence_is_not_a_passing_context(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["sections"].pop("preserve_evidence")
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertTrue(any(
            ref.get("obligation") == "PRESERVE" and ref.get("reason") == "missing_evidence"
            for ref in result["refs"]
        ), result["refs"])

    def test_preserve_evidence_reference_must_exist_in_real_workspace(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            (root / "_project" / "11_file_dependencies.md").write_text(
                "### M1 — Dependencias\n"
                "preserva:\n"
                "- Contrato existente — evidencia: .agent/scripts/no-existe.py\n",
                encoding="utf-8",
            )
            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            preserve_refs = [
                ref for ref in result["refs"] if ref.get("obligation") == "PRESERVE"
            ]
            self.assertTrue(any(
                ref.get("reason") == "invalid_evidence"
                and any("no existe" in detail for detail in ref.get("validation_reasons", []))
                for ref in preserve_refs
            ), preserve_refs)
        finally:
            holder.cleanup()

    def test_preserve_na_requires_explicit_non_modification_statement(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Verificaba que un `n/a` de PRESERVAR declarara explícitamente la no modificación. La garantía se retiró tras seis rondas de falsos
        positivos y cuatro revisiones independientes, todas FAIL: el contenido y
        el separador viajan por el mismo canal y ese canal tiene infinitas
        formas. Lo que sigue verificado —contenido real, no plantilla, una
        referencia por entrada y que TODA referencia citada se resuelva— vive en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_every_preserve_entry_requires_its_own_evidence(self):
        """ALCANCE RETIRADO (4.36.0-agi): la comprobación por entrada se retira del gate mecánico.

        La verificación de que cada entrada individual cite su propia evidencia pasa a
        la revisión humana de /cierre-modulo. Ver AgiDesignRegressionTests en
        DocumentaryEvidenceScopeTests.
        """
        self.skipTest("alcance retirado en 4.36.0: la comprobación por entrada pasa a /cierre-modulo (ver AgiDesignRegressionTests)")
    def test_evidence_from_another_module_is_not_reused(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["sections"]["preserve_evidence"]["module_id"] = "M2"
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources,
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertTrue(any(
            ref.get("obligation") == "PRESERVE" and ref.get("reason") == "wrong_module"
            for ref in result["refs"]
        ), result["refs"])

    def test_ui_required_without_all_five_states_is_not_a_passing_context(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Este test verificaba que estuvieran los cinco estados UI canónicos. Esa garantía se retiró tras seis rondas de
        falsos positivos y cuatro revisiones independientes, todas FAIL: el
        contenido y el separador viajan por el mismo canal y ese canal tiene
        infinitas formas. Lo que queda verificado —contenido real, no plantilla,
        y que TODA referencia citada se resuelva— se cubre en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_ui_individual_na_requires_and_accepts_its_own_reason(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["applicability"]["UI_STATES"] = {"status": "required", "reason": "fixture"}
        content = sources["sections"]["ui_evidence"]["content"]
        content = content.replace(
            "- Parcial — advertencia de datos degradados — evidencia: AC-3",
            "- Parcial — n/a — este flujo no expone datos degradados",
        )
        sources["sections"]["ui_evidence"]["content"] = content
        valid = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources,
        )
        self.assertNotIn("UI_STATES", {ref.get("obligation") for ref in valid["refs"]})
        sources["sections"]["ui_evidence"]["content"] = content.replace(
            "este flujo no expone datos degradados", "[COMPLETAR]"
        )
        invalid = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources,
        )
        self.assertTrue(any(ref.get("obligation") == "UI_STATES" for ref in invalid["refs"]))

    def test_ui_na_cannot_use_an_evidence_label_as_its_reason(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Este test verificaba que la razón de un `n/a` de UI no fuera una etiqueta de evidencia. Esa garantía se retiró tras seis rondas de
        falsos positivos y cuatro revisiones independientes, todas FAIL: el
        contenido y el separador viajan por el mismo canal y ese canal tiene
        infinitas formas. Lo que queda verificado —contenido real, no plantilla,
        y que TODA referencia citada se resuelva— se cubre en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_each_ui_state_requires_behavior_after_removing_state_name(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Este test verificaba que el comportamiento de un estado UI no fuera su propio nombre. Esa garantía se retiró tras seis rondas de
        falsos positivos y cuatro revisiones independientes, todas FAIL: el
        contenido y el separador viajan por el mismo canal y ese canal tiene
        infinitas formas. Lo que queda verificado —contenido real, no plantilla,
        y que TODA referencia citada se resuelva— se cubre en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_ui_behavior_whitespace_and_placeholder_are_not_content(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Este test verificaba que el comportamiento de un estado UI tuviera contenido. Esa garantía se retiró tras seis rondas de
        falsos positivos y cuatro revisiones independientes, todas FAIL: el
        contenido y el separador viajan por el mismo canal y ese canal tiene
        infinitas formas. Lo que queda verificado —contenido real, no plantilla,
        y que TODA referencia citada se resuelva— se cubre en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_ui_states_inside_visible_subsection_but_code_are_not_evidence(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["applicability"]["UI_STATES"] = {"status": "required", "reason": "fixture"}
        sources["sections"]["ui_evidence"]["content"] = (
            "### UI_STATES\n"
            "~~~markdown\n"
            "- Ideal — datos cargados — evidencia: AC-1\n"
            "- Vacío — guía de inicio — evidencia: AC-2\n"
            "- Cargando — indicador visible — evidencia: AC-3\n"
            "- Error — reintento accionable — evidencia: AC-4\n"
            "- Parcial — advertencia visible — evidencia: AC-5\n"
            "~~~\n"
        )
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources,
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertTrue(any(
            ref.get("obligation") == "UI_STATES" and ref.get("reason") == "invalid_evidence"
            for ref in result["refs"]
        ), result["refs"])

    def test_required_nfr_without_measurement_is_not_a_passing_context(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["applicability"]["NFR"] = {
            "status": "required", "reason": "fixture con NFR",
        }
        sources["sections"]["nfr_evidence"]["content"] = (
            "### M1 — NFR\n- Objetivo: respuesta\n- Métrica: latencia\n"
        )
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertTrue(any(
            ref.get("obligation") == "NFR" and ref.get("reason") == "invalid_evidence"
            for ref in result["refs"]
        ), result["refs"])

    def test_nfr_fields_cannot_complete_another_entry(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Verificaba que los campos de una entrada NFR no completaran otra. La garantía se retiró tras seis rondas de falsos
        positivos y cuatro revisiones independientes, todas FAIL: el contenido y
        el separador viajan por el mismo canal y ese canal tiene infinitas
        formas. Lo que sigue verificado —contenido real, no plantilla, una
        referencia por entrada y que TODA referencia citada se resuelva— vive en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_nfr_complete_entry_and_incomplete_entry_reject_the_document(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Verificaba que una entrada NFR incompleta invalidara el documento. La garantía se retiró tras seis rondas de falsos
        positivos y cuatro revisiones independientes, todas FAIL: el contenido y
        el separador viajan por el mismo canal y ese canal tiene infinitas
        formas. Lo que sigue verificado —contenido real, no plantilla, una
        referencia por entrada y que TODA referencia citada se resuelva— vive en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_nfr_duplicate_aliases_are_rejected_per_entry(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Verificaba que un alias de campo NFR duplicado se rechazara por entrada. La garantía se retiró tras seis rondas de falsos
        positivos y cuatro revisiones independientes, todas FAIL: el contenido y
        el separador viajan por el mismo canal y ese canal tiene infinitas
        formas. Lo que sigue verificado —contenido real, no plantilla, una
        referencia por entrada y que TODA referencia citada se resuelva— vive en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_nfr_top_level_fields_are_ambiguous_when_subsections_exist(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Verificaba que un campo NFR suelto quedara fuera de toda entrada. La garantía se retiró tras seis rondas de falsos
        positivos y cuatro revisiones independientes, todas FAIL: el contenido y
        el separador viajan por el mismo canal y ese canal tiene infinitas
        formas. Lo que sigue verificado —contenido real, no plantilla, una
        referencia por entrada y que TODA referencia citada se resuelva— vive en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_nfr_fields_inside_code_are_not_reused_by_a_visible_entry(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["applicability"]["NFR"] = {"status": "required", "reason": "fixture"}
        sources["sections"]["nfr_evidence"]["content"] = (
            "### M1 — NFR\n"
            "### Latencia de consulta\n"
            "~~~markdown\n"
            "- Objetivo: obtener el contexto\n"
            "- Métrica: latencia p95\n"
            "- Umbral: < 500 ms\n"
            "- Verificación: tests/test_context_performance.py\n"
            "~~~\n"
        )
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources,
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertTrue(any(
            ref.get("obligation") == "NFR" and ref.get("reason") == "invalid_evidence"
            for ref in result["refs"]
        ), result["refs"])

    def test_nfr_visible_subsections_with_four_fields_remain_valid(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["applicability"]["NFR"] = {"status": "required", "reason": "fixture"}
        sources["sections"]["nfr_evidence"]["content"] = (
            "## NFR del módulo: M1\n"
            "### Latencia de consulta\n"
            "- Objetivo: obtener el contexto\n"
            "- Métrica: latencia p95\n"
            "- Umbral: < 500 ms\n"
            "- Verificación: tests/test_context_performance.py\n"
            "### Disponibilidad\n"
            "- Objetivo: recuperar el servicio\n"
            "- Métrica: tiempo de recuperación\n"
            "- Umbral: < 30 s\n"
            "- Verificación: tests/test_context_recovery.py\n"
        )
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources,
        )
        self.assertNotIn("NFR", {ref.get("obligation") for ref in result["refs"]})

    def test_required_formal_spec_without_real_content_is_not_a_passing_context(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "required", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=sources
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertTrue(any(
            ref.get("obligation") == "SPECS" and ref.get("reason") == "missing_evidence"
            for ref in result["refs"]
        ), result["refs"])

    def test_required_formal_spec_accepts_real_module_content(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "required", "api_contract": "n/a",
            "justification": "fixture sin HTTP", "classified_at": "2026-09-09T00:00:00Z",
            "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            data_model = root / "_project" / "02_technical_plan" / "data_model.md"
            data_model.parent.mkdir(parents=True, exist_ok=True)
            data_model.write_text(
                "# Modelo de datos\n\n## Entidad Registro\n"
                "- Propietario: M1\n- Identificador: registro_id\n",
                encoding="utf-8",
            )
            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "PASS")
            self.assertTrue(result["required_context_complete"])
            self.assertFalse(any(ref.get("obligation") == "SPECS" for ref in result["refs"]))
        finally:
            holder.cleanup()

    def test_duplicate_ui_evidence_is_invalid_instead_of_first_match_wins(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Este test verificaba que un estado UI duplicado invalidaba la sección. Esa garantía se retiró tras seis rondas de
        falsos positivos y cuatro revisiones independientes, todas FAIL: el
        contenido y el separador viajan por el mismo canal y ese canal tiene
        infinitas formas. Lo que queda verificado —contenido real, no plantilla,
        y que TODA referencia citada se resuelva— se cubre en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_generic_normative_marker_cannot_satisfy_ui_evidence(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1", ui_states="required")
            acceptance = root / "_project" / "08_module_acceptance.md"
            base = acceptance.read_text(encoding="utf-8").split("### UI_STATES", 1)[0]
            acceptance.write_text(
                base
                + "<!-- CONTEXT_SOURCE: ui_evidence -->\n"
                + "- Ideal — datos cargados — evidencia: AC-1\n"
                + "- Vacío — guía de inicio — evidencia: AC-2\n"
                + "- Cargando — indicador visible — evidencia: AC-3\n"
                + "- Error — reintento accionable — evidencia: AC-4\n"
                + "- Parcial — advertencia visible — evidencia: AC-5\n"
                + "<!-- END_CONTEXT_SOURCE: ui_evidence -->\n",
                encoding="utf-8",
            )
            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            self.assertTrue(any(
                ref.get("obligation") == "UI_STATES"
                and ref.get("reason") in {"missing_evidence", "invalid_evidence"}
                for ref in result["refs"]
            ), result["refs"])
        finally:
            holder.cleanup()

    def test_generic_marker_inside_ui_subsection_is_invalid_evidence(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a", "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1", ui_states="required")
            acceptance = root / "_project" / "08_module_acceptance.md"
            base = acceptance.read_text(encoding="utf-8").split("### UI_STATES", 1)[0]
            acceptance.write_text(
                base
                + "### UI_STATES\n"
                + "<!-- CONTEXT_SOURCE: ui_states -->\n"
                + "- Ideal — datos cargados — evidencia: AC-1\n"
                + "- Vacío — guía de inicio — evidencia: AC-2\n"
                + "- Cargando — indicador visible — evidencia: AC-3\n"
                + "- Error — reintento accionable — evidencia: AC-4\n"
                + "- Parcial — advertencia visible — evidencia: AC-5\n"
                + "<!-- END_CONTEXT_SOURCE: ui_states -->\n",
                encoding="utf-8",
            )
            process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            self.assertTrue(any(
                ref.get("obligation") == "UI_STATES"
                and ref.get("reason") == "invalid_evidence"
                and any("marcador normativo" in detail for detail in ref.get("validation_reasons", []))
                for ref in result["refs"]
            ), result["refs"])
        finally:
            holder.cleanup()

    def test_transport_revalidates_approval_against_current_scope(self):
        holder, root = real_context_root()
        try:
            state = fixture_state()
            module_scope_and_acceptance(root, "M2")
            state["_decisions"] = [{
                "id": "ADR-M2", "title": "M2 decision", "context": "fixture",
                "decision": "fixture", "module_id": "M2",
            }]
            workflow_receipts.record_approval(
                state, root, "M2", approved_by="builder", evidence="aprobación oficial fixture",
                risk_summary="riesgo controlado en fixture", adr_refs=["ADR-M2"],
                now_iso="2026-09-09T00:00:00Z",
            )
            with temporary_manager(root, state) as manager:
                before = state_manager.get_work_context(manager, view="implementation", module_id="M2")
                self.assertNotIn("APPROVAL", {ref.get("obligation") for ref in before["refs"]})
                (root / "_project" / "04_active_scope.md").write_text(
                    "# Scope\n\n## Módulo actual: M2\nScope cambiado después de aprobar\n",
                    encoding="utf-8",
                )
                after = state_manager.get_work_context(manager, view="implementation", module_id="M2")
            self.assertTrue(any(
                ref.get("obligation") == "APPROVAL" and ref.get("reason") == "stale_receipt"
                for ref in after["refs"]
            ), after["refs"])
            self.assertEqual(after["gate_status"], "NEEDS_INPUT")
        finally:
            holder.cleanup()

    def test_tier_one_does_not_require_formal_approval_receipt(self):
        holder, root = real_context_root()
        try:
            state = fixture_state()
            module_scope_and_acceptance(root, "M1")
            with temporary_manager(root, state) as manager:
                result = state_manager.get_work_context(manager, view="implementation", module_id="M1")
            self.assertNotIn("APPROVAL", {ref.get("obligation") for ref in result["refs"]})
        finally:
            holder.cleanup()

    def test_tier_one_closure_does_not_require_inactive_review_source(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "fixture sin superficie ejecutable",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        sources = complete_implementation_sources()
        sources["sections"].update({
            "execution": section(".agent/workflows/verificar-ejecutable.md", "EXECUTION\n"),
            "closure": section(".agent/workflows/cierre-modulo.md", "CLOSURE\n"),
        })
        result = state_queries.query_context(
            state, view="closure", module_id="M1", sources=sources
        )
        self.assertNotIn("review", result["required_sections"])
        self.assertNotIn("REVIEW", result["required_obligations"])
        self.assertFalse(any(ref.get("obligation") == "REVIEW" for ref in result["refs"]))

    def test_transport_revalidates_review_and_execution_diff_freshness(self):
        holder, root = real_context_root()
        try:
            state = fixture_state()
            module_scope_and_acceptance(root, "M2")
            state["module_specs"]["M2"] = {
                "data_model": "n/a", "api_contract": "n/a", "justification": "fixture sin runtime",
                "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
            }
            workflow_receipts.start_review(
                state, root, "M2", reviewer_id="reviewer", implementer_id="builder",
                mode="INDEPENDENT_SUBAGENT_NO_ISOLATION", now_iso="2026-09-09T00:00:00Z",
            )
            workflow_receipts.finish_review(
                state, root, "M2", reviewer_id="reviewer", result="pass",
                evidence="revisión independiente completa del diff y de los ACs",
                now_iso="2026-09-09T00:01:00Z",
            )
            workflow_receipts.record_execution(
                state, root, "M2", kind="ok_manual", command="printf smoke",
                evidence="smoke manual ejecutado sobre el workspace fixture", now_iso="2026-09-09T00:02:00Z",
            )
            with temporary_manager(root, state) as manager:
                fresh = state_manager.get_work_context(manager, view="closure", module_id="M2")
                self.assertNotIn("REVIEW", {ref.get("obligation") for ref in fresh["refs"]})
                self.assertNotIn("EXECUTION", {ref.get("obligation") for ref in fresh["refs"]})
                (root / "product.txt").write_text("cambio posterior al cierre de evidencia", encoding="utf-8")
                stale = state_manager.get_work_context(manager, view="closure", module_id="M2")
            obligations = {ref.get("obligation") for ref in stale["refs"]}
            self.assertIn("REVIEW", obligations)
            self.assertIn("EXECUTION", obligations)
            self.assertTrue(any(ref.get("reason") == "stale_receipt" for ref in stale["refs"]))
        finally:
            holder.cleanup()

    def test_transport_does_not_trust_resolution_marker_without_state_log_event(self):
        holder, root = real_context_root()
        try:
            state = fixture_state()
            module_scope_and_acceptance(root, "M1")
            state["module_specs"]["M1"] = {
                "data_model": "n/a", "api_contract": "n/a", "justification": "fixture sin runtime",
                "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
            }
            state["closure_rejections"] = [{
                "id": "CRR-FAKE", "module_id": "M1", "reason": "ac_incomplete",
                "timestamp": "2026-09-09T00:00:00Z", "resolved_at": "2026-09-09T01:00:00Z",
                "resolution_event": {
                    "action": "resolve_closure_rejections", "id": "CRR-FAKE",
                    "timestamp": "2026-09-09T01:00:00Z",
                },
            }]
            with temporary_manager(root, state) as manager:
                result = state_manager.get_work_context(manager, view="closure", module_id="M1")
            self.assertEqual(result["gate_status"], "BLOCK")
            self.assertEqual(result["blocking_count"], 1)
        finally:
            holder.cleanup()

    def test_complete_required_context_can_pass(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a",
            "api_contract": "n/a",
            "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z",
            "classified_by": "fixture",
        }
        result = state_queries.query_context(
            state, view="implementation", module_id="M1", sources=complete_implementation_sources()
        )
        self.assertEqual(result["gate_status"], "PASS")
        self.assertTrue(result["required_context_complete"])
        self.assertFalse(result["refs"])

    def test_resolved_closure_rejection_does_not_block_closure(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a",
            "api_contract": "n/a",
            "justification": "fixture",
            "classified_at": "2026-09-09T00:00:00Z",
            "classified_by": "fixture",
        }
        state["closure_rejections"] = [{
            "id": "CRR-0001",
            "module_id": "M1",
            "reason": "ac_incomplete",
            "timestamp": "2026-09-09T00:00:00Z",
            "resolved_at": "2026-09-09T01:00:00Z",
            "resolution_event": {
                "action": "resolve_closure_rejections",
                "id": "CRR-0001",
                "timestamp": "2026-09-09T01:00:00Z",
            },
        }]
        result = state_queries.query_context(
            state, view="closure", module_id="M1", sources=complete_implementation_sources()
        )
        self.assertNotIn("CRR-0001", {item["id"] for item in result["data"]["items"]})

    def test_explicit_startup_target_loads_scope_for_conflict_detection(self):
        holder, root = real_context_root()
        try:
            module_scope_and_acceptance(root, "M1")
            sources = context_sources.load_sources(root, "startup", "M2", explicit=True)
            self.assertIn("scope", sources["sections"])
            result = state_queries.query_context(
                fixture_state(), view="startup", module_id="M2", sources=sources
            )
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            self.assertTrue(any(ref.get("reason") == "scope_conflict" for ref in result["refs"]))
        finally:
            holder.cleanup()

    def test_recovery_requires_the_journal_source_when_it_is_missing(self):
        state = fixture_state()
        holder, root = runtime_context_root(state)
        try:
            (root / "_project" / "13_session_journal.md").unlink()
            process, recovery, _ = run_mcp_query(root, "recovery")
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(recovery["gate_status"], "NEEDS_INPUT")
            self.assertFalse(recovery["required_context_complete"])
            self.assertTrue(any(
                ref.get("path") == "_project/13_session_journal.md"
                and ref.get("reason") == "missing_source"
                for ref in recovery["refs"]
            ))
        finally:
            holder.cleanup()


class ProgressiveContextTransportIntegrationTests(unittest.TestCase):
    def _collect_cli_pages(self, root: Path, view: str, module: str | None = None):
        pages = []
        cursor = None
        while True:
            process = run_cli_query(root, view, module, cursor)
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            page = json.loads(process.stdout)
            pages.append((process, page))
            if page["complete"]:
                return pages
            cursor = page["next_cursor"]
            self.assertIsNotNone(cursor)

    def _collect_mcp_pages(self, root: Path, view: str, module: str | None = None):
        pages = []
        cursor = None
        while True:
            process, page, responses = run_mcp_query(root, view, module, cursor)
            self.assertEqual(process.returncode, 0, process.stderr.decode() if isinstance(process.stderr, bytes) else process.stderr)
            self.assertTrue(any(response.get("id") == 2 for response in responses))
            pages.append((process, page))
            if page["complete"]:
                return pages
            cursor = page["next_cursor"]
            self.assertIsNotNone(cursor)

    def test_real_cli_and_mcp_complete_startup_implementation_and_closure(self):
        state = fixture_state()
        state["work_tiers"]["M1"]["harvest"] = {
            "classes": "none", "recorded_at": "2026-09-09T00:00:00Z",
        }
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "flujo sin persistencia ni HTTP",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "manual",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1", ui_states="required", nfr="required")
            state_before = (root / "_project" / "03_project_state.json").read_bytes()
            cli_start = run_cli_query(root, "startup")
            self.assertEqual(cli_start.returncode, 0, cli_start.stderr)
            cli_start_json = json.loads(cli_start.stdout)
            mcp_start_process, mcp_start_json, mcp_responses = run_mcp_query(root, "startup")
            self.assertEqual(mcp_start_process.returncode, 0, mcp_start_process.stderr)
            self.assertEqual(cli_start_json, mcp_start_json)
            tools_list = next(response for response in mcp_responses if response.get("id") == 2)
            self.assertIn(
                "get_work_context",
                {tool["name"] for tool in tools_list["result"]["tools"]},
            )
            self.assertLessEqual(
                len(cli_start.stdout.encode("utf-8")),
                state_queries.VIEW_LIMITS["startup"] + 1,
            )

            cli_pages = self._collect_cli_pages(root, "implementation", "M1")
            self.assertTrue(cli_pages[-1][1]["complete"])
            self.assertTrue(cli_pages[-1][1]["required_context_complete"])
            self.assertEqual(cli_pages[-1][1]["gate_status"], "PASS")
            self.assertIn("UI_STATES", cli_pages[0][1]["required_obligations"])
            self.assertTrue(all(
                state_queries.serialized_context_size(page) <= state_queries.VIEW_LIMITS["implementation"]
                for _, page in cli_pages
            ))
            document_keys = [
                (item.get("path"), item.get("selector"), item.get("id"))
                for _, page in cli_pages for item in page["data"]["items"]
                if item.get("path") is not None
            ]
            self.assertEqual(len(document_keys), len(set(document_keys)))
            source_snapshot = context_sources.load_sources(
                root, "implementation", "M1", state=json.loads(state_before)
            )
            report = context_measurement.measure_pages(
                [page for _, page in cli_pages],
                wire_bytes=[len(process.stdout.encode("utf-8")) for process, _ in cli_pages],
                read_paths=sorted(source_snapshot["hashes"]),
            )
            self.assertGreaterEqual(report["pages"], 1)
            self.assertGreater(report["total_data_bytes"], 0)
            self.assertGreater(report["unique_file_count"], 1)
            self.assertEqual(report["repeated_fragment_count"], 0)
            self.assertEqual(
                (root / "_project" / "03_project_state.json").read_bytes(), state_before
            )
            self.assertEqual(report["unique_file_count"], len(report["unique_files"]))

            closure_state = json.loads(state_before)
            workflow_receipts.record_execution(
                closure_state, root, "M1", kind="ok_manual",
                command="fixture-cli-context",
                evidence="verificación oficial sobre la ruta CLI/MCP y sus páginas",
                now_iso="2026-09-09T00:02:00Z",
            )
            (root / "_project" / "03_project_state.json").write_text(
                json.dumps(closure_state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            mcp_pages = self._collect_mcp_pages(root, "closure", "M1")
            self.assertTrue(mcp_pages[-1][1]["complete"])
            self.assertTrue(mcp_pages[-1][1]["required_context_complete"])
            self.assertEqual(mcp_pages[-1][1]["gate_status"], "PASS")
            self.assertTrue(all(
                state_queries.serialized_context_size(page) <= state_queries.VIEW_LIMITS["closure"]
                for _, page in mcp_pages
            ))
            closure_snapshot = context_sources.load_sources(
                root, "closure", "M1", state=closure_state
            )
            closure_report = context_measurement.measure_pages(
                [page for _, page in mcp_pages],
                wire_bytes=[len(process.stdout.encode("utf-8")) for process, _ in mcp_pages],
                read_paths=sorted(closure_snapshot["hashes"]),
            )
            self.assertEqual(closure_report["repeated_fragment_count"], 0)
            self.assertEqual(closure_report["repeated_document_count"], 0)
        finally:
            holder.cleanup()

    def test_measurement_uses_real_reads_and_persistent_mcp_call_session(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "fixture sin persistencia ni HTTP",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            cli_report = context_measurement.measure_transport(
                root, view="implementation", module_id="M1", transport="cli",
            )
            mcp_report = context_measurement.measure_transport(
                root, view="implementation", module_id="M1", transport="mcp",
            )
            for report in (cli_report, mcp_report):
                with self.subTest(transport=report["transport"]):
                    self.assertEqual(report["read_source"], "observed_trace")
                    self.assertGreaterEqual(report["read_operations"], report["read_unique_file_count"])
                    self.assertIsInstance(report["cache_hit_count"], int)
                    self.assertGreater(report["read_bytes"], 0)
                    self.assertIn("_project/03_project_state.json", report["unique_files"])
                    self.assertIn("_project/08_module_acceptance.md", report["unique_files"])
                    self.assertGreater(report["read_bytes_by_category"]["canonical_state"], 0)
                    self.assertIn("state_core:load_state", report["reads_by_consumer"])
                    self.assertIn("context_sources.py:_read_stable", report["reads_by_consumer"])
                    self.assertEqual(report["repeated_fragment_count"], 0)
                    self.assertEqual(report["repeated_document_count"], 0)
            self.assertEqual(mcp_report["process_count"], 1)
            self.assertGreater(mcp_report["initialization_wire_bytes"], 0)
            self.assertGreater(mcp_report["discovery_wire_bytes"], 0)
            self.assertEqual(mcp_report["call_wire_bytes"], mcp_report["wire_bytes"])
            self.assertEqual(cli_report["process_count"], cli_report["pages"])
        finally:
            holder.cleanup()

    def test_resolution_log_is_audited_once_for_multiple_rejections(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "fixture sin persistencia ni HTTP",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            write_valid_resolution_history(root, state, count=3)
            with mock.patch.object(
                validate_closure,
                "_state_log_chain_is_valid",
                wraps=validate_closure._state_log_chain_is_valid,
            ) as chain_check:
                validation = state_manager._context_validation(
                    state, view="implementation", target="M1", kind="module", root=root,
                )
            self.assertEqual(chain_check.call_count, 1)
            self.assertEqual(validation["active_rejection_ids"], [])
            self.assertEqual(validation["source_errors"], [])
        finally:
            holder.cleanup()

    def test_snapshot_rejects_dependency_change_during_one_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.md"
            path.write_text("v1", encoding="utf-8")
            with self.assertRaises(context_measurement.SnapshotChanged):
                with context_measurement.capture_reads(root) as trace:
                    self.assertEqual(path.read_text(encoding="utf-8"), "v1")
                    path.write_text("v2", encoding="utf-8")
                    path.read_text(encoding="utf-8")
            self.assertEqual(len(trace.entries), 1)
            self.assertFalse(trace.entries[0]["cached"])

    def test_shared_raw_cache_counts_text_after_bytes_as_a_cache_hit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.jsonl"
            path.write_text('{"ok": true}\n', encoding="utf-8")
            with context_measurement.capture_reads(root) as trace:
                self.assertEqual(path.read_bytes(), b'{"ok": true}\n')
                self.assertEqual(path.read_text(encoding="utf-8"), '{"ok": true}\n')
            self.assertEqual(trace.entries[0]["bytes"], path.stat().st_size)
            self.assertEqual(trace.entries[1]["bytes"], 0)
            self.assertTrue(trace.entries[1]["cached"])

    def test_tier_two_closure_measurement_separates_history_and_cache(self):
        state = fixture_state()
        state["module_specs"]["M2"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "fixture sin persistencia ni HTTP",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        state["work_tiers"]["M2"]["requires_second_review"] = True
        state["work_tiers"]["M2"]["harvest"] = {
            "classes": "none", "recorded_at": "2026-09-09T00:00:00Z",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M2")
            report = context_measurement.measure_transport(
                root, view="closure", module_id="M2", transport="mcp",
            )
            self.assertEqual(report["read_source"], "observed_trace")
            self.assertGreater(report["cache_hit_count"], 0)
            self.assertEqual(report["repeated_physical_read_count"], 0)
            self.assertTrue(report["unobserved_reads"])
            self.assertIn("initialization", report["reads_by_phase"])
            self.assertIn("query", report["reads_by_phase"])
            self.assertGreater(report["initialization_wire_bytes"], 0)
            self.assertGreater(report["discovery_wire_bytes"], 0)
        finally:
            holder.cleanup()

    def test_measurement_covers_all_public_views_and_historical_growth_separately(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "fixture sin persistencia ni HTTP",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        reports: list[dict] = []
        try:
            runtime_module_docs(root, "M1")
            for transport in ("cli", "mcp"):
                for view, module in (
                    ("startup", None), ("implementation", "M1"),
                    ("closure", "M1"), ("recovery", None),
                ):
                    reports.append(context_measurement.measure_transport(
                        root, view=view, module_id=module, transport=transport,
                    ))
            self.assertEqual(
                {(report["transport"], report["view"]) for report in reports},
                {(transport, view) for transport in ("cli", "mcp")
                 for view in ("startup", "implementation", "closure", "recovery")},
            )
            self.assertTrue(all(
                report["read_source"] == "observed_trace"
                and report["read_unique_file_count"] > 0
                for report in reports
            ))
        finally:
            holder.cleanup()

        small = fixture_state()
        large = fixture_state()
        large["_bugs"] = [
            {"id": f"BUG-{index:05d}", "module_id": "OLD", "severity": "Menor",
             "status": "Resuelto", "affects_ac": False, "description": "historial ajeno"}
            for index in range(2000)
        ]
        small_holder, small_root = runtime_context_root(small)
        large_holder, large_root = runtime_context_root(large)
        try:
            small_report = context_measurement.measure_transport(
                small_root, view="startup", transport="cli",
            )
            large_report = context_measurement.measure_transport(
                large_root, view="startup", transport="cli",
            )
            self.assertEqual(small_report["total_data_bytes"], large_report["total_data_bytes"])
            self.assertGreater(
                large_report["read_bytes_by_category"]["canonical_state"],
                small_report["read_bytes_by_category"]["canonical_state"],
            )
            self.assertEqual(
                small_report["read_bytes_by_category"].get("history_log", 0),
                large_report["read_bytes_by_category"].get("history_log", 0),
            )
        finally:
            small_holder.cleanup()
            large_holder.cleanup()

    def test_measurement_reports_growth_of_valid_resolved_rejection_history(self):
        small = fixture_state()
        large = fixture_state()
        for state in (small, large):
            state["module_specs"]["M1"] = {
                "data_model": "n/a", "api_contract": "n/a",
                "justification": "fixture sin persistencia ni HTTP",
                "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
            }
        small_holder, small_root = runtime_context_root(small)
        large_holder, large_root = runtime_context_root(large)
        try:
            runtime_module_docs(small_root, "M1")
            runtime_module_docs(large_root, "M1")
            write_valid_resolution_history(small_root, small, count=1)
            write_valid_resolution_history(large_root, large, count=24)
            self.assertEqual(
                state_core.verify_log_chain(small_root / "_project" / "state_log.jsonl"),
                [],
            )
            self.assertEqual(
                state_core.verify_log_chain(large_root / "_project" / "state_log.jsonl"),
                [],
            )

            small_process = run_cli_query(small_root, "closure", "M1")
            large_process = run_cli_query(large_root, "closure", "M1")
            self.assertEqual(small_process.returncode, 0, small_process.stderr)
            self.assertEqual(large_process.returncode, 0, large_process.stderr)
            small_page = json.loads(small_process.stdout)
            large_page = json.loads(large_process.stdout)
            self.assertNotEqual(small_page["gate_status"], "BLOCK")
            self.assertNotEqual(large_page["gate_status"], "BLOCK")
            self.assertFalse(any(
                "death criterion:" in detail
                for page in (small_page, large_page)
                for ref in page.get("refs", [])
                for detail in ref.get("validation_reasons", [])
            ))

            small_report = context_measurement.measure_transport(
                small_root, view="closure", module_id="M1", transport="mcp",
            )
            large_report = context_measurement.measure_transport(
                large_root, view="closure", module_id="M1", transport="mcp",
            )
            # El resumen conserva el conteo histórico (1 vs 24), por lo que
            # puede crecer de forma constante; los elementos y referencias
            # activos no deben crecer con rechazos ya respaldados.
            self.assertEqual(small_page["data"]["items"], large_page["data"]["items"])
            self.assertEqual(small_page["refs"], large_page["refs"])
            self.assertLessEqual(
                large_report["total_data_bytes"] - small_report["total_data_bytes"],
                2,
            )
            self.assertGreater(small_report["read_bytes_by_category"]["history_log"], 0)
            self.assertGreater(
                large_report["read_bytes_by_category"]["history_log"],
                small_report["read_bytes_by_category"]["history_log"],
            )
            self.assertEqual(small_report["repeated_physical_read_count"], 0)
            self.assertEqual(large_report["repeated_physical_read_count"], 0)
            self.assertTrue(any(
                event.get("category") == "history_log"
                for event in large_report["read_events"]
            ))
        finally:
            small_holder.cleanup()
            large_holder.cleanup()

    def test_measurement_does_not_trust_caller_declared_paths(self):
        page = {"data": {"items": [{"path": "emitted.md", "selector": "s", "content": "ok"}]}}
        report = context_measurement.measure_pages(
            [page], read_paths=["invented.md"],
            read_trace={"entries": [{
                "path": "actual.md", "operation": "read_text", "content_bytes": 7,
                "bytes": 7, "cached": False, "consumer": "fixture", "phase": "query",
            }]},
        )
        self.assertEqual(report["read_source"], "observed_trace")
        self.assertEqual(report["unique_files"], ["actual.md"])
        self.assertNotIn("invented.md", report["unique_files"])
        self.assertEqual(report["read_bytes"], 7)

    def test_measurement_cli_writes_external_json_report_and_summary(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "fixture sin persistencia ni HTTP",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        holder, root = runtime_context_root(state)
        output_holder = tempfile.TemporaryDirectory()
        try:
            runtime_module_docs(root, "M1")
            report_path = Path(output_holder.name) / "context-report.json"
            process = subprocess.run(
                [sys.executable, "-B", ".agent/scripts/context_measurement.py",
                 "--root", str(root), "--view", "implementation", "--module-id", "M1",
                 "--transport", "mcp", "--output", str(report_path)],
                cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn("lecturas=", process.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["transport"], "mcp")
            self.assertEqual(report["read_source"], "observed_trace")
            self.assertIn("read_events", report)
            self.assertFalse(report_path.is_relative_to(root))
        finally:
            output_holder.cleanup()
            holder.cleanup()

    def test_real_closure_context_rejects_pending_ac_before_execution_receipt_can_pass(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "fixture sin persistencia ni HTTP",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "fixture",
        }
        state["work_tiers"]["M1"]["harvest"] = {
            "classes": "none", "recorded_at": "2026-09-09T00:00:00Z",
        }
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            acceptance = root / "_project" / "08_module_acceptance.md"
            acceptance.write_text(
                acceptance.read_text(encoding="utf-8").replace("[x] AC-1", "[ ] AC-1"),
                encoding="utf-8",
            )
            prepared = json.loads(
                (root / "_project" / "03_project_state.json").read_text(encoding="utf-8")
            )
            workflow_receipts.record_execution(
                prepared, root, "M1", kind="ok_manual", command="fixture-cli-context",
                evidence="ejecución real del fixture", now_iso="2026-09-09T00:05:00Z",
            )
            (root / "_project" / "03_project_state.json").write_text(
                json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            process = run_cli_query(root, "closure", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["gate_status"], "NEEDS_INPUT")
            self.assertFalse(result["required_context_complete"])
            self.assertTrue(any(
                ref.get("obligation") == "CLOSURE"
                and any("cobertura de ACs" in detail for detail in ref.get("validation_reasons", []))
                for ref in result["refs"]
            ), result["refs"])
        finally:
            holder.cleanup()

    def test_real_tier_two_receipt_is_required_and_official_receipts_pass(self):
        state = fixture_state()
        state["module_specs"]["M2"] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "fixture sin superficie ejecutable",
            "classified_at": "2026-09-09T00:00:00Z", "classified_by": "manual",
        }
        state["work_tiers"]["M2"]["requires_second_review"] = True
        state["_decisions"] = [{
            "id": "ADR-M2", "title": "Contexto M2", "context": "fixture",
            "decision": "usar receipts oficiales", "module_id": "M2",
        }]
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M2")
            without_receipt = run_cli_query(root, "implementation", "M2")
            self.assertEqual(without_receipt.returncode, 0)
            blocked = json.loads(without_receipt.stdout)
            self.assertEqual(blocked["gate_status"], "NEEDS_INPUT")
            self.assertTrue(any(
                ref.get("obligation") == "APPROVAL" and ref.get("reason") == "missing_receipt"
                for ref in blocked["refs"]
            ))

            approved = json.loads((root / "_project" / "03_project_state.json").read_text(encoding="utf-8"))
            workflow_receipts.record_approval(
                approved, root, "M2", approved_by="builder",
                evidence="aprobación explícita oficial del fixture",
                risk_summary="cambio transversal controlado en fixture",
                adr_refs=["ADR-M2"], now_iso="2026-09-09T00:03:00Z",
            )
            (root / "_project" / "03_project_state.json").write_text(
                json.dumps(approved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            process, page, _ = run_mcp_query(root, "implementation", "M2")
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(page["gate_status"], "PASS")
            self.assertTrue(page["required_context_complete"])
            self.assertFalse(any(ref.get("obligation") == "APPROVAL" for ref in page["refs"]))
        finally:
            holder.cleanup()

    def test_preparation_requery_can_produce_missing_requirements_without_new_view(self):
        state = fixture_state()
        holder, root = runtime_context_root(state)
        try:
            module_scope_and_acceptance(root, "M1")
            pending_process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(pending_process.returncode, 0, pending_process.stderr)
            pending = json.loads(pending_process.stdout)
            self.assertEqual(pending["gate_status"], "NEEDS_INPUT")
            pending_obligations = {ref.get("obligation") for ref in pending["refs"]}
            self.assertTrue({"SPECS", "UI_STATES", "NFR"}.issubset(pending_obligations))
            self.assertNotIn("preparation", state_queries.VIEWS)

            prepared = json.loads(
                (root / "_project" / "03_project_state.json").read_text(encoding="utf-8")
            )
            prepared["module_specs"]["M1"] = {
                "data_model": "n/a", "api_contract": "n/a",
                "justification": "preparación sin persistencia ni HTTP",
                "classified_at": "2026-09-09T00:04:00Z", "classified_by": "manual",
            }
            runtime_module_docs(root, "M1")
            (root / "_project" / "03_project_state.json").write_text(
                json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            ready_process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(ready_process.returncode, 0, ready_process.stderr)
            ready = json.loads(ready_process.stdout)
            self.assertEqual(ready["gate_status"], "PASS")
            self.assertTrue(ready["required_context_complete"])
        finally:
            holder.cleanup()

    def test_real_scope_conflict_and_global_blocker_remain_visible(self):
        state = fixture_state()
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1")
            conflict_process = run_cli_query(root, "startup", "M2")
            self.assertEqual(conflict_process.returncode, 0, conflict_process.stderr)
            conflict = json.loads(conflict_process.stdout)
            self.assertEqual(conflict["gate_status"], "NEEDS_INPUT")
            self.assertTrue(any(
                ref.get("reason") == "scope_conflict"
                and ref.get("requested_module_id") == "M2"
                and ref.get("declared_module_id") == "M1"
                for ref in conflict["refs"]
            ))

            state["_bugs"] = [{
                "id": "BUG-GLOBAL", "module_id": "OTHER", "severity": "Crítico",
                "status": "Abierto", "affects_ac": False,
                "description": "bloqueo crítico ajeno al módulo objetivo",
            }]
            (root / "_project" / "03_project_state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            blocked_process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(blocked_process.returncode, 0, blocked_process.stderr)
            blocked = json.loads(blocked_process.stdout)
            self.assertEqual(blocked["gate_status"], "BLOCK")
            self.assertTrue(any(item["id"] == "BUG-GLOBAL" for item in blocked["data"]["items"]))
            self.assertTrue(any(ref.get("obligation") == "SPECS" for ref in blocked["refs"]))
        finally:
            holder.cleanup()

    def test_real_recovery_view_loads_audit_and_journal_without_product_history(self):
        state = fixture_state()
        state["last_completed_step"] = "preparación de M1"
        holder, root = runtime_context_root(state)
        try:
            process, recovery, responses = run_mcp_query(root, "recovery")
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(recovery["gate_status"], "PASS")
            self.assertTrue(recovery["required_context_complete"])
            self.assertIn("RECOVERY", recovery["required_obligations"])
            paths = {
                item.get("path") for item in recovery["data"]["items"]
                if item.get("path")
            }
            self.assertIn(".agent/workflows/reconciliar.md", paths)
            self.assertIn("_project/13_session_journal.md", paths)
            self.assertTrue(any(response.get("id") == 1 for response in responses))
        finally:
            holder.cleanup()

    def test_real_startup_ignores_ten_thousand_unrelated_closed_events(self):
        small = fixture_state()
        large = fixture_state()
        large["_bugs"] = [
            {"id": f"BUG-{index:05d}", "module_id": "OLD", "severity": "Menor",
             "status": "Resuelto", "affects_ac": False, "description": "historial ajeno"}
            for index in range(10000)
        ]
        small_holder, small_root = runtime_context_root(small)
        large_holder, large_root = runtime_context_root(large)
        try:
            small_result = run_cli_query(small_root, "startup")
            large_result = run_cli_query(large_root, "startup")
            self.assertEqual(small_result.returncode, 0)
            self.assertEqual(large_result.returncode, 0)
            small_json = json.loads(small_result.stdout)
            large_json = json.loads(large_result.stdout)
            self.assertEqual(small_json["data"], large_json["data"])
            self.assertEqual(small_json["refs"], large_json["refs"])
            self.assertEqual(
                len(large_result.stdout.encode("utf-8")),
                len(small_result.stdout.encode("utf-8")),
            )

            large["_bugs"].append({
                "id": "BUG-GLOBAL", "module_id": "OTHER", "severity": "Crítico",
                "status": "Abierto", "affects_ac": False, "description": "bloqueo global",
            })
            (large_root / "_project" / "03_project_state.json").write_text(
                json.dumps(large, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            blocked = run_cli_query(large_root, "implementation", "M1")
            self.assertEqual(blocked.returncode, 0)
            self.assertEqual(json.loads(blocked.stdout)["gate_status"], "BLOCK")
        finally:
            small_holder.cleanup()
            large_holder.cleanup()


class AtomicAuditCorrectionTests(unittest.TestCase):
    def test_log_write_failure_rolls_back_state_and_projections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "_project"
            project.mkdir()
            (root / ".agent" / "workflows").mkdir(parents=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text(
                json.dumps({"commands": [{"name": "ciclo", "builder_facing": True}]}),
                encoding="utf-8",
            )
            paths = {
                "WORKSPACE_ROOT": root,
                "STATE_FILE_PATH": project / "03_project_state.json",
                "CORE_FILE_PATH": project / "core.md",
                "BUGS_FILE_PATH": project / "06_bug_fixes.md",
                "CLARIFICATIONS_FILE_PATH": project / "07_pending_clarifications.md",
                "DECISIONS_FILE_PATH": project / "03_arch_decisions.md",
                "SESSION_BRIEF_PATH": project / "00_session_brief.json",
            }
            paths["STATE_FILE_PATH"].write_text(
                json.dumps(fixture_state(), ensure_ascii=False), encoding="utf-8"
            )
            before = paths["STATE_FILE_PATH"].read_bytes()
            with mock.patch.multiple(state_core, **paths):
                manager = state_core.StateManager()
                log_path = project / "state_log.jsonl"
                real_open = builtins.open

                def fail_log(path, *args, **kwargs):
                    if Path(path) == log_path:
                        raise OSError("disk full")
                    return real_open(path, *args, **kwargs)

                with mock.patch("builtins.open", side_effect=fail_log):
                    with self.assertRaises(RuntimeError):
                        manager.mutate("update_last_step", {"last_completed_step": "nuevo"})
            self.assertEqual(paths["STATE_FILE_PATH"].read_bytes(), before)
            self.assertFalse(log_path.exists())


class DocumentationAndProjectionCorrectionTests(unittest.TestCase):
    def test_rendered_brief_has_a_pure_semantic_consistency_check(self):
        state = fixture_state()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "_project"
            project.mkdir()
            (root / ".agent" / "workflows").mkdir(parents=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text(
                json.dumps({"commands": [{"name": "ciclo", "builder_facing": True}]}),
                encoding="utf-8",
            )
            paths = {
                "WORKSPACE_ROOT": root,
                "STATE_FILE_PATH": project / "03_project_state.json",
                "CORE_FILE_PATH": project / "core.md",
                "BUGS_FILE_PATH": project / "06_bug_fixes.md",
                "CLARIFICATIONS_FILE_PATH": project / "07_pending_clarifications.md",
                "DECISIONS_FILE_PATH": project / "03_arch_decisions.md",
                "SESSION_BRIEF_PATH": project / "00_session_brief.json",
            }
            with mock.patch.multiple(state_core, **paths):
                projections.render(state)
                brief = json.loads(paths["SESSION_BRIEF_PATH"].read_text(encoding="utf-8"))
            self.assertEqual(projections.projection_semantic_diff(state, brief), {})

    def test_doctor_rejects_a_semantically_stale_session_brief(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "_project"
            project.mkdir()
            state = fixture_state()
            brief = {"project": "[BRIEF_OBSOLETO]"}
            (project / "03_project_state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            brief_path = project / "00_session_brief.json"
            brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
            errors = []
            with mock.patch.object(framework_doctor, "ROOT", root):
                framework_doctor.validate_template_cleanliness("template", errors)
        self.assertTrue(any("session_brief" in error for error in errors), errors)

    def test_manifest_declares_context_metadata_for_every_command(self):
        manifest = json.loads((ROOT / ".agent/workflows/manifest.json").read_text(encoding="utf-8"))
        missing = [item["name"] for item in manifest["commands"] if not isinstance(item.get("context"), dict)]
        self.assertEqual(missing, [])

    def test_executor_no_longer_teaches_retired_slice_protocol(self):
        executor = (ROOT / ".agent/subagents/executor.md").read_text(encoding="utf-8")
        for marker in ("SLICE_RESULT", "RECONCILE_ATTEMPT", "Slice Codex"):
            self.assertNotIn(marker, executor)


if __name__ == "__main__":
    unittest.main()
