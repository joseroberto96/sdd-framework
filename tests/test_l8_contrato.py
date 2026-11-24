#!/usr/bin/env python3
"""Contrato de L8 (5.3.0): vistas y continuidad, el relevo no falla.

Lo escribe el orquestador en rojo antes del arreglo (regla de la Fase 3) y el
ejecutor lo pone en verde sin tocarlo. Un test por punto del lote y, en cada
uno, su contrario: lo que no debe cambiar.

Fixtures sintéticos; ningún dato de un proyecto real. Los números de los
recibos imitan la forma medida en Research App (153 identidades de test
por recibo) y en Example Lab (550 escaneos de ``03_contracts.md`` por
consulta), sin copiar contenido.

Directriz del builder: nada de lo que se arregla aquí guarda hashes nuevos ni
engorda el estado; se recorta e indexa en lectura.
"""

from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import claude_continuity_hook  # noqa: E402
import context_sources  # noqa: E402
import ensure_codex_integration  # noqa: E402
import state_queries  # noqa: E402
import validate_acceptance  # noqa: E402
from test_framework_corrections import (  # noqa: E402
    run_cli_query,
    runtime_context_root,
    runtime_module_docs,
)
from test_state_queries import fixture_state  # noqa: E402

HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
PAGE_LIMIT = 24 * 1024


def _root(state: dict | None = None, scope_module: str | None = "M1"):
    holder, root = runtime_context_root(state or fixture_state())
    if scope_module:
        runtime_module_docs(root, scope_module)
    return holder, root


def _query(root: Path, view: str, module: str | None = None, cursor: str | None = None) -> dict:
    result = run_cli_query(root, view, module, cursor)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - diagnóstico
        raise AssertionError(f"salida no JSON (rc={result.returncode}): {result.stdout[:300]} {result.stderr[:300]}") from exc


def _walk(test: unittest.TestCase, root: Path, view: str, module: str | None = None) -> list[dict]:
    pages: list[dict] = []
    cursor = None
    while True:
        page = _query(root, view, module, cursor)
        test.assertNotIn("error", page, page.get("error"))
        pages.append(page)
        cursor = page.get("next_cursor")
        if not cursor:
            return pages


def _mutate(root: Path, action: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", ".agent/scripts/state_manager.py", "mutate", "--action", action,
         "--payload", json.dumps(payload, ensure_ascii=False)],
        cwd=root, capture_output=True, text=True, timeout=60, check=False,
    )


