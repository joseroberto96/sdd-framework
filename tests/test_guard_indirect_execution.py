#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-236 (L8, plan 5.2): el guard distingue tres veredictos — pass, ask, deny.

Todas las pruebas ejecutan el hook real por subproceso (P1): un test que solo
llama a una función no prueba el cableado que ve el host.

- `ask`: ejecución INDIRECTA de algo destructivo (orden en variable, decodificar
  y ejecutar, fichero escrito y ejecutado por otro programa, `python -c`).
- `pass`: cinco falsos positivos que entrenaban a usar el marcador.
- `deny`: lo directo sigue igual; `deny` gana siempre a `ask`.

Nota sobre los datos: los comandos de este fichero son sintéticos. El corpus
real de transcripciones no se guarda en el repositorio (contiene rutas y
posibles credenciales del builder).
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"
GUARD = SCRIPTS / "guard_destructive_command.py"
RUNNER = SCRIPTS / "codex_hook_runner.py"

B64_RM_HOME = "cm0gLXJmIH4="  # base64 de un borrado recursivo de ~


def hook(command: str, cwd: str | None = None) -> tuple[str, str]:
    """(decisión, motivo) tal como lo emite el hook: pass si no hay salida."""
    event = {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": command},
             "hook_event_name": "PreToolUse"}
    if cwd is not None:
        event["cwd"] = cwd
    proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event),
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return ("pass", "")
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    return (out["permissionDecision"], out["permissionDecisionReason"])


class AskIndirectExecutionTests(unittest.TestCase):
    """Cuatro clases que hoy pasan y deben pedir confirmación."""

    ASK = {
        "variable: eval": "c='rm -rf ~'; eval \"$c\"",
        "variable: como orden": "c='rm -rf ~'; $c",
        "variable: bash -c": "c='rm -rf ~'; bash -c \"$c\"",
        "variable: export + sh -c": "export X='rm -rf ~'; sh -c \"$X\"",
        "variable: partida": "a=rm; b='-rf ~'; $a $b",
        "base64 | sh": f"echo {B64_RM_HOME} | base64 -d | sh",
        "base64 --decode | bash": f"echo {B64_RM_HOME} | base64 --decode | bash",
        "bash -c $(base64)": f"bash -c \"$(echo {B64_RM_HOME} | base64 -d)\"",
        "variable desde base64": f"c=$(echo {B64_RM_HOME} | base64 -d); eval \"$c\"",
        "xxd -r | sh": "echo 726d202d7266207e | xxd -r -p | sh",
        "Makefile + make": "printf 'clean:\\n\\trm -rf ~\\n' > Makefile && make clean",
        "Makefile heredoc + make": "cat > Makefile <<'EOF'\nclean:\n\trm -rf ~\nEOF\nmake clean",
        "package.json + npm run": "echo '{\"scripts\":{\"c\":\"rm -rf ~\"}}' > package.json && npm run c",
        "hook de git + commit": "printf '#!/bin/sh\\nrm -rf ~\\n' > .git/hooks/pre-commit && git commit -m x",
        # S3 (5.3.1 L1, D2): la clase 4 (Python que borra) se retiró. `python -c
        # os.system('rm -rf ~')` pasa a deny (spawn); rmtree y os.remove, a pass.
    }

    def test_ask(self) -> None:
        for name, cmd in self.ASK.items():
            with self.subTest(name):
                decision, reason = hook(cmd)
                self.assertEqual(decision, "ask", f"{cmd!r} -> {decision} {reason}")
                self.assertTrue(reason)

    def test_ask_reason_has_no_deny_word(self) -> None:
        # probe_guard.py (línea base) lee "deny" en la salida como bloqueo duro.
        for cmd in self.ASK.values():
            self.assertNotIn("deny", hook(cmd)[1].lower())

    def test_marker_desbloquea_ask(self) -> None:
        decision, _ = hook("c='rm -rf ~'; eval \"$c\"  # APPROVED-DESTRUCTIVE: limpieza pedida por el builder")
        self.assertEqual(decision, "pass")


