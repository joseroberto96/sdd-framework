#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recorrido end-to-end del ciclo de vida de un módulo.

Por qué existe: hasta 2026-07-24 el framework tenía ~680 tests y **ninguno**
que recorriera el camino completo. Los tres fallos más caros del semestre se
colaron por ese hueco, todos con la suite en verde:

  - FW-008: `.state.lock` entraba en el hash de los review receipts, así que el
    gate tier_2 estaba roto POR CONSTRUCCIÓN para el 100% de los módulos.
    Quemó dos cierres reales en dos proyectos antes de que un humano lo viera.
  - FW-013: el guard destructivo se invocaba por ruta relativa y un `cd` dejaba
    la sesión sin Bash.
  - FW-106: git exporta GIT_DIR al ejecutar el hook, y los tests con repos
    temporales corrompían el índice real.

Ninguno era un fallo de una pieza: los tres vivían en la costura entre piezas
que, por separado, pasaban sus tests. Este archivo cubre esa costura.

No sustituye a los tests unitarios: cuando algo falla aquí, dicen dónde. Este
dice *que* falla, que es lo que faltaba.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core  # noqa: E402


def _entorno_git_limpio() -> dict:
    """Entorno sin las variables GIT_* (FW-106).

    Si la suite corre dentro de un hook, git exporta GIT_DIR/GIT_INDEX_FILE y
    tienen precedencia sobre `-C`: un repo temporal acabaría operando sobre el
    índice REAL del repo, marcando archivos como borrados. Ocurrió de verdad,
    a tres agentes distintos el mismo día.
    """
    entorno = dict(os.environ)
    for clave in [k for k in entorno if k.startswith("GIT_")]:
        del entorno[clave]
    return entorno