def _write_state(root: Path, state: dict) -> None:
    (root / "_project" / "03_project_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _approval_receipt(module: str, identities: int) -> dict:
    """Recibo de aprobación con la forma de los reales; los hashes son sintéticos."""
    tests = {
        f"tests/test_{module.lower()}_{index:03d}.py::Caso::test_{index:03d}": f"{index:064x}"
        for index in range(identities)
    }
    return {
        "module": module, "kind": "approve_receipt", "status": "approved", "tier": "tier_2",
        "approved_at": "2026-10-01T00:00:00+00:00", "approved_by": "builder",
        "approval_evidence": "aprobación del builder en la sesión", "risk_summary": "fixture",
        "adr_refs": [], "basis": {}, "basis_version": 3, "fingerprint": "f" * 64,
        "test_identities": tests, "test_identities_origin": dict(tests),
    }


# ---------------------------------------------------------------------------
# Punto 1 — closure que no empaqueta (PA:FW-285)
# ---------------------------------------------------------------------------
class Punto1ClosureEmpaqueta(unittest.TestCase):
    def _root_with_receipt(self, identities: int):
        state = fixture_state()
        state["workflow_receipts"] = {"approvals": {"M1": _approval_receipt("M1", identities)}}
        holder, root = _root(state)
        self.addCleanup(holder.cleanup)
        return root

    def test_recibo_con_muchas_identidades_no_rompe_closure(self):
        root = self._root_with_receipt(153)
        pages = _walk(self, root, "closure", "M1")
        for page in pages:
            self.assertLessEqual(len(state_queries.serialize_context(page).encode("utf-8")), PAGE_LIMIT)

    def test_la_cabecera_resume_las_identidades_sin_repetir_hashes(self):
        root = self._root_with_receipt(153)
        page = _walk(self, root, "closure", "M1")[0]
        approval = page["data"]["summary"]["receipts"]["approval"]
        self.assertNotIn("test_identities", approval)
        self.assertNotIn("test_identities_origin", approval)
        self.assertEqual(approval.get("test_identities_count"), 153)
        self.assertEqual(approval.get("test_identities_origin_count"), 153)
        identity_hashes = {f"{index:064x}" for index in range(153)}
        self.assertFalse(identity_hashes & set(HEX64.findall(json.dumps(page))))

    def test_lo_guardado_no_cambia(self):
        root = self._root_with_receipt(153)
        path = root / "_project" / "03_project_state.json"
        before = path.read_bytes()
        _walk(self, root, "closure", "M1")
        self.assertEqual(before, path.read_bytes())

    def test_cabecera_desbordada_se_recorta_con_marca_y_no_falla(self):
        warnings = [f"aviso de deriva {index} " + "z" * 300 for index in range(200)]
        result = state_queries.query_context(
            fixture_state(), view="closure", module_id="M1", validation={"warnings": warnings}
        )
        self.assertTrue(result.get("row_clipped"))
        self.assertLessEqual(len(state_queries.serialize_context(result).encode("utf-8")), PAGE_LIMIT)
        self.assertTrue(result["data"]["summary"].get("clipped_fields"))

    def test_cabecera_que_no_cabe_ni_recortada_dice_que_es_la_cabecera(self):
        limits = dict(state_queries.VIEW_LIMITS)
        self.addCleanup(state_queries.VIEW_LIMITS.update, limits)
        state_queries.VIEW_LIMITS["closure"] = 300
        with self.assertRaises(state_queries.QueryError) as raised:
            state_queries.query_context(fixture_state(), view="closure", module_id="M1")
        self.assertIn("encabezado", raised.exception.message)

    def test_contrario_sin_desborde_no_hay_marca_de_recorte(self):
        result = state_queries.query_context(fixture_state(), view="closure", module_id="M1")
        self.assertNotIn("row_clipped", result)
        self.assertNotIn("clipped_fields", result["data"]["summary"])
        root = self._root_with_receipt(3)
        page = _walk(self, root, "closure", "M1")[0]
        self.assertNotIn("row_clipped", page)


# ---------------------------------------------------------------------------
# Punto 2 — recovery con módulo explícito (Example Tool:FW-234)
# ---------------------------------------------------------------------------
def _scope_reasons(pages: list[dict]) -> set[str]:
    return {
        ref.get("reason") for page in pages for ref in page.get("refs", []) if ref.get("section") == "scope"
    }


class Punto2RecoveryConModulo(unittest.TestCase):
    def test_recovery_con_modulo_explicito_carga_su_scope(self):
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        pages = _walk(self, root, "recovery", "M1")
        self.assertNotIn("missing_source", _scope_reasons(pages))
        self.assertIsNone(pages[-1]["next_cursor"])

    def test_contrario_recovery_detecta_scope_de_otro_modulo(self):
        holder, root = _root(scope_module="M2")
        self.addCleanup(holder.cleanup)
        page = _query(root, "recovery", "M1")
        self.assertIn("scope_conflict", _scope_reasons([page]))
        self.assertEqual(page["gate_status"], "NEEDS_INPUT")

    def test_contrario_recovery_sin_scope_sigue_pidiendolo(self):
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        (root / "_project" / "04_active_scope.md").write_text("", encoding="utf-8")
        page = _query(root, "recovery", "M1")
        self.assertEqual(page["gate_status"], "NEEDS_INPUT")
        self.assertTrue(_scope_reasons([page]) & {"missing_source", "empty_source"})

    def test_no_regresion_startup_e_implementation(self):
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        startup = _query(root, "startup", "M1")
        self.assertEqual(startup["gate_status"], "PASS")
        self.assertFalse(_scope_reasons([startup]))
        implementation = _walk(self, root, "implementation", "M1")
        self.assertNotIn("missing_source", _scope_reasons(implementation))

    def test_recovery_sin_modulo_no_exige_scope(self):
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        page = _query(root, "recovery")
        self.assertNotIn("missing_source", _scope_reasons([page]))


# ---------------------------------------------------------------------------
# Punto 3 — objetivo de startup y aviso de set_session_pointer
# ---------------------------------------------------------------------------
class Punto3ObjetivoDeStartup(unittest.TestCase):
    def _state(self, in_progress: list[str], statuses: dict[str, str]) -> dict:
        state = fixture_state()
        state["modules"]["in_progress"] = list(in_progress)
        state["module_statuses"].update(statuses)
        return state

    def test_un_blocked_delante_no_gana_al_in_progress(self):
        state = self._state(["M2", "M1"], {"M2": "blocked", "M1": "in_progress"})
        result = state_queries.query_context(state, view="startup")
        self.assertEqual(result["module_id"], "M1")
        self.assertEqual(state["modules"]["in_progress"], ["M2", "M1"], "la consulta no reordena el estado")

    def test_ciclo_started_vigente_gana_por_cli(self):
        holder, root = _root(self._state(["M1", "M2"], {"M1": "in_progress", "M2": "in_progress"}))
        self.addCleanup(holder.cleanup)
        started = _mutate(root, "ciclo_started", {"module": "M2"})
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertEqual(_query(root, "startup")["module_id"], "M2")

    def test_ciclo_terminado_ya_no_cuenta(self):
        holder, root = _root(self._state(["M1", "M2"], {"M1": "in_progress", "M2": "in_progress"}))
        self.addCleanup(holder.cleanup)
        self.assertEqual(_mutate(root, "ciclo_started", {"module": "M2"}).returncode, 0)
        finished = _mutate(root, "ciclo_finished", {"module": "M2", "result": "paused"})
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        self.assertEqual(_query(root, "startup")["module_id"], "M1")

    def test_ciclo_started_de_un_modulo_fuera_de_in_progress_no_cuenta(self):
        holder, root = _root(self._state(["M1"], {"M1": "in_progress", "M2": "not_started"}))
        self.addCleanup(holder.cleanup)
        self.assertEqual(_mutate(root, "ciclo_started", {"module": "M2"}).returncode, 0)
        self.assertEqual(_query(root, "startup")["module_id"], "M1")

    def test_reordenado_del_builder_gana_a_un_ciclo_sin_cerrar(self):
        holder, root = _root(self._state(["M1", "M2"], {"M1": "in_progress", "M2": "in_progress"}))
        self.addCleanup(holder.cleanup)
        self.assertEqual(_mutate(root, "ciclo_started", {"module": "M2"}).returncode, 0)
        self.assertEqual(_mutate(root, "set_session_pointer", {"next_module": "M1"}).returncode, 0)
        self.assertEqual(_query(root, "startup")["module_id"], "M1")

    def test_log_enlazado_fuera_del_proyecto_no_da_pista(self):
        holder, root = _root(self._state(["M1", "M2"], {"M1": "in_progress", "M2": "in_progress"}))
        self.addCleanup(holder.cleanup)
        outside = Path(holder.name).parent / f"l8_log_externo_{Path(holder.name).name}.jsonl"
        outside.write_text(json.dumps({"action": "ciclo_started", "payload": {"module": "M2"}}) + "\n", encoding="utf-8")
        self.addCleanup(outside.unlink)
        log = root / "_project" / "state_log.jsonl"
        if log.exists():
            log.unlink()
        log.symlink_to(outside)
        self.assertEqual(_query(root, "startup")["module_id"], "M1")

    def test_contrario_orden_de_5_2_3_sin_estos_casos(self):
        state = self._state(["M1", "M2"], {"M1": "in_progress", "M2": "in_progress"})
        self.assertEqual(state_queries.query_context(state, view="startup")["module_id"], "M1")
        only_blocked = self._state(["M2"], {"M2": "blocked"})
        result = state_queries.query_context(only_blocked, view="startup")
        self.assertEqual(result["module_id"], "M2")

    def test_contrario_modulo_explicito_manda(self):
        state = self._state(["M2", "M1"], {"M2": "blocked", "M1": "in_progress"})
        self.assertEqual(state_queries.query_context(state, view="startup", module_id="M2")["module_id"], "M2")

    def test_set_session_pointer_avisa_si_la_descripcion_no_es_del_scope_ni_del_activo(self):
        holder, root = _root(self._state(["M1"], {"M1": "in_progress"}), scope_module="M2")
        self.addCleanup(holder.cleanup)
        result = _mutate(root, "set_session_pointer", {"active_module_description": "Trabajo de M2"})
        self.assertEqual(result.returncode, 0, "el aviso nunca rechaza")
        self.assertIn("AVISO", result.stdout)
        self.assertIn("M2", result.stdout)
        self.assertIn("next_module", result.stdout)

    def test_contrario_set_session_pointer_coherente_no_avisa(self):
        holder, root = _root(self._state(["M1"], {"M1": "in_progress"}), scope_module="M1")
        self.addCleanup(holder.cleanup)
        result = _mutate(root, "set_session_pointer", {"active_module_description": "Trabajo de M1"})
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("AVISO", result.stdout)

    def test_scope_sin_modulo_declarado_no_avisa(self):
        holder, root = _root(self._state(["M1"], {"M1": "in_progress"}), scope_module="Por definir")
        self.addCleanup(holder.cleanup)
        result = _mutate(root, "set_session_pointer", {"active_module_description": "Trabajo de M1"})
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("AVISO", result.stdout)

    def test_el_aviso_nuevo_no_se_guarda_en_el_log(self):
        holder, root = _root(self._state(["M1"], {"M1": "in_progress"}), scope_module="M2")
        self.addCleanup(holder.cleanup)
        result = _mutate(root, "set_session_pointer", {"active_module_description": "Trabajo de M2"})
        self.assertIn("AVISO", result.stdout)
        last = (root / "_project" / "state_log.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        self.assertEqual(json.loads(last)["action"], "set_session_pointer")
        self.assertNotIn("next_module", json.loads(last).get("result", ""))
        self.assertNotIn("M2", json.loads(last).get("result", ""))

    def test_aviso_de_blocked_existente_se_conserva(self):
        holder, root = _root(self._state(["M2"], {"M2": "blocked"}), scope_module="M2")
        self.addCleanup(holder.cleanup)
        result = _mutate(root, "set_session_pointer", {"active_module_description": "Trabajo de M2"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("está blocked", result.stdout)


# ---------------------------------------------------------------------------
# Punto 4 — hook de continuidad que agota 3 s (TL:FW-307)
# ---------------------------------------------------------------------------
class Punto4HookDeContinuidad(unittest.TestCase):
    def test_cada_documento_se_escanea_una_sola_vez_por_consulta(self):
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        (root / "_project" / "03_contracts.md").write_text(
            "# Contratos\n\n" + "".join(
                f"## Entrada{index:03d} — M9\n**Módulo:** `M9`\n\n- `op{index}()`\n\n" for index in range(200)
            ),
            encoding="utf-8",
        )
        calls: collections.Counter = collections.Counter()
        original = state_queries.scan_markdown

        def counting(text, *args, **kwargs):
            calls[text] += 1
            return original(text, *args, **kwargs)

        state_queries.scan_markdown = counting
        self.addCleanup(setattr, state_queries, "scan_markdown", original)
        import state_core
        import state_manager

        claude_continuity_hook._set_workspace_root(root)
        self.addCleanup(claude_continuity_hook._set_workspace_root, ROOT)
        state_manager.get_work_context(state_core.StateManager(), view="startup")
        self.assertTrue(calls, "la consulta debe seguir usando scan_markdown")
        self.assertEqual(max(calls.values()), 1, f"repeticiones: {max(calls.values())}")

    def test_limite_del_hook_sigue_en_3_s(self):
        self.assertEqual(claude_continuity_hook.QUERY_TIMEOUT_SECONDS, 3.0)

    def test_timeout_nombra_la_consulta_mcp_y_la_de_cli(self):
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        self.addCleanup(claude_continuity_hook._set_workspace_root, ROOT)
        envelope = claude_continuity_hook.build_continuity_context(
            root, {"source": "startup"}, query_timeout=0.001
        )
        self.assertEqual(envelope["status"], "TIMEOUT")
        message = envelope["via_de_recuperacion"]
        self.assertIn("get_work_context(view='startup')", message)
        self.assertIn("python3 .agent/scripts/state_manager.py query --view startup", message)
        self.assertNotIn(" 0.0 s", message, "el límite no se redondea a cero")


# ---------------------------------------------------------------------------
# Punto 5 — rechazos que dicen qué esperan (Example Tool:FW-231)
# ---------------------------------------------------------------------------
class Punto5RechazosConFormato(unittest.TestCase):
    def _root_with_acs(self):
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        (root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n- [x] AC-1 — evidencia: tests/test_state_queries.py\n"
            "- [x] AC-2 — evidencia: tests/test_state_queries.py\n"
            "- [x] AC-10 — evidencia: tests/test_state_queries.py\n",
            encoding="utf-8",
        )
        return root

    @staticmethod
    def _problems(root: Path, reference: str, view: str | None = None) -> list[str]:
        content = f"- conservar la vista — evidencia: {reference}\n"
        if view is None:
            return state_queries._evidence_problems("PRESERVE", content, root)
        return state_queries._evidence_problems("PRESERVE", content, root, view=view)

    def test_acn_vale_como_ac_n(self):
        root = self._root_with_acs()
        self.assertEqual(self._problems(root, "AC10"), [])
        self.assertEqual(self._problems(root, "AC2/AC10"), [])

    def test_contrario_en_una_lista_cada_ac_debe_estar_declarado(self):
        root = self._root_with_acs()
        problems = self._problems(root, "AC2/AC77")
        self.assertTrue(any("AC-77" in problem for problem in problems), problems)

    def test_acn_no_declarado_se_nombra_normalizado(self):
        root = self._root_with_acs()
        problems = self._problems(root, "AC77")
        self.assertTrue(any("AC-77" in problem for problem in problems), problems)

    def test_rechazo_dice_el_formato_aceptado(self):
        root = self._root_with_acs()
        problems = self._problems(root, "lo que se probó ayer")
        self.assertTrue(problems)
        text = " ".join(problems)
        for expected in ("AC-n", "ruta", "n/a —"):
            self.assertIn(expected, text)

    def test_pendiente_tdd_vale_en_implementacion(self):
        root = self._root_with_acs()
        self.assertEqual(
            self._problems(root, "pendiente-tdd: tests/test_que_aun_no_existe.py", view="implementation"), []
        )

    def test_pendiente_tdd_el_cierre_exige_que_exista(self):
        root = self._root_with_acs()
        for view in ("closure", None):
            problems = self._problems(root, "pendiente-tdd: tests/test_que_aun_no_existe.py", view=view)
            self.assertTrue(any("tests/test_que_aun_no_existe.py" in problem for problem in problems), (view, problems))
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "test_que_aun_no_existe.py").write_text("# ya existe\n", encoding="utf-8")
        self.assertEqual(self._problems(root, "pendiente-tdd: tests/test_que_aun_no_existe.py", view="closure"), [])

    def test_contrario_ruta_inexistente_sin_prefijo_sigue_rechazada_en_implementacion(self):
        root = self._root_with_acs()
        problems = self._problems(root, "tests/test_que_aun_no_existe.py", view="implementation")
        self.assertTrue(problems)

    def test_contrario_pendiente_tdd_no_sale_del_workspace(self):
        root = self._root_with_acs()
        problems = self._problems(root, "pendiente-tdd: ../fuera.py", view="implementation")
        self.assertTrue(any("sale del workspace" in problem for problem in problems), problems)

    def test_titulos_de_contratos_e_invariantes(self):
        contracts = context_sources._STABLE_HEADINGS["contracts"]
        invariants = context_sources._STABLE_HEADINGS["invariants"]
        for title in ("Contratos públicos — Example Tool API", "Contratos públicos", "Contratos",
                      "Contratos e Interfaces Públicas — Fixture", "Contratos — Example Tool API (interfaces públicas)"):
            self.assertTrue(contracts.match(title), title)
        for title in ("Invariantes de Negocio — Example Tool", "Invariantes de negocio", "Invariantes del Sistema — X",
                      "Invariantes"):
            self.assertTrue(invariants.match(title), title)

    def test_contrario_madre_e_hija_reconocidas_no_son_ambiguas(self):
        for name, text in (
            ("03_invariants.md", "# Invariantes del Sistema — X\n\n## Invariantes de negocio\n\nRN comun.\n"),
            ("03_contracts.md", "# Contratos e Interfaces Públicas — X\n\n## Contratos públicos\n\nContrato comun.\n"),
        ):
            holder, root = _root()
            self.addCleanup(holder.cleanup)
            (root / "_project" / name).write_text(text, encoding="utf-8")
            pages = _walk(self, root, "implementation", "M1")
            self.assertIsNone(pages[-1]["next_cursor"], name)

    def test_contrario_titulos_ajenos_no_se_reconocen(self):
        self.assertFalse(context_sources._STABLE_HEADINGS["contracts"].match("Contratos de terceros"))
        self.assertFalse(context_sources._STABLE_HEADINGS["invariants"].match("Invariantes ajenas al sistema"))


# ---------------------------------------------------------------------------
# Punto 6 — vistas acotadas al módulo (FW-310)
# ---------------------------------------------------------------------------
CONTRACTS = (
    "# Contratos\n\n"
    "## Estado actual de endpoints HTTP\n\nTexto ajeno sin propietario.\n\n"
    "## Phase 6 refactor\n\nOtro bloque ajeno.\n\n"
    "## PaperRuntimeService\n\nInterfaz sin propietario que M1 consume.\n\n"
    "## Cola de avisos de M1\n\nBloque sin propietario que nombra el módulo.\n\n"
    "## Iface — M1\n**Módulo:** `M1`\n\n- `op()`\n\n"
    "## Ajena — M9\n**Módulo:** `M9`\n\n- `otra()`\n"
)
INVARIANTS = (
    "# Invariantes del Sistema — Fixture\n\n"
    "## 1. Dominio\n\nEntidades comunes a todo el sistema.\n\n"
    "## 2. Reglas de negocio\n\n"
    "### RN-001: Trazabilidad obligatoria\n\nVale para todos.\n\n"
    "### RN-002: Presupuesto aislado de M9\n\nSolo afecta a M9.\n\n"
    "### RN-003: Paradas de M1\n\nAfecta a M1.\n\n"
    "### RN-004: Frontera M1 y M9\n\nAfecta a los dos.\n"
)


class Punto6VistasAcotadas(unittest.TestCase):
    MODULE_TEXT = "Scope de M1. Estado del trabajo. Phase uno. Consume PaperRuntimeService."

    def test_contratos_sin_propietario_por_palabra_comun_no_se_entregan(self):
        body, omitted = context_sources.select_contracts(CONTRACTS, "M1", self.MODULE_TEXT)
        self.assertNotIn("Estado actual de endpoints HTTP", body)
        self.assertNotIn("Phase 6 refactor", body)
        self.assertIn("Estado actual de endpoints HTTP", omitted)
        self.assertIn("Phase 6 refactor", omitted)

    def test_contrario_contratos_que_si_son_del_modulo_se_entregan(self):
        body, omitted = context_sources.select_contracts(CONTRACTS, "M1", self.MODULE_TEXT)
        for title in ("Iface — M1", "PaperRuntimeService", "Cola de avisos de M1"):
            self.assertIn(title, body)
        self.assertNotIn("Ajena — M9", body)
        self.assertIn("Ajena — M9", omitted)

    def test_invariantes_de_otro_modulo_pasan_a_referencia(self):
        body, omitted = context_sources.select_invariants(INVARIANTS, "M1", {"M1", "M9"})
        self.assertNotIn("Solo afecta a M9", body)
        self.assertIn("RN-002: Presupuesto aislado de M9", omitted)
        for kept in ("Entidades comunes", "Vale para todos", "Afecta a M1", "Afecta a los dos"):
            self.assertIn(kept, body)

    def test_invariantes_de_una_dependencia_se_entregan(self):
        body, omitted = context_sources.select_invariants(
            INVARIANTS, "M1", {"M1", "M9"}, "Depende de: M9\n"
        )
        self.assertIn("Solo afecta a M9", body)
        self.assertEqual(omitted, [])

    def test_contrario_invariantes_sin_modulos_quedan_enteras(self):
        plain = "# Invariantes\n\n## Regla A\n\nTexto.\n\n## Regla B\n\nTexto.\n"
        self.assertEqual(context_sources.select_invariants(plain, "M1", {"M1", "M9"}), (plain, []))
        self.assertEqual(context_sources.select_invariants(INVARIANTS, None, {"M1", "M9"}), (INVARIANTS, []))

    def test_vista_implementation_entrega_invariantes_acotadas_y_refs_exactas(self):
        state = fixture_state()
        state["module_statuses"]["M9"] = "not_started"
        holder, root = _root(state)
        self.addCleanup(holder.cleanup)
        (root / "_project" / "03_invariants.md").write_text(INVARIANTS, encoding="utf-8")
        pages = _walk(self, root, "implementation", "M1")
        text = "\n".join(
            str(item.get("content")) for page in pages for item in page["data"]["items"]
            if item.get("selector") == "invariants"
        )
        self.assertNotIn("Solo afecta a M9", text)
        self.assertIn("Afecta a M1", text)
        refs = [ref for page in pages for ref in page["refs"] if ref.get("section") == "invariants"]
        self.assertTrue(refs, "lo no entregado queda como referencia exacta")
        self.assertTrue(any("RN-002" in heading for ref in refs for heading in ref.get("omitted_headings", [])))
        self.assertEqual(refs[0]["path"], "_project/03_invariants.md")


# ---------------------------------------------------------------------------
# Punto 7 — revisor automático de Codex (FW-309)
# ---------------------------------------------------------------------------
READ_TOOLS = {"get_session_brief", "get_full_state", "lookup_state", "get_work_context", "describe_action"}


class Punto7CodexLecturasMcp(unittest.TestCase):
    def _tools(self) -> dict[str, dict]:
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        process = subprocess.run(
            [sys.executable, "-B", ".agent/scripts/state_manager.py"], cwd=root,
            input="\n".join(json.dumps(request) for request in requests) + "\n",
            capture_output=True, text=True, timeout=30, check=False,
        )
        responses = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
        listed = next(response for response in responses if response.get("id") == 2)
        return {tool["name"]: tool for tool in listed["result"]["tools"]}

    def test_lecturas_declaran_solo_lectura(self):
        tools = self._tools()
        for name in READ_TOOLS:
            self.assertIs(tools[name].get("annotations", {}).get("readOnlyHint"), True, name)

    def test_contrario_mutate_state_no_es_solo_lectura(self):
        tools = self._tools()
        self.assertIsNot(tools["mutate_state"].get("annotations", {}).get("readOnlyHint"), True)

    def test_config_nueva_pide_revision_solo_para_escrituras(self):
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        _, result = ensure_codex_integration._ensure_native_codex_config(root, check=False)
        self.assertRegex(
            result["serialized"],
            r'(?ms)^\[mcp_servers\.state_manager\][^\[]*^default_tools_approval_mode\s*=\s*"writes"',
        )

    def test_contrario_config_existente_se_preserva_byte_a_byte(self):
        holder, root = _root()
        self.addCleanup(holder.cleanup)
        existing = (
            'model = "x"\n\n[mcp_servers.state_manager]\ncommand = "python3"\n'
            'args = [".agent/scripts/state_manager.py"]\n\n'
            '[mcp_servers.state_manager.tools.get_session_brief]\napproval_mode = "approve"\n'
        )
        (root / ".codex").mkdir(exist_ok=True)
        (root / ".codex" / "config.toml").write_text(existing, encoding="utf-8")
        changed, result = ensure_codex_integration._ensure_native_codex_config(root, check=False)
        self.assertFalse(changed)
        self.assertEqual(result["serialized"], existing)

    def test_contrario_reglas_de_shell_de_fw_305_intactas(self):
        text = ensure_codex_integration.CODEX_RULES_TEXT
        self.assertIn('"query", "lookup", "describe"', text)
        self.assertNotIn("mutate_state", text.split("not_match")[0])


# ---------------------------------------------------------------------------
# Punto 8 — evidencia con extensión larga (TL:FW-302)
# ---------------------------------------------------------------------------
class Punto8ExtensionLarga(unittest.TestCase):
    def test_validate_acceptance_no_recorta_la_extension(self):
        found = [match.group(1) for match in validate_acceptance.PATH_RE.finditer(
            "evidencia: _scratch/campaign/real_terminal_vivo.typescript"
        )]
        self.assertEqual(found, ["_scratch/campaign/real_terminal_vivo.typescript"])

    def test_evidencia_de_vistas_no_recorta_la_extension(self):
        paths, _ = state_queries._evidence_references("real_terminal_vivo.typescript y docs/a/b.typescript")
        self.assertIn("real_terminal_vivo.typescript", paths)
        self.assertIn("docs/a/b.typescript", paths)

    def test_contrario_puntuacion_final_y_extensiones_cortas(self):
        found = [match.group(1) for match in validate_acceptance.PATH_RE.finditer(
            "ver src/a.py, tests/test_b.py) y docs/c.md."
        )]
        self.assertEqual(found, ["src/a.py", "tests/test_b.py", "docs/c.md"])


if __name__ == "__main__":
    unittest.main()