class DenyStaysDenyTests(unittest.TestCase):
    DENY = [
        "rm -rf ~",
        "c='git reset --hard HEAD~3'; eval \"$c\"",  # ya era deny: el patrón está en la asignación
        "git reset --hard HEAD~3",
        "git push --force origin main",
        "echo ok; rm -rf ~/Projects",
        "psql -c 'DROP TABLE users'",
        "echo 'rm -rf ~' | sed 's/x/y/e'",
        "echo 'rm -rf ~' | sh",
        "printf 'rm -rf ~' > s.sh && bash s.sh",
        # deny gana a ask cuando el mismo comando tiene las dos cosas
        f"echo {B64_RM_HOME} | base64 -d | sh; rm -rf ~",
        "c='rm -rf ~'; eval \"$c\"; git reset --hard",
    ]

    def test_deny(self) -> None:
        for cmd in self.DENY:
            with self.subTest(cmd):
                self.assertEqual(hook(cmd)[0], "deny", cmd)

    def test_rm_relativo_no_se_libera_con_cd(self) -> None:
        for cmd in ("cd ~ && rm -rf y", "pushd ~; rm -rf y", "(cd ~; rm -rf y)", "sh -c 'cd ~; rm -rf y'"):
            with self.subTest(cmd):
                self.assertEqual(hook(cmd, cwd="/private/tmp/x")[0], "deny")

    def test_rm_relativo_fuera_de_tmp_sigue_denegado(self) -> None:
        self.assertEqual(hook("rm -rf s", cwd="/Users/alguien/proyecto")[0], "deny")
        self.assertEqual(hook("rm -rf ../..", cwd="/tmp/a")[0], "deny")
        self.assertEqual(hook("rm -rf .", cwd="/tmp")[0], "deny")
        self.assertEqual(hook("rm -rf s")[0], "deny")  # sin cwd: como hoy
        self.assertEqual(hook("rm -rf s", cwd="relativo/x")[0], "deny")


