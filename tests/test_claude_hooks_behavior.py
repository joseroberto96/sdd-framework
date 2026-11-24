#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-235: los hooks de `.claude/settings.json` hacen lo que dicen, ejecutados de verdad.

- **Formateo.** Claude Code lanza los hooks en el cwd de la sesión, y ese cwd se
  desplaza (un `cd` en un Bash lo deja en un subdirectorio). Con la ruta relativa
  el hook fallaba en silencio; con solo la ruta absoluta seguía sin formatear,
  porque `post_edit_format.sh` calculaba la raíz con `pwd`.
- **Contador de errores.** Un único fichero de `/tmp` para todas las sesiones y
  proyectos, que nunca se reiniciaba con un éxito: dos fallos separados por horas
  avisaban de "errores consecutivos". Decisión del builder 2026-09-14: contar por
  sesión y decir la verdad en el aviso.
- **Recordatorio del estado.** Saltaba al editar cualquier fichero cuyo contenido
  mencionara `03_project_state.json`.

Los comandos se leen del `.base` y se ejecutan con `bash -c`; `/tmp/` se redirige
a un directorio temporal para no tocar los contadores de sesiones reales.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".agent" / "templates" / "settings.json.base"
FORMAT_SCRIPT = ROOT / ".agent" / "scripts" / "post_edit_format.sh"

sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import ensure_claude_settings as ecs  # noqa: E402

_UNFORMATTED = "x=1;y =  2\n"


def _base() -> dict:
    return json.loads(BASE.read_text(encoding="utf-8"))


def _command(event: str, needle: str) -> str:
    for group in _base()["hooks"][event]:
        for hook in group["hooks"]:
            if needle in hook["command"]:
                return hook["command"]
    raise AssertionError(f"no hay hook {event} con {needle!r}")


class FormatHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proyecto con espacios"
        (self.project / ".agent" / "scripts").mkdir(parents=True)
        (self.project / "src" / "deep").mkdir(parents=True)
        shutil.copy2(FORMAT_SCRIPT, self.project / ".agent" / "scripts" / "post_edit_format.sh")
        # Formateador falso: deja constancia añadiendo una línea.
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        ruff = self.bin / "ruff"
        ruff.write_text('#!/bin/bash\n[ "$1" = format ] && echo "# formateado" >> "$2"\n', encoding="utf-8")
        ruff.chmod(0o755)

    def _edit_and_hook(self, cwd: Path, target: Path) -> bool:
        target.write_text(_UNFORMATTED, encoding="utf-8")
        python_dir = Path(sys.executable).parent
        env = {
            "PATH": f"{self.bin}:{python_dir}:" + os.environ.get("PATH", "/usr/bin:/bin"),
            "CLAUDE_PROJECT_DIR": str(self.project),
            "HOME": str(self.tmp),
        }
        subprocess.run(
            ["bash", "-c", _command("PostToolUse", "post_edit_format.sh")],
            cwd=cwd, env=env, capture_output=True, text=True,
            input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(target)}}),
        )
        return target.read_text(encoding="utf-8") != _UNFORMATTED

    def test_formatea_con_el_cwd_en_cualquier_carpeta_del_proyecto(self):
        cwds = [self.project, self.project / "src", self.project / "src" / "deep", self.project / ".agent" / "scripts"]
        targets = [self.project / "raiz.py", self.project / "src" / "deep" / "hondo.py"]
        for cwd in cwds:
            for target in targets:
                with self.subTest(cwd=str(cwd.relative_to(self.tmp)), target=target.name):
                    self.assertTrue(self._edit_and_hook(cwd, target), "el hook no formateó")

    def test_no_formatea_fuera_del_proyecto(self):
        """Preservar: un fichero fuera de la raíz no se toca."""
        fuera = self.tmp / "fuera.py"
        self.assertFalse(self._edit_and_hook(self.project / "src", fuera))


class ErrorCounterHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.command = _command("PostToolUseFailure", "cc_err_count").replace("/tmp/", f"{self.tmp}/")

    def _fail(self, session_id: str) -> str:
        result = subprocess.run(
            ["bash", "-c", self.command], capture_output=True, text=True,
            input=json.dumps({"session_id": session_id, "hook_event_name": "PostToolUseFailure"}),
        )
        return result.stdout

    def test_fallos_de_sesiones_distintas_no_se_suman(self):
        self.assertEqual(self._fail("sesion-a"), "")
        self.assertEqual(self._fail("sesion-b"), "", "un fallo de otra sesión disparó el aviso")

    def test_dos_fallos_en_la_misma_sesion_avisan_sin_decir_consecutivos(self):
        self.assertEqual(self._fail("sesion-c"), "")
        salida = self._fail("sesion-c")
        mensaje = json.loads(salida)["systemMessage"]
        self.assertIn("/debug", mensaje)
        self.assertIn("en esta sesión", mensaje)
        self.assertNotIn("consecutivos", mensaje, "el hook no ve los éxitos: no puede afirmar que sean consecutivos")