class CicloCompletoDeModuloTests(unittest.TestCase):
    """Un módulo tier_2 desde que se abre hasta que se commitea cerrado."""

    @classmethod
    def setUpClass(cls):
        """Siembra una plantilla inicializada una sola vez; cada test la copia."""
        cls._base_tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._base_tmp.name) / "plantilla"
        cls.base.mkdir(parents=True)
        for parte in (".agent", "_project", ".claude"):
            shutil.copytree(
                ROOT / parte, cls.base / parte,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
            )
        for archivo in ("llms.txt", "CLAUDE.md", "AGENTS.md", "GEMINI.md", "GUARDRAILS_MASTER.md",
                        "README.md", "opencode.json", ".gitattributes"):
            if (ROOT / archivo).exists():
                shutil.copy(ROOT / archivo, cls.base / archivo)
        (cls.base / "tests").mkdir(exist_ok=True)
        shutil.copy(ROOT / "tests" / "gate_calibration.py", cls.base / "tests" / "gate_calibration.py")
        estado_path = cls.base / "_project" / "03_project_state.json"
        estado = json.loads(estado_path.read_text(encoding="utf-8"))
        estado["framework_mode"] = "initialized"
        estado["project"] = "proyecto_e2e"
        # FW-154: el fixture copia `_project/` del repo anfitrión. En la plantilla
        # eso es un proyecto vacío, pero este archivo también viaja dentro de los
        # proyectos adoptados — y allí arrastraba su historia real: módulos,
        # bugs y un state_log que el replay estricto no siempre puede reproducir
        # (4 de 7 proyectos tienen historia anterior a la bitácora). El test
        # fallaba por el anfitrión, no por el ciclo. Se parte SIEMPRE de cero.
        for clave, vacio in (
            ("modules", {"completed": [], "in_progress": [], "pending": []}),
            ("module_statuses", {}), ("work_tiers", {}), ("module_specs", {}),
            ("workflow_receipts", {"approvals": {}, "reviews": {}, "executions": {}}),
            ("closure_rejections", []), ("_bugs", []), ("_clarifications", []),
            ("_decisions", []), ("active_module", None),
        ):
            estado[clave] = vacio
        (cls.base / "_project" / "state_log.jsonl").unlink(missing_ok=True)
        estado["stack"] = {
            "backend": "Python stdlib", "frontend": "ninguno",
            "database": "ninguna", "auth": "ninguna",
        }
        # Obligatorio en modo initialized (graduación de rigor por capacidad).
        estado.setdefault("execution_profile", {"capability_tier": "strong"})
        # Un proyecto inicializado real resuelve los placeholders de plantilla
        # en /nuevo-proyecto; sin esto el doctor falla por el andamiaje, no por
        # el ciclo, y el commit final no probaría nada del recorrido.
        sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
        import framework_doctor as _fd  # el mismo patrón que valida el doctor
        objetivo = list((cls.base / "_project").rglob("*.md")) + [cls.base / "CLAUDE.md"]
        for md in objetivo:
            if not md.exists():
                continue
            texto = md.read_text(encoding="utf-8", errors="ignore")
            limpio = _fd.PLACEHOLDER_RE.sub("definido para el proyecto e2e", texto)
            if limpio != texto:
                md.write_text(limpio, encoding="utf-8")
        # FW-187: el fixture copia `_project/` del anfitrión (FW-154). En un
        # adoptante Codex, `15_environment_profile.md` declara `- Herramienta:
        # Codex` — un valor real, no un placeholder — y el proyecto sembrado
        # hereda ese harness sin el cableado `.codex/` (config.toml, hooks.json,
        # .mcp.json), así que el doctor del commit de cierre falla por el
        # anfitrión, no por el ciclo. Este test no va de Codex: se normaliza el
        # harness a un valor neutro, igual que se resuelven los placeholders.
        perfil = cls.base / "_project" / "15_environment_profile.md"
        if perfil.exists():
            perfil.write_text(
                re.sub(
                    r"^([ \t]*(?:[-*][ \t]+)?(?:\*\*)?(?:Herramienta|Entorno(?:[ \t]+principal)?|Harness(?:[ \t]+principal)?)(?:\*\*)?[ \t]*:(?:\*\*)?)[ \t]*.*$",
                    r"\1 Claude Code",
                    perfil.read_text(encoding="utf-8"),
                    flags=re.MULTILINE | re.IGNORECASE,
                ),
                encoding="utf-8",
            )
        estado_path.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
        # Un proyecto real tiene .gitignore, y aquí no es decorado: sin él, los
        # .pyc que generan los propios scripts aparecen como untracked y entran
        # en el hash del diff, de modo que el receipt de revisión nace stale.
        # Descubierto montando este mismo recorrido.
        shutil.copy(ROOT / ".agent" / "templates" / "gitignore.base", cls.base / ".gitignore")
        # Wrappers de las 4 superficies: el doctor los exige y los genera el
        # propio sync, igual que en una adopción real.
        subprocess.run([sys.executable, ".agent/scripts/sync_claude_commands.py"],
                       cwd=cls.base, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls._base_tmp.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proyecto = Path(self._tmp.name) / "proyecto"
        shutil.copytree(self.base, self.proyecto)
        self.entorno = _entorno_git_limpio()
        self._git("init", "-q", ".")
        # FW-192: `git` puede lanzar `gc --auto` en segundo plano tras un commit.
        # Ese proceso sigue escribiendo en `.git/` mientras `tearDown` borra el
        # temporal, y `rmtree` muere con "Directory not empty" — un fallo de
        # limpieza que se lee como fallo del ciclo y tiñe la suite entera de rojo
        # de forma intermitente (más probable bajo la carga de la suite completa).
        # Desactivar el mantenimiento automático ataca la causa; enmascarar el
        # error de limpieza escondería además residuo real.
        self._git("config", "gc.auto", "0")
        self._git("config", "maintenance.auto", "false")
        self._git("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "--allow-empty", "-m", "base")

    def tearDown(self):
        self._tmp.cleanup()

    # ---------------------------------------------------------------- helpers

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.proyecto), *args],
            capture_output=True, text=True, env=self.entorno,
        )

    def _mutar(self, accion: str, payload: dict) -> subprocess.CompletedProcess:
        """Invoca el CLI real del motor — la misma vía que usa un operador."""
        return subprocess.run(
            [sys.executable, ".agent/scripts/state_manager.py", "mutate",
             "--action", accion, "--payload", json.dumps(payload, ensure_ascii=False)],
            cwd=self.proyecto, capture_output=True, text=True, env=self.entorno,
        )

    def _mutar_ok(self, accion: str, payload: dict) -> str:
        resultado = self._mutar(accion, payload)
        self.assertEqual(
            resultado.returncode, 0,
            f"'{accion}' falló: {(resultado.stdout + resultado.stderr).strip()}",
        )
        return resultado.stdout

    def _estado(self) -> dict:
        return json.loads((self.proyecto / "_project" / "03_project_state.json").read_text(encoding="utf-8"))

    def _sembrar_producto(self) -> None:
        """Código y tests reales: el gate anti-fabricación exige que los
        artefactos citados en la evidencia existan de verdad."""
        (self.proyecto / "src").mkdir(exist_ok=True)
        (self.proyecto / "tests").mkdir(exist_ok=True)
        (self.proyecto / "src" / "core.py").write_text(
            "def run(entrada: str) -> str:\n    return entrada.strip().lower()\n", encoding="utf-8")
        (self.proyecto / "tests" / "test_core.py").write_text(
            'import sys, unittest\n'
            'sys.path.insert(0, "src")\n'
            'from core import run\n\n'
            'class RunTests(unittest.TestCase):\n'
            '    def test_run_minimo(self):\n'
            '        self.assertEqual(run("  Hola  "), "hola")\n\n'
            'if __name__ == "__main__":\n'
            '    unittest.main()\n', encoding="utf-8")

    def _escribir_acs(self, con_evidencia: bool = True) -> None:
        sufijo = (
            " — evidencia: `tests/test_core.py::test_run_minimo` en verde"
            if con_evidencia else ""
        )
        with (self.proyecto / "_project" / "08_module_acceptance.md").open("a", encoding="utf-8") as fh:
            fh.write(
                "\n## Módulo — M01_core\n\n"
                # FW-147: el sello del builder es ahora parte del cierre, no un
                # post-it que solo leía /nueva-fase.
                "Estado: ✅ Verificado con cliente\n\n"
                f"- [x] `run()` normaliza la entrada mínima{sufijo}\n"
            )

    def _abrir_modulo_tier_2(self) -> None:
        self._mutar_ok("add_module", {"id": "M01_core"})
        self._mutar_ok("update_module_status", {"id": "M01_core", "status": "in_progress"})
        self._mutar_ok("add_work_tier", {
            "id": "M01_core", "tier": "tier_2", "requires_second_review": True,
            "rationale": "toca el contrato público del núcleo", "classified_by": "agente",
        })
        self._mutar_ok("classify_module_spec", {
            "id": "M01_core", "data_model": "n/a", "api_contract": "n/a",
            "justification": "módulo interno sin persistencia ni API",
            "classified_by": "scope-siguiente",
        })
        self._mutar_ok("register_decision", {
            "title": "Contrato del núcleo",
            "context": "Hay que fijar la interfaz pública",
            "decision": "Exponer solo run()", "module_id": "M01_core",
        })

    def _aprobar_y_revisar(self) -> None:
        """Aprobación y segunda revisión, en este orden.

        El APPROVE_RECEIPT sella un snapshot de scope/ACs/tier/ADRs, así que
        registrarlo antes de fijarlos lo deja obsoleto al llegar al cierre.
        Ese orden vive hoy solo en la prosa de `/cierre-modulo`; aquí queda
        fijado como comportamiento.
        """
        self._mutar_ok("record_approval_receipt", {
            "module": "M01_core", "approved_by": "builder",
            "evidence": "aprobación explícita del builder tras revisar scope y ACs",
            "risk_summary": "cambio aditivo sobre el núcleo, rollback por revert",
            "adr_refs": ["ADR-001"],
        })
        self._mutar_ok("start_independent_review", {
            "module": "M01_core", "reviewer_id": "subagente-reviewer",
            "implementer_id": "agente-principal", "mode": "READ_ONLY_SUBAGENTS",
        })
        self._mutar_ok("record_review_receipt", {
            "module": "M01_core", "result": "pass", "reviewer_id": "subagente-reviewer",
            "evidence": "Reviewer y Security sin hallazgos; contrato y ACs verificados",
        })
        # FW-144: paso 3.8 con recibo. Se sella DESPUÉS del review y antes del
        # paso 0 por la misma razón que el receipt de review va tras la
        # aprobación: fija el diff_hash vigente y cualquier cambio posterior de
        # código lo deja stale.
        self._mutar_ok("record_execution_receipt", {
            "module": "M01_core", "kind": "ok_automatizada",
            "command": "python3 -c \"from core import run; print(run(' x '))\"",
            "exit_code": 0,
            "evidence": "salida observada 'x' — normaliza espacios como declara el AC",
        })
        # FW-201: paso 5.5 de /cierre-modulo — cosecha de fricción declarada
        # antes del cierre (HARVEST GATE).
        self._mutar_ok("record_harvest", {"module_id": "M01_core", "classes": "none"})
        # FW-134: EXPERT GATE (paso 3.9) en tier_2 exige ambos checkpoints
        # sellados — el doctor los verifica post-cierre (validate_domain_
        # sweep_receipts / validate_expert_audit_receipts), así que un módulo
        # tier_2 completado sin ellos rompe el pre-commit aunque complete_module
        # ya lo haya aceptado.
        self._mutar_ok("record_checkpoint", {
            "module": "M01_core", "kind": "domain_sweep", "scope_id": "M01_core",
            "evidence": "Barrido de sub-dominios: sin sub-dominios fuera de las skills core.",
            "reference": "09_domain_profile.md fila M01_core",
        })
        self._mutar_ok("record_checkpoint", {
            "module": "M01_core", "kind": "expert_audit", "scope_id": "M01_core",
            "evidence": "Ningún experto activo toca el módulo.",
            "reference": "09_domain_profile.md → Expertos activos",
        })
        # Paso 0 de /cierre-modulo: `completed` solo es alcanzable desde aquí.
        self._mutar_ok("update_module_status",
                       {"id": "M01_core", "status": "ready_for_validation"})

    # ------------------------------------------------------------------ tests

    def test_un_modulo_tier_2_recorre_todo_el_ciclo_y_se_commitea(self):
        """El camino feliz completo, extremo a extremo."""
        self._sembrar_producto()
        self._abrir_modulo_tier_2()
        self._escribir_acs()

        evidencia = subprocess.run(
            [sys.executable, ".agent/scripts/validate_acceptance.py", "M01_core"],
            cwd=self.proyecto, capture_output=True, text=True, env=self.entorno)
        self.assertEqual(evidencia.returncode, 0,
                         f"EVIDENCE GATE debería pasar: {evidencia.stdout}")

        # FW-144/147: la CLI de cierre debe ser una previsualización FIEL del
        # veredicto del motor, no una versión optimista. Antes de sellar el paso
        # 3.8 ya tiene que decir que falta — si dijera OK aquí, un agente haría
        # todo el recorrido para chocar al final, que es la fricción que este
        # lote existe para quitar.
        previo = subprocess.run(
            [sys.executable, ".agent/scripts/validate_closure.py", "M01_core"],
            cwd=self.proyecto, capture_output=True, text=True, env=self.entorno)
        self.assertEqual(previo.returncode, 1,
                         f"la previsualización debe bloquear antes del paso 3.8: {previo.stdout}")
        self.assertIn("execution receipt ausente", previo.stdout)

        self._aprobar_y_revisar()

        listo = subprocess.run(
            [sys.executable, ".agent/scripts/validate_closure.py", "M01_core"],
            cwd=self.proyecto, capture_output=True, text=True, env=self.entorno)
        self.assertEqual(listo.returncode, 0,
                         f"con todo sellado la previsualización debe permitir: {listo.stdout}")

        self._mutar_ok("complete_module", {"id": "M01_core"})

        estado = self._estado()
        self.assertEqual(estado["module_statuses"]["M01_core"], "completed")
        self.assertIn("M01_core", estado["modules"]["completed"])
        # FW-149: el cierre deja constancia de quién lo cerró.
        self.assertIn("closed_by", estado["work_tiers"]["M01_core"])
        # FW-144: el receipt de ejecución quedó sellado y respaldado por el log.
        self.assertEqual(
            estado["workflow_receipts"]["executions"]["M01_core"]["kind"],
            "ok_automatizada",
        )

        # El commit real, con el hook instalado: doctor + transition gate + suite.
        subprocess.run(["bash", ".agent/scripts/pre_commit.sh", "install"],
                       cwd=self.proyecto, capture_output=True, env=self.entorno)
        self._git("add", "-A")
        commit = self._git("-c", "user.email=t@t", "-c", "user.name=t",
                           "commit", "-m", "M01_core cerrado")
        self.assertEqual(commit.returncode, 0,
                         f"el commit del cierre debería pasar los gates: {commit.stdout + commit.stderr}")

        # Integridad tras el recorrido: las tres vías deben coincidir.
        log = self.proyecto / "_project" / "state_log.jsonl"
        self.assertEqual(state_core.verify_log_chain(log), [], "la cadena del log debe quedar íntegra")

        auditoria = subprocess.run(
            [sys.executable, ".agent/scripts/replay_state_log.py", "--audit"],
            cwd=self.proyecto, capture_output=True, text=True, env=self.entorno)
        self.assertEqual(auditoria.returncode, 0,
                         f"cada entidad debe tener respaldo: {auditoria.stdout}")

        replay = subprocess.run(
            [sys.executable, ".agent/scripts/replay_state_log.py"],
            cwd=self.proyecto, capture_output=True, text=True, env=self.entorno)
        self.assertEqual(replay.returncode, 0,
                         f"el log del ciclo debe poder reproducirse: {replay.stdout + replay.stderr}")

        # FW-149: el estado reconstruido desde la bitácora debe coincidir con el
        # vivo también en el sello de autoría. La primera versión lo escribía
        # solo en la rama normal de `complete_module`, así que el replay lo
        # perdía y las dos verdades divergían en silencio.
        reconstruido = self.proyecto / "_project" / "replay_out.json"
        escrito = subprocess.run(
            [sys.executable, ".agent/scripts/replay_state_log.py", "--write",
             "--out", str(reconstruido)],
            cwd=self.proyecto, capture_output=True, text=True, env=self.entorno)
        self.assertEqual(escrito.returncode, 0,
                         f"el replay con --write debe completar: {escrito.stdout + escrito.stderr}")
        replicado = json.loads(reconstruido.read_text(encoding="utf-8"))
        self.assertEqual(
            replicado["work_tiers"]["M01_core"].get("closed_by"),
            estado["work_tiers"]["M01_core"].get("closed_by"),
            "el sello closed_by debe sobrevivir al replay",
        )
        self.assertEqual(
            replicado["workflow_receipts"]["executions"]["M01_core"]["kind"],
            "ok_automatizada",
            "el execution receipt debe sobrevivir al replay",
        )

    def test_sin_evidencia_en_los_acs_el_ciclo_no_llega_al_cierre(self):
        """Negativo: el recorrido detecta que falta un eslabón.

        Un camino feliz que pasa no demuestra gran cosa si el mismo camino
        pasaría igual con la evidencia vacía.
        """
        self._sembrar_producto()
        self._abrir_modulo_tier_2()
        self._escribir_acs(con_evidencia=False)
        self._aprobar_y_revisar()

        cierre = self._mutar("complete_module", {"id": "M01_core"})

        self.assertNotEqual(cierre.returncode, 0, "cerrar sin evidencia debe bloquearse")
        self.assertEqual(self._estado()["module_statuses"]["M01_core"], "ready_for_validation")

    def test_un_receipt_fabricado_a_mano_no_cierra_el_modulo(self):
        """Negativo: la evidencia escrita directamente en el estado no sirve.

        Es el vector que midió el sabotaje controlado: sin pasar por el motor,
        el receipt existe en el estado y el doctor lo da por bueno — pero la
        bitácora no lo registra.
        """
        self._sembrar_producto()
        self._abrir_modulo_tier_2()
        self._escribir_acs()
        self._mutar_ok("record_approval_receipt", {
            "module": "M01_core", "approved_by": "builder",
            "evidence": "aprobación explícita del builder",
            "risk_summary": "cambio aditivo", "adr_refs": ["ADR-001"],
        })

        estado_path = self.proyecto / "_project" / "03_project_state.json"
        estado = json.loads(estado_path.read_text(encoding="utf-8"))
        estado.setdefault("workflow_receipts", {}).setdefault("reviews", {})["task:M01_core"] = [{
            "module": "M01_core", "result": "pass", "status": "completed",
            "evidence": "revisión independiente completa, 0 hallazgos",
        }]
        estado_path.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")

        cierre = self._mutar("complete_module", {"id": "M01_core"})

        self.assertNotEqual(cierre.returncode, 0, "un receipt sin evento en la bitácora no debe sellar el cierre")
        self.assertIn("state_log", (cierre.stdout + cierre.stderr))


if __name__ == "__main__":
    unittest.main()