class FalsePositiveTests(unittest.TestCase):
    """Los cinco falsos positivos de FW-236 y el caso visto en la sesión."""

    PASS = {
        "grep | sed -n": "grep -rn 'rm -rf' docs/ | sed -n 1,5p",
        "grep | sed s///": "grep -rn 'rm -rf' docs/ | sed 's/rm/RM/g'",
        "heredoc de datos por ssh": "ssh host 'cat > /tmp/notas.txt' <<'EOF'\nno hagas rm -rf ~\nEOF",
        "shellcheck de script escrito": "printf 'rm -rf ~\\n' > s.sh && shellcheck s.sh",
        "python3 - con literal": "python3 - <<'PY'\nimport json\nprint(json.dumps({'nota': 'no hagas rm -rf ~'}))\nPY",
        "rm -rf relativo, cwd en scratchpad": "rm -rf s",
        # visto en esta misma sesión: un heredoc que solo CONTENÍA el texto
        "heredoc que solo contiene el texto": "cat <<'EOF' > plan.md\nel guard bloquea rm -rf ~ y git push --force\nEOF",
        "heredoc + build (sin fichero ejecutable)": "cat > docs.md <<'EOF'\nno uses rm -rf ~\nEOF\nnpm run build",
        # controles benignos de las clases ask
        "variable benigna": "c='ls -la'; eval \"$c\"",
        "base64 sin ejecutar": "base64 -d < a.b64 > a.bin && file a.bin",
        "base64 | jq": "echo eyJhIjoxfQ== | base64 -d | jq .",
        "Makefile benigno + make": "printf 'all:\\n\\techo hola\\n' > Makefile && make",
        "python rmtree efímero": "python3 -c \"import shutil; shutil.rmtree('/tmp/x')\"",
        "python subprocess benigno": "python3 -c \"import subprocess; print(subprocess.check_output(['git','status']))\"",
        "python sin llamadas destructivas": "python3 -c \"print(1+1)\"",
    }

    def test_pass(self) -> None:
        for name, cmd in self.PASS.items():
            with self.subTest(name):
                cwd = "/private/tmp/claude-501/scratchpad" if name.startswith("rm -rf relativo") else None
                decision, reason = hook(cmd, cwd=cwd)
                self.assertEqual(decision, "pass", f"{cmd!r} -> {decision} {reason}")

    def test_sed_peligroso_no_es_lector(self) -> None:
        for cmd in ("echo 'rm -rf ~' | sed -e 's/a/b/e'", "echo 'rm -rf ~' | sed 'e'",
                    "echo 'rm -rf ~' | sed -i p f", "echo 'rm -rf ~' | sed 'w out'",
                    "echo 'rm -rf ~' | sed -f script.sed"):
            with self.subTest(cmd):
                self.assertEqual(hook(cmd)[0], "deny")

    def test_ssh_con_shell_remoto_no_es_dato(self) -> None:
        for cmd in ("ssh host bash <<'EOF'\nrm -rf ~\nEOF", "ssh host <<'EOF'\nrm -rf ~\nEOF",
                    "ssh host 'cat; rm -rf ~' <<'EOF'\nx\nEOF"):
            with self.subTest(cmd):
                self.assertEqual(hook(cmd)[0], "deny")

    def test_python_con_llamadas_no_es_dato(self) -> None:
        # S3 (5.3.1 L1, D2): la orden lanzada se juzga (spawn); `exec` de una cadena
        # que no lanza nada es un hueco aceptado.
        self.assertEqual(hook("python3 - <<'PY'\nimport os\nos.system('rm -rf ~')\nPY")[0], "deny")
        self.assertEqual(hook("python3 - <<'PY'\nexec('import os')\nprint('rm -rf ~')\nPY")[0], "pass")

    def test_shellcheck_no_libera_ejecutar(self) -> None:
        self.assertEqual(hook("printf 'rm -rf ~\\n' > s.sh && shellcheck s.sh && bash s.sh")[0], "deny")


