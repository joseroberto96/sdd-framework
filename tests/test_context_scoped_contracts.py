#!/usr/bin/env python3
"""L5 (5.2.3, FW-297): las vistas entregan solo los contratos pertinentes al módulo.

El fixture es sintético (sin datos de ningún proyecto real): un ``03_contracts.md``
grande con entradas de varios módulos, módulos con y sin interfaz pública y
``task:`` con spec clasificada. La cifra de antes/después se comprueba con el
mismo recorrido por cursor que usa el medidor.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import context_sources  # noqa: E402
from test_framework_corrections import (  # noqa: E402
    run_cli_query,
    runtime_context_root,
    runtime_module_docs,
)
from test_state_queries import fixture_state  # noqa: E402

OTHER_ENTRIES = 60
BODY = "- `operacion(param: tipo) -> tipo`\n  - Propósito: " + ("detalle del contrato; " * 30) + "\n"


def contracts_text(extra_owned: str = "") -> str:
    parts = [
        "# Contratos e Interfaces Públicas — Fixture\n\n"
        "## Reglas de coexistencia\n\nTexto general del formato.\n\n"
        "## Contratos declarados\n\n"
    ]
    for index in range(3):
        parts.append(f"### Iface{index:03d} — M1 (Protocol)\n**Módulo:** `M1`\n**Archivo:** src/m1/iface{index}.py\n\n{BODY}\n")
    for index in range(OTHER_ENTRIES):
        parts.append(
            f"### Ajena{index:03d} — M9_otro (Protocol)\n**Módulo:** `M9_otro`\n"
            f"**Archivo:** src/m9/ajena{index}.py\n\n{BODY * 4}\n"
        )
    parts.append(f"### SharedBus — M9_otro (evento)\n**Módulo:** `M9_otro`\n**Archivo:** src/m9/bus.py\n\n{BODY}\n")
    parts.append(extra_owned)
    return "".join(parts)


def build_root(*, extra_owned: str = "", stack_blob: str | None = None):
    state = fixture_state()
    state["current_step"] = "implementation"
    state["modules"] = {
        "completed": [],
        "in_progress": ["M1", "M3", "task:t_spec", "task:t_bus"],
        "pending": [],
    }

    state["module_statuses"] = {"M1": "in_progress", "M3": "in_progress"}

    def spec(data_model: str, api_contract: str) -> dict:
        return {
            "data_model": data_model, "api_contract": api_contract,
            "justification": "fixture", "classified_at": "2026-09-09T00:00:00Z",
            "classified_by": "fixture",
        }

    state["module_specs"] = {
        "M1": spec("n/a", "done"),
        "M3": spec("done", "n/a"),
        "task:t_spec": spec("n/a", "n/a"),
        "task:t_bus": spec("n/a", "done"),
    }
    state["work_tiers"] = {
        name: {
            "tier": "tier_1", "risk": "standard", "rationale": "fixture",
            "state_reasons": ["fixture"], "classified_at": "2026-09-09T00:00:00Z",
            "classified_by": "fixture",
        }
        for name in state["modules"]["in_progress"]
    }
    if stack_blob is not None:
        state["stack"] = {**state["stack"], "notas": stack_blob}
    holder, root = runtime_context_root(state)
    (root / "_project" / "03_contracts.md").write_text(contracts_text(extra_owned), encoding="utf-8")
    return holder, root


def walk(root: Path, view: str, module: str) -> list[dict]:
    pages: list[dict] = []
    cursor = None
    while True:
        result = run_cli_query(root, view, module, cursor)
        if result.returncode != 0:
            raise AssertionError(f"query falló: {result.stderr[:400]}")
        page = json.loads(result.stdout)
        pages.append(page)
        cursor = page.get("next_cursor")
        if not cursor:
            return pages


def delivered_text(pages: list[dict]) -> str:
    return "\n".join(
        str(item.get("content")) for page in pages for item in page["data"]["items"]
        if item.get("kind") in {"section", "section_fragment"}
    )


def assert_contracts_delivered(test: unittest.TestCase, pages: list[dict]) -> None:
    """El recorrido llegó al final y contratos no quedó como fuente ausente o vacía."""
    test.assertIsNone(pages[-1]["next_cursor"])
    reasons = {
        ref.get("reason") for page in pages for ref in page["refs"] if ref.get("section") == "contracts"
    }
    test.assertFalse(reasons & {"missing_source", "empty_source", "wrong_module"}, reasons)


def write_scope(root: Path, module: str, body: str = "") -> None:
    (root / "_project" / "04_active_scope.md").write_text(
        f"# Scope\n\n## Módulo actual: {module}\nScope de {module}\n{body}", encoding="utf-8"
    )


class ScopedContractsTests(unittest.TestCase):
    def test_module_without_public_interface_does_not_page_foreign_contracts(self):
        holder, root = build_root()
        self.addCleanup(holder.cleanup)
        runtime_module_docs(root, "M3")
        pages = walk(root, "implementation", "M3")
        text = delivered_text(pages)
        self.assertNotIn("Ajena010", text)
        self.assertNotIn("Iface000", text)
        self.assertLessEqual(len(pages), 3, [len(json.dumps(page)) for page in pages])
        assert_contracts_delivered(self, pages)
        self.assertIn("Sin contratos", text)
        refs = [ref for page in pages for ref in page["refs"] if ref.get("section") == "contracts"]
        self.assertTrue(refs, "lo no entregado debe quedar como referencia exacta")
        omitted = refs[0]
        self.assertEqual(omitted["path"], "_project/03_contracts.md")
        self.assertGreaterEqual(omitted["omitted_count"], OTHER_ENTRIES)
        self.assertTrue(any("Ajena010" in heading for heading in omitted["omitted_headings"]))

    def test_module_with_public_interface_receives_only_its_section(self):
        holder, root = build_root()
        self.addCleanup(holder.cleanup)
        runtime_module_docs(root, "M1")
        pages = walk(root, "implementation", "M1")
        text = delivered_text(pages)
        for index in range(3):
            self.assertIn(f"Iface{index:03d}", text)
        self.assertNotIn("Ajena010", text)
        self.assertLessEqual(len(pages), 3)
        assert_contracts_delivered(self, pages)

    def test_closure_view_uses_the_same_selection(self):
        holder, root = build_root()
        self.addCleanup(holder.cleanup)
        runtime_module_docs(root, "M1")
        text = delivered_text(walk(root, "closure", "M1"))
        self.assertIn("Iface000", text)
        self.assertNotIn("Ajena010", text)

    def test_consumed_interface_declared_in_scope_is_delivered(self):
        holder, root = build_root()
        self.addCleanup(holder.cleanup)
        write_scope(root, "task:t_bus", "interfaces_requeridas:\n- SharedBus\n")
        pages = walk(root, "implementation", "task:t_bus")
        text = delivered_text(pages)
        self.assertIn("SharedBus", text)
        self.assertNotIn("Ajena010", text)

    def test_task_spec_is_resolved_in_summary(self):
        holder, root = build_root()
        self.addCleanup(holder.cleanup)
        write_scope(root, "task:t_bus")
        page = walk(root, "implementation", "task:t_bus")[0]
        spec = page["data"]["summary"]["module_spec"]
        self.assertIsNotNone(spec)
        self.assertEqual(spec["api_contract"], "done")
        fields = {
            item["id"] for p in walk(root, "implementation", "task:t_bus")
            for item in p["data"]["items"] if item.get("kind") == "state_field"
        }
        self.assertTrue(any(identifier.startswith("state:module_spec:") for identifier in fields))

    def test_task_with_both_specs_na_does_not_require_contracts(self):
        holder, root = build_root()
        self.addCleanup(holder.cleanup)
        write_scope(root, "task:t_spec")
        pages = walk(root, "implementation", "task:t_spec")
        self.assertNotIn("contracts", pages[0]["required_sections"])
        self.assertNotIn("Ajena010", delivered_text(pages))
        self.assertLessEqual(len(pages), 3)

    def test_oversized_owned_entry_is_fragmented_never_source_missing(self):
        huge = "### Enorme — M1 (Protocol)\n**Módulo:** `M1`\n\n" + ('línea "con comillas" y ñ \\ ' * 20000) + "\n"
        holder, root = build_root(extra_owned=huge)
        self.addCleanup(holder.cleanup)
        runtime_module_docs(root, "M1")
        pages = walk(root, "implementation", "M1")
        self.assertIn("Enorme", delivered_text(pages))
        assert_contracts_delivered(self, pages)

    def test_oversized_state_value_is_fragmented_never_source_missing(self):
        holder, root = build_root(stack_blob='x"\\ñ' * 30000)
        self.addCleanup(holder.cleanup)
        runtime_module_docs(root, "M3")
        pages = walk(root, "implementation", "M3")
        assert_contracts_delivered(self, pages)

    def test_many_omitted_entries_with_long_non_ascii_titles_keep_the_header_bounded(self):
        holder, root = build_root()
        self.addCleanup(holder.cleanup)
        many = "".join(
            f"### {'Ñandú' * 40}{index:04d} — M9_otro\n**Módulo:** `M9_otro`\n\ncuerpo\n\n" for index in range(600)
        )
        text = (root / "_project" / "03_contracts.md").read_text(encoding="utf-8")
        (root / "_project" / "03_contracts.md").write_text(text + many, encoding="utf-8")
        runtime_module_docs(root, "M3")
        pages = walk(root, "implementation", "M3")
        ref = next(ref for ref in pages[0]["refs"] if ref.get("section") == "contracts")
        self.assertEqual(ref["omitted_count"] - len(ref["omitted_headings"]), ref["omitted_truncated"])
        self.assertLess(len(json.dumps(pages[0], ensure_ascii=False).encode("utf-8")), 24 * 1024)
        self.assertNotIn("0599", delivered_text(pages))

    def test_project_without_partition_keeps_whole_document(self):
        holder, root = build_root()
        self.addCleanup(holder.cleanup)
        (root / "_project" / "03_contracts.md").write_text(
            "# Contratos e Interfaces Públicas — Pequeño\n\nSolo texto libre sin entradas por módulo.\n",
            encoding="utf-8",
        )
        runtime_module_docs(root, "M3")
        text = delivered_text(walk(root, "implementation", "M3"))
        self.assertIn("Solo texto libre", text)


class SelectContractsUnitTests(unittest.TestCase):
    def test_selection_is_deterministic_and_lossless(self):
        content = contracts_text()
        first = context_sources.select_contracts(content, "M1", "")
        second = context_sources.select_contracts(content, "M1", "")
        self.assertEqual(first, second)
        delivered, omitted = first
        # nada se pierde: cada entrada ajena está en omitidos con su título exacto
        self.assertEqual(sum(1 for heading in omitted if heading.startswith("Ajena")), OTHER_ENTRIES)
        self.assertIn("SharedBus — M9_otro (evento)", omitted)
        self.assertNotIn("Ajena000", delivered)


if __name__ == "__main__":
    unittest.main()