class StateReminderRetirementTests(unittest.TestCase):
    """FW-4.5 Lote 3: El recordatorio de 03_project_state sobre Write|Edit se retira.
    El framework prohíbe mutar el estado por Write/Edit (solo MCP o CLI), por lo que
    el hook era inútil por construcción. Debe quedar fuera de la base y retirarse de
    proyectos adoptantes durante actualizaciones."""

    def test_recordatorio_retirado_de_base(self):
        base = _base()
        for group in base.get("hooks", {}).get("PostToolUse", []):
            for hook in group.get("hooks", []):
                self.assertNotIn("03_project_state", hook.get("command", ""))
                self.assertNotIn("project_state.json actualizado", hook.get("command", ""))

    def test_actualizacion_retira_formas_conocidas(self):
        legacy_grep = (
            "input=$(cat); echo \"$input\" | grep -q '03_project_state\\.json' && "
            "echo '{\"systemMessage\": \"project_state.json actualizado. Si acabas de cerrar un módulo, ejecuta /cierre-modulo.\"}' || true"
        )
        fw235_case = (
            "input=$(cat); path=$(printf '%s' \"$input\" | python3 -c \"import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))\" 2>/dev/null); "
            "case \"$path\" in 03_project_state.json|*/03_project_state.json) echo '{\"systemMessage\": \"project_state.json actualizado. Si acabas de cerrar un módulo, ejecuta /cierre-modulo.\"}';; esac; true"
        )
        for name, cmd in (("legacy_grep", legacy_grep), ("fw235_case", fw235_case)):
            with self.subTest(form=name):
                target = {"hooks": {
                    "PostToolUse": [
                        {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": cmd}]},
                        {"matcher": "Write|Edit|MultiEdit", "hooks": [{"type": "command", "command": "post_edit_format.sh"}]},
                    ]
                }}
                merged, changes = ecs.merge_settings(_base(), target)
                post_hooks = [h["command"] for g in merged["hooks"]["PostToolUse"] for h in g["hooks"]]
                self.assertNotIn(cmd, post_hooks)
                self.assertTrue(any("hook del framework retirado (03_project_state)" in c for c in changes))
                # Idempotencia
                _, second_changes = ecs.merge_settings(_base(), merged)
                self.assertFalse(any("03_project_state" in c for c in second_changes))


class AdopterSettingsMigrationTests(unittest.TestCase):
    """Los 9 adoptantes tienen la forma anterior: `ensure_claude_settings` debe
    sustituir cada hook en sitio, sin duplicar ni dejar la versión vieja."""

    OLD_FORMAT = (
        "input=$(cat); path=$(printf '%s' \"$input\" | python3 -c \"import json,sys; d=json.load(sys.stdin); "
        "print(d.get('tool_input',{}).get('file_path',''))\" 2>/dev/null); [ -n \"$path\" ] && "
        "CLAUDE_TOOL_FILE_PATH=\"$path\" .agent/scripts/post_edit_format.sh || true"
    )
    OLD_COUNTER = (
        "f=/tmp/cc_err_count.txt; n=$(cat \"$f\" 2>/dev/null || echo 0); n=$((n+1)); echo $n > \"$f\"; "
        "[ $n -ge 2 ] && { echo 0 > \"$f\"; echo '{\"systemMessage\": \"Varios errores consecutivos en Bash. "
        "Considera invocar /debug para un diagnóstico sistemático.\"}'; } || true"
    )
    OLD_STATE = (
        "input=$(cat); echo \"$input\" | grep -q '03_project_state\\.json' && echo '{\"systemMessage\": "
        "\"project_state.json actualizado. Si acabas de cerrar un módulo, ejecuta /cierre-modulo.\"}' || true"
    )

    def test_forma_anterior_se_sustituye_sin_duplicar(self):
        old = {"hooks": {
            "PostToolUseFailure": [{"matcher": "Bash", "hooks": [{"type": "command", "command": self.OLD_COUNTER}]}],
            "PostToolUse": [
                {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": self.OLD_STATE}]},
                {"matcher": "Write|Edit|MultiEdit", "hooks": [{"type": "command", "command": self.OLD_FORMAT}]},
            ],
        }}
        merged, _ = ecs.merge_settings(_base(), old)
        base = _base()
        for event in ("PostToolUseFailure", "PostToolUse"):
            with self.subTest(event=event):
                commands = [h["command"] for g in merged["hooks"][event] for h in g["hooks"]]
                expected = [h["command"] for g in base["hooks"][event] for h in g["hooks"]]
                self.assertEqual(sorted(commands), sorted(expected))


if __name__ == "__main__":
    unittest.main()