class AdversarialRelaxationTests(unittest.TestCase):
    """Ataques a las cinco relajaciones (autorrevisión de L8). Ninguno puede pasar:
    solo `deny` o `ask`. Cada uno es una forma que un predicado de «dato» basado
    en listas de palabras dejaría pasar."""

    PYTHON_ESCAPES = {
        "import partido con continuación": "import \\\nos\nos.system('rm -rf ~')",
        "reexport: dataclasses.inspect.os": "import dataclasses\ndataclasses.inspect.os.system('rm -rf ~')",
        "reexport: random._os": "import random\nrandom._os.system('rm -rf ~')",
        "exec con letras de ancho completo": "ｅｘｅｃ(\"import os; os.system('rm -rf ~')\")",
        "f-string con expresión": "import json\nprint(f\"{__import__('os').system('rm -rf ~')}\")",
        "import tras dos puntos": "if 1: import os\nos.system('rm -rf ~')",
        "from json import codecs": "from json import codecs\ncodecs.open('f', 'w')",
        "sys.modules": "import sys\nsys.modules['os'].system('rm -rf ~')",
        "__builtins__": "print(__builtins__)\n__builtins__.exec('x')",
        "atributo fuera de la lista": "import json\nprint(json.decoder.re.sre_compile)",
        "open": "import json\nopen('/etc/hosts', 'w').write('x')",
    }

    # Los que el guard publicado (4.40) ya bloqueaba: no pueden dejar de hacerlo.
    # El resto (f-string, from json import codecs, __builtins__, atributo fuera de
    # lista, open) pasaba también antes: el guard no lee la semántica de Python,
    # y aquí solo se exige que NO se los trate como dato.
    OLD_DENIED = ("import partido con continuación", "reexport: dataclasses.inspect.os",
                  "reexport: random._os", "import tras dos puntos", "sys.modules")

    def test_python_ya_bloqueado_sigue_bloqueado(self) -> None:
        for name in self.OLD_DENIED:
            with self.subTest(name):
                decision, _ = hook(f"python3 - <<'PY'\n{self.PYTHON_ESCAPES[name]}\nPY")
                self.assertIn(decision, ("deny", "ask"), name)

    def test_exec_de_ancho_completo_es_un_hueco_aceptado(self) -> None:
        # S3 (5.3.1 L1, D2): la orden va dentro de una cadena que ejecuta `exec`;
        # el dictamen `security` lo acepta como argumento dinámico.
        code = self.PYTHON_ESCAPES["exec con letras de ancho completo"]
        self.assertEqual(hook(f"python3 - <<'PY'\n{code}\nPY")[0], "pass")

    def test_rm_relativo_solo_cuando_rm_es_la_orden(self) -> None:
        cwd = "/private/tmp/claude-501/x/scratchpad"
        for cmd in ("find ~ -exec rm -rf {} +", "ssh host rm -rf y", "docker exec c rm -rf y",
                    "env -C ~ rm -rf y", "sudo rm -rf y", "rm -rf s ~", "xargs rm -rf y < lista"):
            with self.subTest(cmd):
                self.assertEqual(hook(cmd, cwd=cwd)[0], "deny")

    def test_rm_relativo_no_sigue_un_enlace_hacia_fuera_de_tmp(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "enlace"
            link.symlink_to(Path.home())
            self.assertEqual(hook("rm -rf s", cwd=str(link))[0], "deny")

    def test_ssh_con_expansion_local_sigue_denegado(self) -> None:
        self.assertEqual(hook("ssh host 'cat > f' <<EOF\n$(rm -rf ~)\nEOF")[0], "deny")

    def test_sed_con_ejecucion_no_es_lector(self) -> None:
        for cmd in ("echo 'rm -rf ~' | sed 'e'", "echo 'rm -rf ~' | sed -n '1p;e'",
                    "echo 'rm -rf ~' | sed --expression='s/a/b/e'", "echo 'rm -rf ~' | sed 's|a|b|e'",
                    "echo 'rm -rf ~' | sed -n p | sh"):
            with self.subTest(cmd):
                self.assertEqual(hook(cmd)[0], "deny")


class SecurityReviewRegressionTests(unittest.TestCase):
    """Hallazgos de la revisión `security` de L8 (todos eran deny→pass respecto al
    guard publicado, reproducidos antes de corregir)."""

    TMP = "/tmp/l8_review"

    def test_cd_ofuscado_con_comillas_o_escapes(self) -> None:
        for cmd in ("c''d ~ && rm -rf Documents", "c\\d /Users && rm -rf Shared",
                    "p''ushd ~ >/dev/null; rm -rf Documents", "c\"\"d ~; rm -rf Documents"):
            with self.subTest(cmd):
                self.assertEqual(hook(cmd, cwd=self.TMP)[0], "deny")

    def test_enlace_simbolico_dentro_del_destino(self) -> None:
        import tempfile
        victim = tempfile.mkdtemp(dir=Path(__file__).resolve().parent, prefix=".l8_victim_")
        try:
            (Path(victim) / "sub").mkdir()
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "lnk").symlink_to(victim)
                self.assertEqual(hook("rm -rf lnk/sub", cwd=tmp)[0], "deny")
                self.assertEqual(hook("rm -rf lnk", cwd=tmp)[0], "deny")
        finally:
            import shutil
            shutil.rmtree(victim, ignore_errors=True)

    def test_punto_y_punto_punto(self) -> None:
        self.assertEqual(hook("rm -rf .", cwd=self.TMP)[0], "deny")
        self.assertEqual(hook("rm -rf ..", cwd="/private/var/folders/ab/cd/T/sub")[0], "deny")
        self.assertEqual(hook("rm -rf ./", cwd=self.TMP)[0], "deny")

    def test_ssh_con_opciones_que_ejecutan(self) -> None:
        body = "<<'EOF'\nrm -rf ~\nEOF"
        for opts in ("-o PermitLocalCommand=yes -o LocalCommand=sh", "-F /tmp/cfg", "-J salto", "-S /tmp/sock",
                     "-oProxyCommand=sh", "-W h:1", "-L 1:h:2"):
            with self.subTest(opts):
                self.assertEqual(hook(f"ssh {opts} host cat {body}")[0], "deny")

    def test_ssh_escritura_remota_persistente(self) -> None:
        for path in ("/etc/cron.d/x", "~/.bashrc", ".ssh/authorized_keys", "/etc/profile.d/x.sh", "notas.txt",
                     "/tmp/../etc/x"):
            with self.subTest(path):
                cmd = f"ssh prod 'cat > {path}' <<'EOF'\n* * * * * root rm -rf /srv\nEOF"
                self.assertEqual(hook(cmd)[0], "deny")
        self.assertEqual(hook("ssh -o BatchMode=yes -p 22 prod 'tee /var/tmp/n.txt' <<'EOF'\nno hagas rm -rf ~\nEOF")[0], "pass")

    def test_ssh_palabras_con_guion_solo_flags_simples(self) -> None:
        for word in ('"-$(sh)"', '"-`sh`"', "-$IFS", "-x=1", "--a_b"):
            with self.subTest(word):
                cmd = f"ssh host 'cat {word}' <<'EOF'\nrm -rf ~\nEOF"
                self.assertEqual(hook(cmd)[0], "deny")
        self.assertEqual(hook("ssh host 'cat -n /tmp/f' <<'EOF'\nno hagas rm -rf ~\nEOF")[0], "pass")

    def test_dos_puntos_tras_un_enlace_simbolico(self) -> None:
        import tempfile
        victim = tempfile.mkdtemp(dir=Path(__file__).resolve().parent, prefix=".l8_victim_")
        try:
            (Path(victim) / "sub").mkdir()
            (Path(victim) / "foo").mkdir()
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "lnk").symlink_to(Path(victim) / "sub")
                self.assertEqual(hook("rm -rf lnk/../foo", cwd=tmp)[0], "deny")
                self.assertEqual(hook("rm -rf a/../b", cwd=tmp)[0], "deny")
        finally:
            import shutil
            shutil.rmtree(victim, ignore_errors=True)

    def test_cd_partido_con_continuacion_de_linea(self) -> None:
        self.assertEqual(hook("c\\\nd ~ && rm -rf Documents", cwd=self.TMP)[0], "deny")
        self.assertEqual(hook("p\\\nushd ~; rm -rf Documents", cwd=self.TMP)[0], "deny")

    def test_shellcheck_escrito_por_el_propio_comando(self) -> None:
        self.assertEqual(hook("printf 'rm -rf ~' > shellcheck; chmod +x shellcheck; ./shellcheck")[0], "deny")


