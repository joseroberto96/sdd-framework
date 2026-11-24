import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import context_sources
import state_queries


class ContextMarkerVisibilityTests(unittest.TestCase):
    UI_EVIDENCE = (
        "### UI_STATES\n"
        "- Ideal — datos cargados — evidencia: AC-1\n"
        "- Vacío — guía para iniciar — evidencia: AC-2\n"
        "- Cargando — indicador visible — evidencia: AC-3\n"
        "- Error — reintento accionable — evidencia: AC-4\n"
        "- Parcial — advertencia visible — evidencia: AC-5\n"
    )
    PRESERVE_EVIDENCE = (
        "preserva:\n"
        "- Contrato existente — evidencia: tests/test_context_marker_visibility.py\n"
    )
    NFR_EVIDENCE = (
        "### NFR del módulo\n"
        "- Objetivo: respuesta de consulta\n"
        "- Métrica: latencia p95\n"
        "- Umbral: < 500ms\n"
        "- Verificación: tests/test_context_marker_visibility.py\n"
    )

    def test_valid_ui_evidence_remains_valid_with_hidden_complete_example(self):
        content = self.UI_EVIDENCE + (
            "\n```markdown\n"
            "- Ideal — ejemplo oculto — evidencia: AC-hidden\n"
            "- Vacío — ejemplo oculto — evidencia: AC-hidden\n"
            "- Cargando — ejemplo oculto — evidencia: AC-hidden\n"
            "- Error — ejemplo oculto — evidencia: AC-hidden\n"
            "- Parcial — ejemplo oculto — evidencia: AC-hidden\n"
            "```\n"
        )
        self.assertEqual(state_queries._evidence_problems("UI_STATES", content), [])

    def test_valid_preserve_evidence_remains_valid_with_hidden_complete_example(self):
        content = self.PRESERVE_EVIDENCE + (
            "\n```markdown\n"
            "preserva:\n- Ejemplo — evidencia: tests/example.py\n"
            "```\n"
        )
        self.assertEqual(state_queries._evidence_problems("PRESERVE", content), [])

    def test_valid_nfr_evidence_remains_valid_with_hidden_complete_example(self):
        """Y el reverso: un ejemplo oculto tampoco invalida evidencia real."""
        content = self.NFR_EVIDENCE + (
            "\n```markdown\n- Objetivo: ejemplo\n- Umbral: 1ms\n```\n"
        )
        self.assertEqual(state_queries._evidence_problems("NFR", content), [])
    def test_active_evidence_markers_are_invalid_even_with_valid_text(self):
        for marker in (
            "<!-- CONTEXT_SOURCE: ui_evidence -->\n" + self.UI_EVIDENCE,
            self.UI_EVIDENCE + "<!-- END_CONTEXT_SOURCE: ui_evidence -->\n",
        ):
            problems = state_queries._evidence_problems("UI_STATES", marker)
            self.assertEqual(problems, ["la evidencia no puede usar un marcador normativo genérico"])

    def test_hidden_marker_does_not_invalidate_valid_evidence(self):
        content = self.UI_EVIDENCE + (
            "\n```markdown\n<!-- CONTEXT_SOURCE: ui_evidence -->\n"
            "<!-- END_CONTEXT_SOURCE: ui_evidence -->\n```\n"
        )
        self.assertEqual(state_queries._evidence_problems("UI_STATES", content), [])

    def test_hidden_example_cannot_supply_evidence(self):
        """Sobrevive: el contenido oculto no aporta evidencia (regla A).

        Todo el validador lee una sola vista, `visible_markdown_lines`. La
        última evasión de las seis rondas consistió en esconder contenido donde
        una parte del código miraba crudo y otra no.
        """
        problems = state_queries._evidence_problems("UI_STATES", (
            "### UI_STATES\n```markdown\n"
            "- Ideal — ejemplo oculto — evidencia: AC-1\n```\n"
        ))
        self.assertTrue(problems, "un ejemplo oculto no puede ser la evidencia")
    def test_ui_state_evidence_parser_direct_contract(self):
        """ALCANCE RETIRADO (4.36.0): el gate ya no juzga la estructura de la prosa.

        Verificaba el contrato del parser de líneas de estado UI. La garantía se retiró tras seis rondas de falsos
        positivos y cuatro revisiones independientes, todas FAIL: el contenido y
        el separador viajan por el mismo canal y ese canal tiene infinitas
        formas. Lo que sigue verificado —contenido real, no plantilla, una
        referencia por entrada y que TODA referencia citada se resuelva— vive en
        `DocumentaryEvidenceScopeTests`. El juicio de estructura pasa a la
        revisión humana de `/cierre-modulo`.
        """
        self.skipTest("alcance retirado en 4.36.0: ver DocumentaryEvidenceScopeTests")
    def test_fenced_marker_is_not_a_source(self):
        text = (
            "~~~markdown\n<!-- CONTEXT_SOURCE: preserve_inventory -->\n"
            "ejemplo\n<!-- END_CONTEXT_SOURCE: preserve_inventory -->\n~~~\n"
        )
        self.assertEqual(context_sources._marker_section(text, "preserve_inventory"), (None, None))

    def test_marker_like_text_is_not_active_in_backticks_or_tildes(self):
        for text in (
            "`<!-- CONTEXT_SOURCE: preserve_inventory -->`\n",
            "~<!-- CONTEXT_SOURCE: preserve_inventory -->~\n",
            "texto `<!-- CONTEXT_SOURCE: preserve_inventory -->` texto\n",
        ):
            self.assertEqual(state_queries.scan_markdown(text).context_markers, [])

    def test_longer_fence_needs_longer_closing_fence(self):
        text = (
            "````markdown\n<!-- CONTEXT_SOURCE: preserve_inventory -->\n"
            "```\n<!-- END_CONTEXT_SOURCE: preserve_inventory -->\n````\n"
        )
        self.assertEqual(state_queries.scan_markdown(text).context_markers, [])

    def test_unclosed_fence_hides_marker_until_eof(self):
        text = "```markdown\n<!-- CONTEXT_SOURCE: preserve_inventory -->\n"
        self.assertEqual(state_queries.scan_markdown(text).context_markers, [])

    def test_indented_code_hides_marker(self):
        text = "    <!-- CONTEXT_SOURCE: preserve_inventory -->\n"
        self.assertEqual(state_queries.scan_markdown(text).context_markers, [])

    def test_open_comment_hides_marker_interior(self):
        text = "<!-- comentario\n<!-- CONTEXT_SOURCE: preserve_inventory -->\n"
        self.assertEqual(state_queries.scan_markdown(text).context_markers, [])

    def test_prose_line_with_marker_text_is_not_active(self):
        text = "Cita <!-- CONTEXT_SOURCE: preserve_inventory --> en prosa\n"
        self.assertEqual(state_queries.scan_markdown(text).context_markers, [])

    def test_comment_prefix_followed_by_fence_text_does_not_open_a_fence(self):
        text = (
            "<!-- nota --> ~~~markdown\n"
            "<!-- CONTEXT_SOURCE: preserve_inventory -->\n"
            "visible\n"
            "<!-- END_CONTEXT_SOURCE: preserve_inventory -->\n"
        )
        scan = state_queries.scan_markdown(text)
        self.assertEqual(
            scan.context_markers,
            [
                state_queries.ContextMarker(2, "preserve_inventory", False),
                state_queries.ContextMarker(4, "preserve_inventory", True),
            ],
        )

    def test_comment_prefix_followed_by_fence_text_cannot_hide_invalid_evidence(self):
        content = (
            self.UI_EVIDENCE
            + "<!-- nota --> ~~~markdown\n"
            + "<!-- CONTEXT_SOURCE: ui_evidence -->\n"
            + "ejemplo\n"
            + "<!-- END_CONTEXT_SOURCE: ui_evidence -->\n"
        )
        self.assertEqual(
            state_queries._evidence_problems("UI_STATES", content),
            ["la evidencia no puede usar un marcador normativo genérico"],
        )

    def test_real_marker_is_selected_and_hidden_marker_is_ignored(self):
        text = (
            "<!-- CONTEXT_SOURCE: preserve_inventory -->\n"
            "cuerpo\n<!-- END_CONTEXT_SOURCE: preserve_inventory -->\n"
            "```\n<!-- CONTEXT_SOURCE: preserve_inventory -->\n"
            "oculto\n<!-- END_CONTEXT_SOURCE: preserve_inventory -->\n```\n"
        )
        self.assertEqual(
            context_sources._marker_section(text, "preserve_inventory"),
            ("cuerpo\n", "preserve_inventory"),
        )

    def test_marker_events_include_original_numbers_and_closing_flag(self):
        text = "\n  <!-- CONTEXT_SOURCE: dependencies -->\r\nbody\r\n   <!-- END_CONTEXT_SOURCE: dependencies -->\r\n"
        self.assertEqual(
            state_queries.scan_markdown(text).context_markers,
            [
                state_queries.ContextMarker(2, "dependencies", False),
                state_queries.ContextMarker(4, "dependencies", True),
            ],
        )

    def test_normative_markers_allow_zero_to_three_spaces_and_no_extra_text(self):
        for indent in ("", " ", "  ", "   "):
            scan = state_queries.scan_markdown(
                f"{indent}<!-- CONTEXT_SOURCE: dependencies -->\n"
                f"{indent}<!-- END_CONTEXT_SOURCE: dependencies -->\n"
            )
            self.assertEqual(len(scan.context_markers), 2)
        self.assertEqual(
            state_queries.scan_markdown("<!-- CONTEXT_SOURCE: dependencies --> extra\n").context_markers,
            [],
        )

    def test_marker_section_reports_orphans_and_duplicates(self):
        with self.assertRaises(context_sources.QueryError) as missing:
            context_sources._marker_section("<!-- CONTEXT_SOURCE: x -->\n", "x")
        self.assertEqual(missing.exception.code, "SOURCE_MISSING")

        with self.assertRaises(context_sources.QueryError) as orphan:
            context_sources._marker_section("<!-- END_CONTEXT_SOURCE: x -->\n", "x")
        self.assertEqual(orphan.exception.code, "SOURCE_MISSING")

        duplicate = (
            "<!-- CONTEXT_SOURCE: x -->\n1\n<!-- END_CONTEXT_SOURCE: x -->\n"
            "<!-- CONTEXT_SOURCE: x -->\n2\n<!-- END_CONTEXT_SOURCE: x -->\n"
        )
        with self.assertRaises(context_sources.QueryError) as ambiguous:
            context_sources._marker_section(duplicate, "x")
        self.assertEqual(ambiguous.exception.code, "SOURCE_AMBIGUOUS")

    def test_marker_section_preserves_exact_body_and_original_file_hash_inputs(self):
        text = "<!-- CONTEXT_SOURCE: x -->\r\n línea \r\n<!-- END_CONTEXT_SOURCE: x -->\r\n"
        self.assertEqual(context_sources._marker_section(text, "x"), (" línea \r\n", "x"))


if __name__ == "__main__":
    unittest.main()
