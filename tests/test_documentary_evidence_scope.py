#!/usr/bin/env python3
"""Alcance declarado de la validación de evidencia documental (4.36.0).

Sustituye a la validación estructural que se retiró tras seis rondas de falsos
positivos y cuatro revisiones independientes, todas FAIL. Aquí se fija lo que el
gate SÍ garantiza y, con el mismo peso, lo que ya NO promete — un gate que
anuncia un juicio que no puede sostener es peor que uno que declara su alcance.

Cada regla se prueba en las dos direcciones (lección E de los seis intentos):
el caso que debe bloquear y la prosa legítima que debe seguir pasando.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_queries


class EvidenceScopeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "real.py").write_text("x", encoding="utf-8")
        (self.root / "_project").mkdir()
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "- [x] AC-1 — evidencia: tests/real.py\n", encoding="utf-8",
        )
        (self.root / "_project" / "06_bug_fixes.md").write_text(
            "### BUG-12 — algo\n", encoding="utf-8",
        )
        self.addCleanup(self.tmp.cleanup)

    def check(self, identifier: str, content: str) -> list[str]:
        return state_queries._evidence_problems(identifier, content, self.root)


class ReferencesAreResolvedTests(EvidenceScopeTestCase):
    """Lo que el gate garantiza: toda referencia citada se resuelve."""

    def test_every_shape_of_unresolvable_reference_blocks(self):
        """Las formas que evadieron la comprobación durante seis rondas.

        Ninguna depende ya de una regla propia: se rechazan porque el fichero
        no existe o el identificador no está declarado, y no hay ninguna
        exención que negociar (regla C de los seis intentos).
        """
        casos = {
            "fichero inexistente": "tests/no_existe.py",
            "sin directorio": "test_fantasma.py",
            "extensión larga": "informe.markdown",
            "no ascii": "test_café.py",
            "verbo http delante": "GET /tests/no_existe.py",
            "verbo con sufijo": "GET /tests/no_existe.py?caso=1",
            "barra inicial": "/tests/no_existe.py",
            "url": "https://ci.invalido/run/1",
            "identificador inventado": "AC-999",
            "identificador en minúsculas": "ac-999",
            "identificador dentro de la ruta": "tests/AC-1.py",
            "fuera del workspace": "../secreto.py",
        }
        for nombre, referencia in casos.items():
            with self.subTest(caso=nombre):
                content = f"## Evidencia\n- Entrada — evidencia: {referencia}\n"
                self.assertTrue(self.check("PRESERVE", content), nombre)

    def test_resolvable_references_pass(self):
        for referencia in ("tests/real.py", "AC-1", "BUG-12"):
            with self.subTest(referencia=referencia):
                content = f"## Evidencia\n- Entrada — evidencia: {referencia}\n"
                self.assertEqual(self.check("PRESERVE", content), [])

    def test_a_cited_identifier_is_not_a_declared_one(self):
        """Citar no declara: el documento de evidencia vive bajo `_project/`."""
        (self.root / "_project" / "10_arch_constraints.md").write_text(
            "- Entrada — evidencia: AC-777\n", encoding="utf-8",
        )
        self.assertTrue(self.check(
            "PRESERVE", "## Evidencia\n- Entrada — evidencia: AC-777\n",
        ))


class ContentIsRealTests(EvidenceScopeTestCase):
    def test_template_content_blocks(self):
        for valor in ("[completar]", "por definir", "# pendiente", "TBD", "FIXME"):
            with self.subTest(valor=valor):
                self.assertTrue(self.check(
                    "NFR", f"## NFR\n- Latencia: {valor} — evidencia: tests/real.py\n",
                ))

    def test_spanish_prose_is_not_a_template(self):
        """Efecto inverso: la prosa castellana legítima debe pasar."""
        for valor in ("invita a completar el primer registro",
                      "muestra un ejemplo de formato válido",
                      "cubre todo el recorrido de la consulta"):
            with self.subTest(valor=valor):
                self.assertEqual(self.check(
                    "NFR", f"## NFR\n- Latencia: {valor} — evidencia: tests/real.py\n",
                ), [])

    def test_hidden_content_is_never_evidence(self):
        """Regla A: una sola vista. Lo invisible no cuenta ni completa."""
        for oculto in (
            "```markdown\n- Entrada — evidencia: tests/real.py\n```\n",
            "<!--\n- Entrada — evidencia: tests/real.py\n-->\n",
            "    - Entrada — evidencia: tests/real.py\n",
        ):
            with self.subTest(forma=oculto[:12]):
                self.assertTrue(self.check("PRESERVE", "## Evidencia\n" + oculto))


class PerEntryEvidenceTests(EvidenceScopeTestCase):
    """Alcance simplificado: una sección con referencias válidas pasa; la exhaustividad
    de cada ítem individual corresponde a la revisión humana de /cierre-modulo."""

    def test_details_and_prose_are_not_entries(self):
        self.assertEqual(self.check("PRESERVE", (
            "## PRESERVAR\n"
            "- Login conserva la sesión — evidencia: tests/real.py\n"
            "  - detalle: conserva también el token\n\n"
            "Se comprobó a mano con el arnés local.\n"
        )), [])

    def test_an_entry_declared_not_applicable_needs_no_reference(self):
        """El gate no juzga la razón; la revisión humana ve los `n/a`."""
        self.assertEqual(self.check("PRESERVE", (
            "## PRESERVAR\n"
            "- Login conserva la sesión — evidencia: tests/real.py\n"
            "- Parcial — n/a — esta vista no tiene carga parcial\n"
        )), [])

    def test_nfr_fields_are_not_entries(self):
        """En NFR los ítems son campos: exigirles referencia rompería el formato."""
        self.assertEqual(self.check("NFR", (
            "## NFR del módulo: M1\n"
            "### Latencia\n"
            "- Objetivo: responder sin bloquear la vista\n"
            "- Métrica: latencia p95 de la API\n"
            "- Umbral: < 500 ms\n"
            "- Verificación: tests/real.py\n"
        )), [])


class RetiredScopeIsDocumentedTests(EvidenceScopeTestCase):
    """Lo que el gate ya NO promete, fijado para que nadie lo dé por hecho.

    Estos documentos PASAN el gate y están incompletos. No es un defecto: es el
    alcance declarado. Si alguien reintroduce la validación estructural, estos
    tests fallarán y le obligarán a leer por qué se retiró antes de repetir las
    seis rondas.
    """

    def test_structure_of_the_section_is_not_judged(self):
        casos = {
            "faltan estados UI": (
                "UI_STATES",
                "## UI\n- Ideal — muestra los datos — evidencia: tests/real.py\n",
            ),
            "entrada NFR sin sus cuatro campos": (
                "NFR",
                "## NFR\n- Objetivo: responder sin bloquear — evidencia: tests/real.py\n",
            ),
            "dos entradas NFR a medias": (
                "NFR",
                "## NFR\n### Latencia\n- Objetivo: responder\n- Métrica: p95\n"
                "### Recuperación\n- Umbral: < 30 s\n- Verificación: tests/real.py\n",
            ),
            "ítem sin evidencia junto a ítem con evidencia": (
                "PRESERVE",
                "## PRESERVAR\n"
                "- Login conserva la sesión — evidencia: tests/real.py\n"
                "- Logout no toca el almacenamiento\n",
            ),
            "entrada en blockquote sin evidencia junto a ítem con evidencia": (
                "PRESERVE",
                "## P\n- A — evidencia: tests/real.py\n> - B sin evidencia\n",
            ),
        }
        for nombre, (identifier, content) in casos.items():
            with self.subTest(caso=nombre):
                self.assertEqual(
                    self.check(identifier, content), [],
                    f"{nombre}: el gate declara NO juzgar esto; si ahora bloquea, "
                    "lee el comentario de cabecera de la sección de evidencia en "
                    "state_queries.py antes de cambiar nada",
                )


class ReviewFindingsRegressionTests(EvidenceScopeTestCase):
    """Los seis hallazgos de la revisión por encargos pequeños (2026-09-11)."""

    def test_markdown_links_and_task_boxes_are_not_templates(self):
        """H1: un corchete marcaba plantilla siempre, y eso es Markdown normal."""
        for documento in (
            "- Login — evidencia: [test de login](tests/real.py)",
            "- [x] Login — evidencia: tests/real.py",
            "- [ ] Login pendiente — evidencia: tests/real.py",
        ):
            with self.subTest(documento=documento[:28]):
                self.assertEqual(self.check("PRESERVE", f"## P\n{documento}\n"), [])

    def test_real_templates_are_still_rejected(self):
        for documento in ("- X — evidencia: [completar]",
                          "- [Título breve] — evidencia: tests/real.py"):
            with self.subTest(documento=documento[:28]):
                self.assertTrue(self.check("PRESERVE", f"## P\n{documento}\n"))

    def test_only_tagged_text_is_verified_now(self):
        """La garantía cambió: arreglo 1 (4.36.0) retira la verificación de prosa suelta.

        `_unambiguous_reference_problems` intentaba resolver, en cualquier
        parte del texto, lo que "no podía ser otra cosa" que una referencia
        (fichero con extensión, identificador). Costó más de lo que dio: el
        mismo mecanismo que debía bloquear `Según AC-999 se cumple` bloqueaba
        también prosa legítima como `El backend corre en Node.js` o `Dominio
        ejemplo.com` (ver `ClosureRoundRegressionTests`). El contrato ahora es
        que la evidencia es *solo* lo que está dentro de un `— evidencia:`; lo
        que quede fuera de una etiqueta ya no se interpreta como referencia,
        exista o no el fichero o identificador que menciona.
        """
        for prosa in ("Se mide en inventado/no_existe.py", "Según AC-999 se cumple"):
            with self.subTest(prosa=prosa[:24]):
                self.assertEqual(self.check(
                    "NFR", f"## N\n{prosa}\n- L — evidencia: tests/real.py\n",
                ), [])

    def test_spanish_prose_shaped_like_a_path_is_not_a_reference(self):
        """El reverso: «entrada/salida» tiene forma de ruta y no lo es."""
        self.assertEqual(self.check(
            "NFR",
            "## N\nCubre entrada/salida y lectura/escritura del flujo\n"
            "- L — evidencia: tests/real.py\n",
        ), [])

    def test_numbered_and_indented_entries_count_as_entries(self):
        """H3: la regla por ítem se evitaba numerando o sangrando dos espacios."""
        for documento in ("1. Sesión de usuario\n2. Export CSV",
                          "  - Módulo A\n  - Módulo B"):
            with self.subTest(documento=documento[:20]):
                self.assertTrue(self.check(
                    "PRESERVE", f"## P\n{documento}\n\nVer tests/real.py\n",
                ))

    def test_a_child_item_is_detail_not_an_entry(self):
        """Y su contrapeso: una sub-viñeta cuelga de su padre, no es entrada."""
        self.assertEqual(self.check("PRESERVE", (
            "## P\n- Login — evidencia: tests/real.py\n"
            "  - detalle: conserva también el token\n"
        )), [])

    def test_an_unchecked_box_does_not_declare_an_identifier(self):
        """H4: un criterio pendiente declaraba el identificador que cita."""
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "- [x] AC-1 — evidencia: tests/real.py\n- [ ] AC-9 — pendiente\n",
            encoding="utf-8",
        )
        self.assertTrue(self.check("PRESERVE", "## P\n- X — evidencia: AC-9\n"))
        self.assertEqual(self.check("PRESERVE", "## P\n- X — evidencia: AC-1\n"), [])


class ClosureRoundRegressionTests(EvidenceScopeTestCase):
    """Los cinco arreglos del cierre del validador (4.36.0).

    Un método por arreglo, con el caso que debe bloquear y la prosa legítima
    que debe seguir pasando como `subTest` del mismo método (regla E de los
    seis intentos). Se agrupan así, en vez de un test por dirección, porque
    en más de un arreglo la dirección "bloquea" ya funcionaba antes del
    arreglo — solo la dirección "pasa" es nueva — y el rojo demostrado exige
    que el método completo falle contra `6ad3e1e`.
    """

    def test_arreglo_1_only_tagged_references_are_verified(self):
        with self.subTest(caso="referencia etiquetada a fichero inexistente bloquea"):
            self.assertTrue(self.check(
                "PRESERVE", "## P\n- L — evidencia: tests/no_existe.py\n",
            ))
        for prosa in ("El backend corre en Node.js", "Dominio ejemplo.com"):
            with self.subTest(caso=f"prosa legítima pasa: {prosa}"):
                self.assertEqual(self.check(
                    "PRESERVE", f"## P\n{prosa}\n- Login — evidencia: tests/real.py\n",
                ), [])

    def test_arreglo_2_template_is_a_closed_list(self):
        with self.subTest(caso="palabra de plantilla bloquea"):
            self.assertTrue(self.check(
                "PRESERVE", "## P\n- X — evidencia: [completar]\n",
            ))
        with self.subTest(caso="corchete ordinario no es plantilla"):
            self.assertEqual(self.check(
                "PRESERVE", "## P\n- Índice datos[0] — evidencia: tests/real.py\n",
            ), [])

    def test_arreglo_3_blockquote_entries_and_all_not_applicable(self):
        with self.subTest(caso="todas las entradas n/a no exigen referencia de sección"):
            self.assertEqual(self.check("PRESERVE", (
                "## P\n"
                "- X — n/a (no aplica en este módulo)\n"
                "- Y — n/a (no aplica en este módulo)\n"
            )), [])
        with self.subTest(caso="sin ninguna entrada sigue exigiendo evidencia"):
            # Guarda contra la trampa de la regla anterior: `all()` sobre una
            # lista vacía es `True`. Sin comprobar que hay contenido de exención,
            # una sección sin ninguna entrada pasaría el gate por vacuidad.
            self.assertTrue(self.check(
                "PRESERVE", "## P\nSolo prosa, sin ninguna entrada declarada.\n",
            ))
        with self.subTest(caso="sub-viñeta de detalle sigue sin ser entrada"):
            self.assertEqual(self.check("PRESERVE", (
                "## P\n"
                "- A — evidencia: tests/real.py\n"
                "  - detalle: información adicional\n"
            )), [])

    def test_arreglo_4_declaration_lives_in_its_canonical_document(self):
        (self.root / "_project" / "10_arch_constraints.md").write_text(
            "- [x] AC-999 — evidencia: tests/real.py\n", encoding="utf-8",
        )
        (self.root / "_project" / "06_bug_fixes.md").write_text(
            "#BUG-1 sin espacio\n", encoding="utf-8",
        )
        with self.subTest(caso="declaración fuera de su documento canónico no cuenta"):
            self.assertTrue(self.check("PRESERVE", "## P\n- X — evidencia: AC-999\n"))
        with self.subTest(caso="declaración en su documento canónico sí cuenta"):
            self.assertEqual(self.check("PRESERVE", "## P\n- X — evidencia: AC-1\n"), [])
        with self.subTest(caso="encabezado sin espacio tras # no declara"):
            self.assertTrue(self.check("PRESERVE", "## P\n- X — evidencia: BUG-1\n"))

    def test_arreglo_5_identifiers_inside_a_path_are_not_extracted(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "ADR-007-cache.md").write_text("x", encoding="utf-8")
        (self.root / "tests" / "AC-1.py").write_text("x", encoding="utf-8")
        with self.subTest(caso="ruta existente con identificador dentro del nombre pasa"):
            self.assertEqual(self.check(
                "PRESERVE", "## P\n- Migrado — evidencia: docs/ADR-007-cache.md\n",
            ), [])
        with self.subTest(caso="ruta existente con AC dentro del nombre pasa"):
            self.assertEqual(self.check(
                "PRESERVE", "## P\n- Cubierto — evidencia: tests/AC-1.py\n",
            ), [])
        with self.subTest(caso="identificador suelto y no declarado sigue bloqueando"):
            self.assertTrue(self.check(
                "PRESERVE", "## P\n- Suelto — evidencia: AC-999\n",
            ))


class FinalClosureRegressionTests(EvidenceScopeTestCase):
    """Ronda final del cierre del validador (4.36.0): fallback, vista visible
    de la declaración, separador de `n/a`, y la mitad de arreglo 2 sin
    cobertura por mutación."""

    def test_punto1_verification_tag_replaces_the_prose_fallback(self):
        """`Verificación:` en NFR es una etiqueta de evidencia como
        `— evidencia:`; sin fallback, la prosa sin etiquetar no se interpreta
        como referencia (ni para bloquear ni para pasar)."""
        with self.subTest(caso="NFR con Verificación válida pasa"):
            self.assertEqual(self.check(
                "NFR", "## N\n- Verificación: tests/real.py\n",
            ), [])
        with self.subTest(caso="NFR con Verificación a fichero inexistente bloquea"):
            self.assertTrue(self.check(
                "NFR", "## N\n- Verificación: tests/no_existe.py\n",
            ))
        with self.subTest(caso="NFR sin ninguna verificación bloquea"):
            self.assertTrue(self.check(
                "NFR", "## N\nSolo texto, ninguna verificación citada.\n",
            ))
        with self.subTest(caso="prosa sin etiqueta bloquea por no citar, no por inventar Node.js"):
            problems = self.check("NFR", "## N\nCorre en Node.js\n")
            self.assertTrue(problems)
            self.assertTrue(any("no cita ninguna referencia" in p for p in problems))
            self.assertFalse(any("Node.js" in p for p in problems))
        with self.subTest(caso="prosa suelta junto a Verificación válida pasa"):
            self.assertEqual(self.check(
                "NFR", "## N\nCorre en Node.js\n- Verificación: tests/real.py\n",
            ), [])
        with self.subTest(caso="PRESERVE con — evidencia: clásica sigue pasando"):
            self.assertEqual(self.check(
                "PRESERVE", "## P\n- Login — evidencia: tests/real.py\n",
            ), [])

    def test_punto2_declaration_ignores_hidden_fenced_content(self):
        """Regla A: la declaración se lee sobre `visible_markdown_lines`, no
        sobre el fichero crudo. Un ejemplo dentro de un fence no declara."""
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "- [x] AC-1 — evidencia: tests/real.py\n"
            "```md\n- [x] AC-888 — ejemplo de formato\n```\n",
            encoding="utf-8",
        )
        with self.subTest(caso="declaración dentro de un fence de ejemplo no cuenta"):
            self.assertTrue(self.check("PRESERVE", "## P\n- X — evidencia: AC-888\n"))
        with self.subTest(caso="declaración real fuera del fence sigue contando"):
            self.assertEqual(self.check("PRESERVE", "## P\n- X — evidencia: AC-1\n"), [])

    def test_punto3_not_applicable_only_after_the_entry_separator(self):
        """`n/a` exime tras el separador de entrada (guion largo, o guion
        corto al principio de la entrada o precedido de espacio); no tras los
        dos puntos de un subcampo, ni dentro de una palabra o ruta."""
        eximen = {
            "guion largo con razón": "— n/a (razón)",
            "guion corto en medio, precedido de espacio": "- Parcial — n/a",
            "marcador de lista pegado a n/a, sin nombrar antes qué es": "- n/a porque…",
            "mayúsculas": "— N/A",
        }
        for nombre, texto in eximen.items():
            with self.subTest(caso=f"exime: {nombre}"):
                self.assertEqual(self.check(
                    "PRESERVE", f"## P\n- A — evidencia: tests/real.py\n{texto}\n",
                ), [])
        no_eximen = {
            "n/a como valor de un subcampo tras dos puntos":
                "- Cache — persistencia: n/a, se preserva el TTL",
            "frase con n/a a mitad de línea":
                "- Límite — Rate limit: n/a para el MVP",
            "fracción que no es n/a": "- Turno 24/7 sin evidencia",
            "n/a dentro de una ruta": "- Ver docs/plan-n/a.md sin evidencia",
            "n/a dentro de un identificador": "- Backend n/a-storage sin evidencia",
        }
        for nombre, texto in no_eximen.items():
            with self.subTest(caso=f"no exime: {nombre}"):
                self.assertTrue(self.check("PRESERVE", f"## P\n{texto}\n"))

    def test_punto3_tagged_item_is_verified_even_if_it_also_says_not_applicable(self):
        """Endurecer la salida temprana: la exención n/a solo aplica a ítems
        SIN etiqueta. Un ítem con `— evidencia:` se verifica siempre, aunque
        su texto también contenga `n/a`."""
        self.assertTrue(self.check(
            "PRESERVE", "## P\n- Cache — evidencia: tests/no_existe.py — n/a\n",
        ))

    def test_punto4a_multiline_template_and_unclosed_bracket_have_their_own_coverage(self):
        """4a: revertir `re.MULTILINE` o la rama del corchete sin cerrar de
        `_TEMPLATE_PHRASE_RE` dejaba la suite en verde — ningún test dependía
        de ellas. Cada subTest falla si se revierte esa mitad."""
        with self.subTest(caso="plantilla en la segunda línea del documento (re.MULTILINE)"):
            self.assertTrue(self.check(
                "NFR", "## N\nAlgo real.\nEjemplo de uso completo — evidencia: tests/real.py\n",
            ))
        with self.subTest(caso="corchete de plantilla sin cerrar"):
            self.assertTrue(self.check(
                "PRESERVE", "## P\n- X — evidencia: [completar\n",
            ))


class AgiDesignRegressionTests(EvidenceScopeTestCase):
    """Regresiones del diseño simplificado y blindado (4.36.0-agi).

    Cubre exhaustivamente el defecto P0 y los tres P1 de la ronda anterior,
    asegurando que las exenciones y declaraciones se comporten de forma
    mecánicamente determinista.
    """

    def test_p0_identifier_shaped_not_applicable_blocks_when_no_evidence(self):
        """P0: '- n/a-storage debe seguir sirviendo...' no es una exención n/a."""
        self.assertTrue(self.check(
            "PRESERVE", "## P\n- n/a-storage debe seguir sirviendo los blobs antiguos sin migración\n"
        ))
        self.assertTrue(self.check(
            "PRESERVE", "## P\n- Backend n/a-storage sin evidencia\n"
        ))

    def test_p1_1_subdirectories_in_project_do_not_declare_identifiers(self):
        """P1.1: Archivos en subcarpetas de _project/ no declaran identificadores."""
        (self.root / "_project" / "sub").mkdir()
        (self.root / "_project" / "sub" / "08_module_acceptance.md").write_text(
            "- [x] AC-999 — zombie en subdirectorio\n", encoding="utf-8",
        )
        self.assertTrue(self.check("PRESERVE", "## P\n- Login — evidencia: AC-999\n"))

    def test_p1_2_numbered_list_verifications_pass(self):
        """P1.2: '1. Verificación: tests/real.py' y '1. **Verificación:**' pasan."""
        for linea in (
            "1. Verificación: tests/real.py",
            "1) Verificación: tests/real.py",
            "1. **Verificación:** tests/real.py",
            "1. **Verificación**: tests/real.py",
            "- **Verificación:** tests/real.py",
        ):
            with self.subTest(linea=linea):
                self.assertEqual(self.check("NFR", f"## N\n{linea}\n"), [])

    def test_p1_3_subbullet_after_prose_paragraph_passes(self):
        """P1.3: Sub-viñeta tras párrafo de prosa dentro de una entrada pasa."""
        content = (
            "## PRESERVAR\n"
            "- Login conserva la sesión — evidencia: tests/real.py\n"
            "  Párrafo explicativo de contexto.\n"
            "  - Sub-viñeta de detalle técnico\n"
        )
        self.assertEqual(self.check("PRESERVE", content), [])

    def test_valid_section_level_exemptions_pass(self):
        casos = (
            "- n/a — este módulo es nuevo sin código previo",
            "- n/a: este módulo es nuevo sin código previo",
            "- n/a (este módulo es nuevo sin código previo)",
            "preserva: n/a — este módulo es nuevo sin código previo",
            "- No aplica — no hay código existente",
            "- NO APLICA: no hay código existente",
            "> - n/a — exención dentro de un blockquote",
            "1. n/a — lista numerada con justificación",
        )
        for doc in casos:
            with self.subTest(doc=doc[:30]):
                self.assertEqual(self.check("PRESERVE", f"## P\n{doc}\n"), [])

    def test_invalid_section_exemptions_block(self):
        casos = (
            "- n/a",  # sin justificación
            "- Turno 24/7 sin evidencia",
            "- Ver docs/plan-n/a.md sin evidencia",
            "- Cache — persistencia: n/a, se preserva el TTL",
            "- Límite — Rate limit: n/a para el MVP",
        )
        for doc in casos:
            with self.subTest(doc=doc[:30]):
                self.assertTrue(self.check("PRESERVE", f"## P\n{doc}\n"))


if __name__ == "__main__":
    unittest.main()