class Fw236CorpusTests(unittest.TestCase):
    """Los 29 casos de FW-236. La lista original de la 4.40 no se conservó: este
    corpus se reconstruyó a partir de la descripción del field report (11 que
    pasaban y deben preguntar, 4 que bloqueaban de más, 8 ya correctos y 6
    variantes) y del falso positivo del rm relativo con cwd temporal."""

    SCRATCH = "/private/tmp/claude-501/x/scratchpad"
    CASES = [
        # (esperado, comando, cwd)
        ("ask", "c='rm -rf ~'; eval \"$c\"", None),
        ("ask", "c='rm -rf ~'; $c", None),
        ("ask", "c='rm -rf ~'; bash -c \"$c\"", None),
        ("ask", "export X='rm -rf ~'; sh -c \"$X\"", None),
        ("ask", "a=rm; b='-rf ~'; $a $b", None),
        ("ask", f"echo {B64_RM_HOME} | base64 -d | sh", None),
        ("ask", f"bash -c \"$(echo {B64_RM_HOME} | base64 -d)\"", None),
        ("ask", "printf 'clean:\\n\\trm -rf ~\\n' > Makefile && make clean", None),
        ("ask", "echo '{\"scripts\":{\"c\":\"rm -rf ~\"}}' > package.json && npm run c", None),
        ("ask", "printf '#!/bin/sh\\nrm -rf ~\\n' > .git/hooks/pre-commit && git commit -m x", None),
        ("pass", "python3 -c \"import shutil,os; shutil.rmtree(os.path.expanduser('~'))\"", None),  # S3, D2
        ("pass", "ssh host 'cat > /tmp/n.txt' <<'EOF'\nno hagas rm -rf ~\nEOF", None),
        ("pass", "grep -rn 'rm -rf' docs/ | sed -n 1,5p", None),
        ("pass", "printf 'rm -rf ~\\n' > s.sh && shellcheck s.sh", None),
        ("pass", "python3 - <<'PY'\nprint('rm -rf ~')\nPY", None),
        ("deny", "f() { rm -rf ~; }; f", None),
        ("deny", "printf 'rm -rf ~' | sh", None),
        ("deny", "echo ~ | xargs rm -rf", None),
        ("deny", "find ~ -delete", None),
        ("deny", "find ~ -exec rm -rf {} +", None),
        ("deny", "echo 'rm -rf ~' | sed 's/x/y/e'", None),
        ("deny", "echo 'rm -rf ~' | awk '{system($0)}'", None),
        ("pass", "cat <<\"it's\"\nrm -rf ~\nit's", None),
        ("deny", "cmd='psql -c \"DROP TABLE users\"'; eval \"$cmd\"", None),
        ("ask", f"c=$(echo {B64_RM_HOME} | base64 -d); eval \"$c\"", None),
        ("ask", "echo 726d202d7266207e | xxd -r -p | sh", None),
        ("ask", "cat > Makefile <<'EOF'\nclean:\n\trm -rf ~\nEOF\nmake clean", None),
        ("pass", "python3 - <<'PY'\nimport shutil, os\nshutil.rmtree(os.path.expanduser('~/Projects'))\nPY", None),  # S3, D2
        ("pass", "rm -rf s", SCRATCH),
    ]

    def test_son_29(self) -> None:
        self.assertEqual(len(self.CASES), 29)

    def test_corpus(self) -> None:
        for expected, cmd, cwd in self.CASES:
            with self.subTest(cmd[:60]):
                self.assertEqual(hook(cmd, cwd)[0], expected, cmd)


class CodexTranslatesAskTests(unittest.TestCase):
    """Codex no admite `ask` en PreToolUse: lo ignora y sigue (fail-open). El
    runner debe traducirlo a deny con el mismo motivo (D4)."""

    def _run(self, command: str) -> dict:
        event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(ROOT),
                 "hook_event_name": "PreToolUse"}
        proc = subprocess.run([sys.executable, str(RUNNER), "--hook-id", "guard"],
                              input=json.dumps(event), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)["hookSpecificOutput"] if proc.stdout.strip() else {}

    def test_ask_se_traduce_a_deny(self) -> None:
        out = self._run("c='rm -rf ~'; eval \"$c\"")
        self.assertEqual(out.get("permissionDecision"), "deny")
        self.assertIn("orden", out.get("permissionDecisionReason", ""))
        self.assertIn("Codex", out.get("permissionDecisionReason", ""))

    def test_guard_que_no_se_puede_ejecutar_deniega(self) -> None:
        """Revisión security de L8 (preexistente): OSError al lanzar el guard dejaba pasar."""
        import io
        import contextlib
        from unittest import mock
        sys.path.insert(0, str(SCRIPTS))
        import codex_hook_runner as runner
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(runner.subprocess, "run", side_effect=PermissionError("x")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            runner._run_guard(ROOT, "{}")
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_deny_directo_intacto_y_pass_sin_salida(self) -> None:
        self.assertEqual(self._run("rm -rf ~").get("permissionDecision"), "deny")
        self.assertEqual(self._run("git status"), {})


if __name__ == "__main__":
    unittest.main()
