#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contrato de L1 de la 5.3.1: límites duros en Claude Code y Codex.

L1.1 (S3): el guard juzga lo que ejecuta el shell. El código en línea para un
intérprete (`python3 -c`, `python3 - <<EOF`, `node -e`, `ruby -e`, `perl -e`,
`php -r`) se trata como un script en fichero: no se buscan palabras dentro. Con
las condiciones del dictamen `security` del 2026-10-21: la mitigación de spawn
(una orden con argumentos literales que el código lanza se juzga como shell),
la salida del código que llega a un shell se juzga entera, las sustituciones
que expande el shell se siguen juzgando y `wal_checkpoint(TRUNCATE)` nunca
cuenta.

Los casos reales están reducidos a su forma (la estructura que disparaba el
guard), sin el texto privado de los proyectos. El origen de cada uno está en la
bitácora de la 5.3.1 (sección L1). Todas las pruebas ejecutan el hook real por
subproceso, como lo invoca el host.
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_guard_indirect_execution import ROOT, RUNNER, SCRIPTS, hook

sys.path.insert(0, str(SCRIPTS))
import sync_codex_hooks  # noqa: E402

TL = "/Users/ejemplo/Projects/Personales/Example Lab"
SCRATCH = "/private/tmp/claude-501/-proyecto-ejemplo/00000000-0000-0000-0000-000000000000/scratchpad"
RM = "rm" + " -rf"
PUSH_FORCE = "git push" + " --force origin main"
TRUNC = "TRUNC" + "ATE"
DROP = "DROP" + " TABLE"
DEL = "DELETE" + " FROM"

# --- Casos reales: hoy deny o ask; después, pass --------------------------------
REALES_A_PASS = {
    # TL-FW-322 (2026-10-20): heredocs que sustituyen texto en docstrings y docs.
    "TL-FW-322-a docstring de un script": (
        f'cd "{TL}" && python3 - <<\'EOF\'\n'
        "from pathlib import Path\n"
        'p=Path("scripts/axi_select_evaluate.py"); s=p.read_text()\n'
        "rep=[\n"
        '("""Uso (siempre sobre un clon):""",\n'
        f'"""Uso (siempre sobre un clon). Se hace el checkpoint en el clon con PRAGMA wal_checkpoint({TRUNC}):"""),\n'
        "]\n"
        "for a,b in rep:\n"
        "    assert s.count(a)==1,a[:40]; s=s.replace(a,b)\n"
        "p.write_text(s)\n"
        "EOF\n"
        "echo ok"
    ),
    "TL-FW-322-b docs/OPERACIONES.md": (
        f'cd "{TL}" && git rev-parse --short=8 HEAD && python3 - <<\'EOF\'\n'
        "from pathlib import Path\n"
        'p=Path("docs/OPERACIONES.md"); s=p.read_text()\n'
        f"rep=[('nunca sobre la base real.\\n', 'nunca sobre la base real. Se hace `PRAGMA wal_checkpoint({TRUNC})` sobre el clon.\\n')]\n"
        "for a,b in rep:\n"
        "    assert s.count(a)==1,a[:40]; s=s.replace(a,b)\n"
        "p.write_text(s)\n"
        "EOF\n"
        "echo ok"
    ),
    "TL-FW-322-c fila del perfil y field report": (
        f'cd "{TL}" && python3 - <<\'EOF\'\n'
        "from pathlib import Path\n"
        'p=Path("_project/09_domain_profile.md"); lines=p.read_text().split("\\n")\n'
        'i=next(k for k,l in enumerate(lines) if l.startswith("| `task:intraday_development` |"))\n'
        f'row=("| `task:second_wave_safety_prep` | 2026-10-20 | checkpoint del clon con PRAGMA wal_checkpoint({TRUNC}) |")\n'
        "lines.insert(i+1,row); p.write_text(\"\\n\".join(lines))\n"
        'f=Path("_feedback/field_report.md"); s=f.read_text()\n'
        f's+=("\\n## TL-FW-322 — bloquea un heredoc que solo escribe `PRAGMA wal_checkpoint({TRUNC})`\\n"\n'
        f'"**Síntoma:** bloqueadas como «{TRUNC} vacía la tabla».\\n")\n'
        "f.write_text(s)\n"
        "EOF\n"
        "git diff --stat _project/09_domain_profile.md _feedback/field_report.md"
    ),
    # TL-FW-322 (2026-10-21): heredoc a sqlite3 sobre un clon; el lexer partía los paréntesis.
    "TL-FW-322-d heredoc a sqlite3 con wal_checkpoint": (
        f"C={SCRATCH}/clone/ev.sqlite; ls -l $C | awk '{{print $5}}'; sqlite3 $C <<'EOF'\n"
        ".timer on\n"
        "CREATE INDEX IF NOT EXISTS idx_trial_created ON trial(created_utc);\n"
        f"PRAGMA wal_checkpoint({TRUNC});\n"
        "EOF\n"
        "ls -l $C | awk '{print $5}'"
    ),
    # PA-FW-001 (reaparición del 2026-10-17, Codex): verificación y registro del archivo de worktrees.
    "PA-FW-001-17b verificación con subprocess de lectura": (
        "python3 - <<'PY'\n"
        "import json,subprocess,hashlib\n"
        "from pathlib import Path\n"
        "backup=Path(Path('_scratch/archivar/backup-path.txt').read_text().strip());d=json.loads((backup/'manifest.json').read_text())\n"
        "assert all(x.get('removed') and x.get('verified_restore') and not Path(x['worktree']).exists() for x in d['targets'])\n"
        "assert subprocess.check_output(['git','for-each-ref','--format=%(refname) %(objectname)','refs/heads'],text=True)==(backup/'branches-before.txt').read_text()\n"
        "actual=[l[9:] for l in subprocess.check_output(['git','worktree','list','--porcelain'],text=True).splitlines() if l.startswith('worktree ')]\n"
        "print(len(actual))\n"
        "PY"
    ),
    "PA-FW-001-17c registro JSON y Markdown": (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "import json,hashlib\n"
        "backup=Path(Path('_scratch/archivar/backup-path.txt').read_text().strip())\n"
        "lines=[hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(backup)) for p in sorted(backup.rglob('*')) if p.is_file()]\n"
        "(backup/'SHA256SUMS').write_text('\\n'.join(lines)+'\\n')\n"
        "p=Path('_project/26_inventario_ramas_worktrees.md')\n"
        "p.write_text('# Ramas y worktrees\\n\\nRetirada administrativa: worktrees removed tras respaldo verificado.\\n')\n"
        "PY"
    ),
    "PA-FW-001-17a VAR=valor e intérprete del venv": (
        "PYTHONPATH=. .venv/bin/python - <<'PY'\n"
        "from pathlib import Path\n"
        "import json\n"
        "rows=[{'ticker':'X','nota':'removed from watchlist'}]\n"
        "Path('outputs/watchlist_registro.json').write_text(json.dumps(rows))\n"
        "PY"
    ),
    # TL-FW-312 (2026-10-07): prosa sobre `git push` en un fichero del scratchpad.
    "TL-FW-312 prosa sobre git push": (
        "python3 - <<'EOF'\n"
        "from pathlib import Path\n"
        f"p=Path('{SCRATCH}/paquete_comun.md')\n"
        "t=p.read_text()\n"
        "a=t[t.index('7. Sin commits'):t.index('8. Ante una decisión')]\n"
        "t=t.replace(a,'7. No hagas commits ni `git push --delete` ni `git push :rama`: el integrador recoge tu trabajo.\\n')\n"
        "p.write_text(t)\n"
        "EOF"
    ),
    # BIB-FW-001 (2026-10-01, Codex): edición de código JS de la interfaz desde Python.
    "BIB-FW-001 edición de JS con remove()": (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "p=Path('tools/trackid/static/app.js');t=p.read_text()\n"
        "t=t.replace('function cleanup() {}', 'function cleanup() { for (const el of rows) el.remove(); }')\n"
        "p.write_text(t)\n"
        "PY"
    ),
    # TALLO-FW-236 (2026-10-06): el diario y el field report mencionan la palabra.
    "TALLO-FW-236-b diario con la palabra": (
        "python3 - <<'EOF'\n"
        "from pathlib import Path\n"
        'j = Path("_project/13_session_journal.md"); t = j.read_text(encoding="utf-8")\n'
        "def quitar(inicio):\n"
        "    global t\n"
        '    i = t.index(inicio); f = t.index("\\n", i)\n'
        "    t = t[:i] + t[f+1:]\n"
        'quitar("- Fase4 BUG104: completar contratos")\n'
        f"t += '''\\n## TALLO-FW-236 — bloquea `wal_checkpoint({TRUNC})`\\n- Síntoma: «{TRUNC} vacía la tabla».\\n'''\n"
        'j.write_text(t, encoding="utf-8")\n'
        "EOF\n"
        "echo ok"
    ),
    # Misma familia (TL, 2026-10-20): un test que borra filas, editado desde Python.
    "TL 2026-10-20 test con SQL editado desde Python": (
        f'cd "{TL}" && .venv/bin/python - <<\'EOF\'\n'
        "from pathlib import Path\n"
        'p=Path("tests/unit/test_intraday_prereg_register_script.py"); s=p.read_text()\n'
        "new='''def test_x(db):\n"
        "    with sqlite3.connect(db) as conn:\n"
        f"        conn.execute(\"{DEL} backtest_result_catalog\")\n"
        "'''\n"
        "p.write_text(s+new)\n"
        "EOF\n"
        ".venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_intraday_prereg_register_script.py 2>&1 | tail -12 | tee _scratch/prereg/rojo.txt"
    ),
    # Esta misma sesión (L1, 2026-10-21): un `python3 -c` que lista palabras.
    "L1 sesión: python3 -c que lista palabras": (
        f"python3 -I -c \"import re; [print(k) for k in ['{TRUNC}','{DEL}']]\""
    ),
    # TL-FW-317: además del arnés, la palabra «remove» dentro de una cadena.
    "TL-FW-317 VAR=valor, ruta entre comillas y heredoc": (
        f'PYTHONPATH="$PWD/src" "{TL}/.venv/bin/python" - <<\'EOF\'\n'
        "from pathlib import Path\n"
        'p = Path("src/x.py")\n'
        'print(p.read_text().count("remove"))\n'
        "EOF"
    ),
}

# Ya pasaban (5.3.0 L7) y deben seguir pasando.
REALES_YA_PASAN = {
    "TALLO-FW-236-a sqlite3 sobre un respaldo": (
        'set -e; B=backups/tallo_pre_migracion.db\n'
        f'sqlite3 "$B" "pragma wal_checkpoint({TRUNC});" >/dev/null; test ! -s "$B-wal"'
    ),
}


class CasosRealesTests(unittest.TestCase):
    def test_casos_reales_pasan(self) -> None:
        for name, cmd in {**REALES_A_PASS, **REALES_YA_PASAN}.items():
            with self.subTest(name):
                decision, reason = hook(cmd)
                self.assertEqual(decision, "pass", f"{name}: {decision} {reason}")


class S3SigueBloqueandoTests(unittest.TestCase):
    """Lo que S3 no libera: el shell, los clientes de base de datos, la orden que
    lanza el código (mitigación de spawn) y la salida que llega a un shell."""

    DENY = {
        # destructivos del shell y clientes de base de datos
        "rm en el shell": f"{RM} ~",
        "push forzado": PUSH_FORCE,
        "sqlite3 con DROP": f"sqlite3 data.db '{DROP} users'",
        "heredoc a sqlite3 con DELETE": f"sqlite3 data.db <<'EOF'\n{DEL} users;\nEOF",
        "echo a sqlite3": f"echo '{DROP} users;' | sqlite3 data.db",
        "psql -c": f"psql -c '{DROP} users'",
        "docker exec psql": f"docker exec db psql -U app -c '{TRUNC} users'",
        "TRUNCATE junto a wal_checkpoint": f'sqlite3 r.db "PRAGMA wal_checkpoint({TRUNC}); {TRUNC} {DROP[5:]} users;"',
        "bash -c": f"bash -c '{PUSH_FORCE}'",
        # mitigación de spawn: Python
        "os.system": f"python3 -c \"import os; os.system('{RM} /var/data')\"",
        "os.system sobre ~": f"python3 -c \"import os; os.system('{RM} ~')\"",
        "subprocess.run lista": f"python3 -c \"import subprocess; subprocess.run(['rm', '-rf', '/var/data'])\"",
        "subprocess con alias de módulo": f"python3 -c \"import subprocess as sp; sp.call(['git','push','--force','origin','main'])\"",
        "from subprocess import run as r": "python3 -c \"from subprocess import run as r; r(['git','push','--force','origin','main'])\"",
        "shell=True": f"python3 -c \"import subprocess; subprocess.run('{PUSH_FORCE}', shell=True)\"",
        "os.execl con sh -c": f"python3 -c \"import os; os.execl('/bin/sh', 'sh', '-c', '{RM} ~')\"",
        "heredoc con os.system": f"python3 - <<'PY'\nimport os\nos.system('{RM} ~')\nPY",
        "heredoc con Popen": f"python3 - <<'PY'\nfrom subprocess import Popen\nPopen(['git', 'reset', '--hard', 'origin/main']).wait()\nPY",
        # mitigación de spawn: otros intérpretes
        "node execSync": f"node -e \"require('child_process').execSync('{PUSH_FORCE}')\"",
        "node execFileSync": "node -e \"require('child_process').execFileSync('git', ['push', '--force', 'origin', 'main'])\"",
        "node spawnSync": f"node -e \"require('child_process').spawnSync('sh', ['-c', '{RM} ~'])\"",
        "perl system": f"perl -e 'system(\"{RM} ~\")'",
        "perl qx": f"perl -e 'qx{{{RM} ~}}'",
        "ruby comillas invertidas": f"ruby -e '`{RM} ~`'",
        "ruby %x": f"ruby -e '%x({RM} ~)'",
        "ruby IO.popen": f"ruby -e 'IO.popen(\"{PUSH_FORCE}\")'",
        "php shell_exec": f"php -r 'shell_exec(\"{RM} ~\");'",
        # revisión security de L1 (hallazgo 1): Perl y Ruby sin paréntesis
        "perl system sin paréntesis": f"perl -e 'system \"{PUSH_FORCE}\"'",
        "ruby system sin paréntesis": f"ruby -e 'system \"{PUSH_FORCE}\"'",
        "perl exec sin paréntesis": f"perl -e 'exec \"{PUSH_FORCE}\"'",
        "ruby exec sin paréntesis": f"ruby -e 'exec \"{RM} ~\"'",
        "perl system lista": "perl -e 'system \"git\", \"push\", \"--force\"'",
        # hallazgo 2: el exec de child_process sigue contando
        "node child_process.exec": f"node -e \"require('child_process').exec('{PUSH_FORCE}')\"",
        "node exec desestructurado": f"node -e \"const {{exec}}=require('child_process'); exec('{PUSH_FORCE}')\"",
        # la salida del código llega a un shell
        "python -c | sh": f"python3 -c \"print('{RM} ~')\" | sh",
        "heredoc | bash": "python3 - <<'EOF' | bash\nprint('git reset --hard')\nEOF",
        "node -e | sh": f"node -e \"console.log('{RM} ~')\" | sh",
        "salida por tee a sh": f"python3 -c \"print('{RM} ~')\" | tee /tmp/x.log | sh",
        "salida a eval": f"eval \"$(python3 -c \"print('{RM} ~')\")\"",
        "dentro de bash -c": f"bash -c \"python3 -c 'print(1)' && {RM} ~\"",
        # condición (a): las opciones se leen hasta el primer posicional o -m
        "python3 script -c": f"python3 run.py -c \"{PUSH_FORCE}\"",
        "python3 -m mod -c": f"python3 -m tool -c \"{PUSH_FORCE}\"",
        "node app.js -e": f"node app.js -e \"{PUSH_FORCE}\"",
        # condición (c): las sustituciones que expande el shell se juzgan
        "heredoc sin comillas con $(...)": f"python3 - <<EOF\nprint(\"$({RM} ~)\")\nEOF",
        "-c con $(...)": f"python3 -c \"print('$({RM} ~)')\"",
        # condición (d): el DELETE por token sigue en los clientes
        "psql DELETE sin WHERE": f"psql -c \"{DEL} users\"",
    }

    def test_deny(self) -> None:
        for name, cmd in self.DENY.items():
            with self.subTest(name):
                decision, reason = hook(cmd)
                self.assertEqual(decision, "deny", f"{name}: {cmd!r} -> {decision} {reason}")

    def test_el_motivo_de_spawn_lo_dice(self) -> None:
        _, reason = hook(f"python3 -c \"import os; os.system('{RM} /var/data')\"")
        self.assertIn("lanza una orden", reason)

    def test_el_marcador_sigue_funcionando_en_el_guard(self) -> None:
        cmd = f"python3 -c \"import os; os.system('{RM} /var/data')\"  # APPROVED-DESTRUCTIVE: limpieza pedida por el builder"
        self.assertEqual(hook(cmd)[0], "pass")


class S3LiberaTests(unittest.TestCase):
    """Lo que S3 libera a sabiendas (D2): código en línea sin orden lanzada."""

    PASS = {
        "texto destructivo en docs": f"python3 - <<'EOF'\nfrom pathlib import Path\np = Path('docs/x.md')\np.write_text(p.read_text().replace('a', 'nunca {PUSH_FORCE} ni {DROP}'))\nEOF",
        "json con la palabra removed": "python3 -c \"import json; d=json.load(open('_project/x.json')); d['nota']='worktree removed'; print(d)\"",
        "node escribe código con remove()": "node -e \"const fs=require('fs'); fs.writeFileSync('app.js', fs.readFileSync('app.js','utf8').replace('a','el.remove()'))\"",
        "subprocess de lectura": "python3 -c \"import subprocess; print(subprocess.check_output(['git','status']))\"",
        # revisión security de L1 (hallazgo 2): un .exec() de otra librería no lanza procesos
        "node db.exec con SQL": f"node -e \"db.exec('{DROP} x')\"",
        "commit con el texto en la lista": f"python3 -c \"import subprocess; subprocess.run(['git','commit','-m','doc: {DROP} en el ejemplo'])\"",
        "-c sin orden con la palabra": f"python3 -c \"print('{DEL} x')\"",
        "salida a un carrier": f"python3 -c \"print('{RM} ~')\" | tail -5",
        "salida a un fichero por tee": f"python3 -c \"print('{DROP}')\" | tee /tmp/notas.txt",
    }

    def test_pass(self) -> None:
        for name, cmd in self.PASS.items():
            with self.subTest(name):
                decision, reason = hook(cmd)
                self.assertEqual(decision, "pass", f"{name}: {cmd!r} -> {decision} {reason}")


class D3FicheroEscritoYEjecutadoTests(unittest.TestCase):
    """D3: un fichero escrito y ejecutado en el mismo comando es un script en
    fichero solo si todos sus consumidores son intérpretes de la lista."""

    def test_interprete_pasa(self) -> None:
        for runner in ("python3 /tmp/t.py", "/opt/venv/bin/python /tmp/t.py", "pytest /tmp/t.py",
                       "PY=/opt/venv/bin/python; $PY /tmp/t.py"):
            cmd = f"cat > /tmp/t.py <<'EOF'\nSQL = '{DEL} t'\nEOF\n{runner}"
            with self.subTest(runner):
                self.assertEqual(hook(cmd)[0], "pass", cmd)

    def test_shell_cliente_o_mezcla_deniega(self) -> None:
        for runner in ("bash /tmp/t.py", "sh /tmp/t.py", "source /tmp/t.py", ". /tmp/t.py", "/tmp/t.py",
                       "sqlite3 db < /tmp/t.py", "psql -f /tmp/t.py", "xargs -a /tmp/t.py echo",
                       "python3 /tmp/t.py && bash /tmp/t.py", "make -f /tmp/t.py"):
            cmd = f"cat > /tmp/t.py <<'EOF'\n{PUSH_FORCE}\n{DEL} t;\nEOF\n{runner}"
            with self.subTest(runner):
                self.assertEqual(hook(cmd)[0], "deny", cmd)

    def test_variable_no_resoluble_sigue_insegura(self) -> None:
        for prefix in ("if true; then PY=/bin/bash; fi; ", "PY=python3; PY=bash; ", "PY=$(command -v bash); "):
            cmd = f"{prefix}cat > /tmp/t.py <<'EOF'\n{PUSH_FORCE}\nEOF\n$PY /tmp/t.py"
            with self.subTest(prefix):
                self.assertEqual(hook(cmd)[0], "deny", cmd)

    def test_ficheros_que_otro_programa_ejecuta_mantienen_su_clase(self) -> None:
        # setup.py ejecutado por python sigue siendo un flujo inseguro: se juzga el texto.
        self.assertEqual(hook(f"cat > setup.py <<'EOF'\nimport os; os.system('{RM} ~')\nEOF\npython3 setup.py install")[0], "deny")
        # conftest.py que pytest carga sin nombrarlo: clase 3 (ask), como antes.
        self.assertEqual(hook(f"cat > conftest.py <<'EOF'\nimport os; os.system('{RM} ~')\nEOF\npytest -q")[0], "ask")

    def test_sustituciones_del_cuerpo_sin_comillas(self) -> None:
        self.assertEqual(hook(f"cat > /tmp/t.py <<EOF\nX = '$({RM} ~)'\nEOF\npython3 /tmp/t.py")[0], "deny")


class L12FormasDelEjecutorTests(unittest.TestCase):
    """L1.2 (TL-FW-317, D6): el guard admite estas formas; el rechazo real venía
    del aislamiento de worktree de Claude Code para subagentes (FW-365)."""

    PASS = (
        'PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit',
        f'"{TL}/.venv/bin/python" -m pytest -q tests/unit',
        "bash scripts/quality_gate.sh",
        "env PYTHONPATH=src python3 -m pytest -q",
        'cd .claude/worktrees/lane-a && PYTHONPATH="$PWD/src" FRAMEWORK_PYTHON=/x/.venv/bin/python bash scripts/quality_gate.sh',
    )
    DENY = (
        f"FOO=1 {PUSH_FORCE}",
        "env A=1 git reset --hard HEAD~2",
        f'"/usr/bin/git" push --force origin main',
        f"PYTHONPATH=src {RM} ~",
        f"env -i PATH=/usr/bin sh -c '{RM} ~'",
    )

    def test_formas_admitidas(self) -> None:
        for cmd in self.PASS:
            with self.subTest(cmd):
                self.assertEqual(hook(cmd, cwd=TL)[0], "pass")

    def test_controles_siguen_denegados(self) -> None:
        for cmd in self.DENY:
            with self.subTest(cmd):
                self.assertEqual(hook(cmd)[0], "deny")


class L13CodexFallaCerradoTests(unittest.TestCase):
    """L1.3 (FW-363) con las condiciones 3(a)–(e) del dictamen: el hook `guard` de
    Codex deniega cuando no puede llegar a un veredicto; `state`, `format` y
    `stop` se siguen omitiendo."""

    @staticmethod
    def _decision(stdout: str) -> str:
        if not stdout.strip():
            return "pass"
        return json.loads(stdout)["hookSpecificOutput"]["permissionDecision"]

    def _bootstrap(self, hook_id: str, *, cwd: Path, event: str) -> subprocess.CompletedProcess:
        return subprocess.run(sync_codex_hooks._bootstrap_command(hook_id), shell=True, cwd=cwd, input=event,
                              capture_output=True, text=True, timeout=60)

    def _runner(self, hook_id: str, payload, *, cwd: Path, raw: bytes | None = None) -> subprocess.CompletedProcess:
        data = raw if raw is not None else (payload if isinstance(payload, str) else json.dumps(payload)).encode()
        proc = subprocess.run([sys.executable, str(RUNNER), "--hook-id", hook_id], cwd=cwd, input=data,
                              capture_output=True, timeout=60)
        return subprocess.CompletedProcess(proc.args, proc.returncode, proc.stdout.decode(), proc.stderr.decode())

    def test_bootstrap_sin_runner(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            event = json.dumps({"tool_name": "Bash", "tool_input": {"command": PUSH_FORCE}, "cwd": empty})
            guard = self._bootstrap("guard", cwd=Path(empty), event=event)
            self.assertEqual(guard.returncode, 0)
            self.assertEqual(self._decision(guard.stdout), "deny")
            reason = json.loads(guard.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("reinicia Codex en la raíz del proyecto", reason)
            for other in ("state", "format", "stop"):
                with self.subTest(other):
                    proc = self._bootstrap(other, cwd=Path(empty), event=event)
                    self.assertEqual((proc.returncode, proc.stdout), (0, ""))
                    self.assertIn("no se pudo resolver codex_hook_runner.py", proc.stderr)

    def test_bootstrap_con_el_cwd_borrado(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gone = Path(tmp) / "worktree"
            gone.mkdir()
            code = (f"import os,subprocess,sys; os.chdir({str(gone)!r}); os.rmdir({str(gone)!r}); "
                    f"sys.exit(subprocess.run({sync_codex_hooks._bootstrap_command('guard')!r}, shell=True).returncode)")
            proc = subprocess.run([sys.executable, "-c", code], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(self._decision(proc.stdout), "deny", proc.stderr)

    def test_runner_guard_sin_veredicto_deniega(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            cwd = Path(empty)
            good_cwd = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(ROOT)}
            cases = {
                "no es JSON": self._runner("guard", "no es json " + PUSH_FORCE, cwd=cwd),
                "JSON que no es objeto": self._runner("guard", [PUSH_FORCE], cwd=cwd),
                "raíz no resoluble": self._runner("guard", {"tool_name": "Bash", "tool_input": {"command": PUSH_FORCE}, "cwd": empty}, cwd=cwd),
                "sin tool_input.command": self._runner("guard", {"tool_name": "Bash", "tool_input": {"cmd": PUSH_FORCE}, "cwd": str(ROOT)}, cwd=cwd),
                "command que no es cadena": self._runner("guard", {**good_cwd, "tool_input": {"command": ["git", "push"]}}, cwd=cwd),
                "stdin no decodificable": self._runner("guard", None, cwd=cwd, raw=b"\xff\xfe\xfa"),
            }
            for name, proc in cases.items():
                with self.subTest(name):
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(self._decision(proc.stdout), "deny", proc.stdout + proc.stderr)
            root_msg = json.loads(cases["raíz no resoluble"].stdout)["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("reinicia Codex en la raíz del proyecto", root_msg)

    def test_runner_otros_hooks_se_omiten(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            for hook_id in ("state", "format", "stop"):
                with self.subTest(hook_id):
                    proc = self._runner(hook_id, "no es json", cwd=Path(empty))
                    self.assertEqual((proc.returncode, proc.stdout), (0, ""))
                    self.assertIn("se omite de forma segura", proc.stderr)

    def test_un_nombre_de_herramienta_distinto_no_vuelve_pass_el_guard(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            proc = self._runner("guard", {"tool_name": "shell", "tool_input": {"command": PUSH_FORCE}, "cwd": str(ROOT)},
                                cwd=Path(empty))
            self.assertEqual(self._decision(proc.stdout), "deny")

    def test_fallo_del_adaptador_deniega_para_guard(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        import codex_hook_runner as runner
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(runner, "_run_guard", side_effect=RuntimeError("x")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            runner.dispatch("guard", {}, "{}", ROOT)
        self.assertEqual(self._decision(out.getvalue()), "deny")
        out = io.StringIO()
        with mock.patch.object(runner, "_run_stop", side_effect=RuntimeError("x")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            runner.dispatch("stop", {}, "{}", ROOT)
        self.assertEqual(out.getvalue(), "")

    def test_control_con_raiz_valida(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            ok = self._runner("guard", {"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(ROOT)}, cwd=Path(empty))
            self.assertEqual(self._decision(ok.stdout), "pass")
            bad = self._runner("guard", {"tool_name": "Bash", "tool_input": {"command": PUSH_FORCE}, "cwd": str(ROOT)}, cwd=Path(empty))
            self.assertEqual(self._decision(bad.stdout), "deny")


import re  # noqa: E402
import shutil  # noqa: E402

import ensure_claude_settings as ecs  # noqa: E402
import ensure_codex_integration as eci  # noqa: E402
import guard_destructive_command as guard  # noqa: E402

LIMITES = ecs.load_hard_limits(ROOT)


def _claude_rule_matches(rule: str, command: str) -> bool:
    """Aproximación a la semántica documentada de `permissions.deny` de Bash en
    Claude Code (harness_watch.md, 2026-10-21): `*` vale cualquier texto; `X *`
    casa también con `X` a secas; se separan compuestos por `&&`, `||`, `;`, `|`,
    `&` y saltos de línea fuera de comillas, y se ignoran las asignaciones
    iniciales. No sustituye a la prueba en una sesión real (bitácora, L1)."""
    spec = rule[len("Bash("):-1]
    optional_tail = spec.endswith(" *")
    core = spec[:-2] if optional_tail else spec
    pattern = "^" + ".*".join(re.escape(part) for part in core.split("*")) + ("(?: .*)?" if optional_tail else "") + "$"
    parts, buf, quote = [], "", ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            quote = "" if ch == quote else quote
            buf += ch
        elif ch in "'\"":
            quote = ch
            buf += ch
        elif command.startswith(("&&", "||"), i):
            parts.append(buf)
            buf = ""
            i += 1
        elif ch in ";|&\n":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
        i += 1
    parts.append(buf)
    for part in parts:
        part = re.sub(r"^(?:\s*[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*", "", part).strip()
        if part and re.match(pattern, part, re.S):
            return True
    return False


class L14ListaUnicaTests(unittest.TestCase):
    """L1.4: el núcleo irreversible sale de una sola lista; reglas estrechas."""

    def test_ninguna_regla_empieza_por_comodin_ni_queda_vacia(self) -> None:
        for regla in LIMITES["reglas"]:
            for rule in regla["claude"]:
                with self.subTest(rule):
                    self.assertFalse(rule[len("Bash("):].startswith("*"))
            self.assertTrue(regla["debe_bloquear"] and regla["no_debe_bloquear"] and regla["no_casa"], regla["id"])

    def test_claude_bloquea_lo_que_debe_y_nada_mas(self) -> None:
        todas = ecs.claude_deny_rules(ROOT)
        for regla in LIMITES["reglas"]:
            for cmd in regla["debe_bloquear"]:
                with self.subTest(regla=regla["id"], cmd=cmd):
                    self.assertTrue(any(_claude_rule_matches(r, cmd) for r in regla["claude"]), cmd)
            for cmd in regla["no_debe_bloquear"]:
                with self.subTest(regla=regla["id"], no=cmd):
                    self.assertFalse([r for r in todas if _claude_rule_matches(r, cmd)], cmd)

    def test_opciones_seguras_no_casan(self) -> None:
        todas = ecs.claude_deny_rules(ROOT)
        for cmd in ("git push origin --force-if-includes --force-with-lease", "git push origin HEAD:refs/for/main"):
            with self.subTest(cmd):
                self.assertFalse([r for r in todas if _claude_rule_matches(r, cmd)], cmd)

    def test_heredoc_con_el_texto_queda_para_la_sesion_real(self) -> None:
        # Leída al pie de la letra, la documentación separa también por saltos de
        # línea: una línea de un heredoc a `cat` o a `git commit -F -` con el texto
        # casaría. No se puede comprobar sin una sesión real (sin `claude -p`): es
        # la verificación manual V1 de la bitácora de L1. Si casa, la salida es
        # escribir ese texto con Edit o Write.
        todas = ecs.claude_deny_rules(ROOT)
        self.assertTrue([r for r in todas if _claude_rule_matches(r, f"cat <<'EOF' > notas.md\n{PUSH_FORCE}\nEOF")])

    def test_cada_regla_es_igual_o_mas_estrecha_que_el_guard(self) -> None:
        for regla in LIMITES["reglas"]:
            ejemplos = list(regla["debe_bloquear"]) + [m for spec in regla["codex"] for m in spec["match"]]
            for cmd in ejemplos:
                with self.subTest(regla=regla["id"], cmd=cmd):
                    self.assertEqual(guard.judge(cmd)[0], "deny", cmd)

    def test_el_guard_no_promete_el_marcador_para_el_nucleo(self) -> None:
        for cmd in (PUSH_FORCE, "npx prisma db push", f"psql -c '{DROP} users'", "git reset --hard origin/main"):
            with self.subTest(cmd):
                decision, reason = hook(cmd)
                self.assertEqual(decision, "deny")
                self.assertNotIn("APPROVED-DESTRUCTIVE", reason)
                self.assertIn("la ejecuta él", reason)
        for cmd in (f"{RM} ~", "git reset --hard HEAD~1"):
            decision, reason = hook(cmd)
            self.assertIn("APPROVED-DESTRUCTIVE", reason, cmd)


class L14ClaudeSettingsTests(unittest.TestCase):
    def test_plantilla_y_settings_activos_llevan_el_nucleo(self) -> None:
        reglas = ecs.claude_deny_rules(ROOT)
        base = json.loads((ROOT / ".agent/templates/settings.json.base").read_text(encoding="utf-8"))
        activo = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(base["permissions"]["deny"], reglas)
        self.assertTrue(set(reglas) <= set(activo["permissions"]["deny"]))

    def test_sin_regla_edit_sobre_el_estado_d8(self) -> None:
        reglas = ecs.claude_deny_rules(ROOT)
        self.assertFalse([r for r in reglas if r.startswith(("Edit(", "Write(", "Read("))])

    def test_merge_solo_anade_deny_y_respeta_el_resto(self) -> None:
        current = {
            "permissions": {"allow": ["Bash(ls *)"], "ask": ["Bash(git push *)"], "deny": ["Bash(curl *)"],
                            "defaultMode": "acceptEdits", "additionalDirectories": ["../x"]},
            "env": {"A": "1"},
            "hooks": {},
        }
        base = json.loads((ROOT / ".agent/templates/settings.json.base").read_text(encoding="utf-8"))
        merged, changes = ecs.merge_settings(base, current, managed_deny=ecs.claude_deny_rules(ROOT))
        perms = merged["permissions"]
        self.assertEqual(perms["allow"], ["Bash(ls *)"])
        self.assertEqual(perms["ask"], ["Bash(git push *)"])
        self.assertEqual((perms["defaultMode"], perms["additionalDirectories"], merged["env"]), ("acceptEdits", ["../x"], {"A": "1"}))
        self.assertEqual(perms["deny"][0], "Bash(curl *)")
        self.assertEqual(perms["deny"][1:], ecs.claude_deny_rules(ROOT))
        again, changes2 = ecs.merge_settings(base, merged, managed_deny=ecs.claude_deny_rules(ROOT))
        self.assertEqual((again, changes2), (merged, []))

    def test_tipos_inesperados_abortan(self) -> None:
        base = json.loads((ROOT / ".agent/templates/settings.json.base").read_text(encoding="utf-8"))
        for bad in ({"permissions": []}, {"permissions": {"deny": "Bash(x)"}}, {"permissions": {"deny": [1]}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ecs.merge_settings(base, bad, managed_deny=["Bash(git push --force *)"])

    def test_reglas_retiradas_se_quitan(self) -> None:
        base = json.loads((ROOT / ".agent/templates/settings.json.base").read_text(encoding="utf-8"))
        with mock.patch.object(ecs, "_FW_RETIRED_DENY", ("Bash(vieja *)",)):
            merged, changes = ecs.merge_settings(base, {"permissions": {"deny": ["Bash(vieja *)", "Bash(mia *)"]}},
                                                 managed_deny=["Bash(nueva *)"])
        self.assertEqual(merged["permissions"]["deny"], ["Bash(mia *)", "Bash(nueva *)"])
        self.assertTrue(any("retirada" in c for c in changes))


class L14CodexReglasTests(unittest.TestCase):
    def _rules(self) -> list[dict]:
        rules: list[dict] = []
        exec(compile(eci.destructive_rules_text(ROOT), "framework-destructive.rules", "exec"),
             {"prefix_rule": lambda **kw: rules.append(kw)})
        return rules

    def test_forbidden_y_salida_sin_marcador(self) -> None:
        rules = self._rules()
        self.assertTrue(rules)
        for rule in rules:
            self.assertEqual(rule["decision"], "forbidden")
            self.assertIn("las ejecuta él", rule["justification"])
            self.assertNotIn("el marcador el marcador", rule["justification"])
            self.assertNotIn("APPROVED-DESTRUCTIVE", rule["justification"])
            self.assertTrue(rule["match"] and rule["not_match"])

    def test_se_genera_aparte_del_de_lecturas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            changed, _ = eci._ensure_codex_rules(root)
            self.assertTrue(changed)
            eci._write_codex_rules(root)
            self.assertEqual((root / eci.CODEX_DESTRUCTIVE_RULES_REL).read_text(encoding="utf-8"), eci.destructive_rules_text(ROOT))
            self.assertEqual((root / eci.CODEX_RULES_REL).read_text(encoding="utf-8"), eci.CODEX_RULES_TEXT)
            self.assertEqual(eci._ensure_codex_rules(root)[0], False)
            (root / eci.CODEX_DESTRUCTIVE_RULES_REL).write_text("# mío\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                eci._ensure_codex_rules(root)

    @unittest.skipUnless(shutil.which("codex"), "codex no está instalado: no se puede correr execpolicy check")
    def test_codex_execpolicy_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "framework-destructive.rules"
            path.write_text(eci.destructive_rules_text(ROOT), encoding="utf-8")
            for rule in self._rules():
                for example, expected in [(m, "forbidden") for m in rule["match"]] + [(n, None) for n in rule["not_match"]]:
                    with self.subTest(example=example):
                        proc = subprocess.run(["codex", "execpolicy", "check", "--rules", str(path), "--", *example.split()],
                                              capture_output=True, text=True, timeout=60)
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        self.assertEqual(json.loads(proc.stdout).get("decision"), expected, proc.stdout)


import framework_doctor as doctor  # noqa: E402


class L15DoctorAvisosTests(unittest.TestCase):
    """L1.5 (S7, D7, condición 3(f)): avisos del doctor por arnés."""

    def _run(self, root: Path, mode: str, *, env: dict[str, str], which: dict[str, str | None]) -> list[str]:
        warnings: list[str] = []
        with mock.patch.object(doctor, "ROOT", root), mock.patch.dict("os.environ", env), \
                mock.patch.object(doctor.shutil, "which", side_effect=lambda name: which.get(name)):
            doctor.validate_hard_limits_hosts(mode, warnings)
        return warnings

    def test_extensiones_antiguas_de_claude_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as proj:
            ext = Path(tmp)
            for name in ("anthropic.claude-code-2.1.185-darwin-arm64", "anthropic.claude-code-2.1.306-darwin-arm64",
                         "otra.extension-1.0.0"):
                (ext / name).mkdir()
            warnings = self._run(Path(proj), "template", env={"FRAMEWORK_EDITOR_EXTENSION_DIRS": tmp}, which={})
            self.assertEqual(len(warnings), 1, warnings)
            self.assertIn("2.1.185", warnings[0])
            self.assertNotIn("2.1.306", warnings[0])

    def test_opencode_y_gemini_solo_en_initialized(self) -> None:
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as ext:
            root = Path(proj)
            (root / "opencode.json").write_text("{}", encoding="utf-8")
            (root / "GEMINI.md").write_text("x", encoding="utf-8")
            env = {"FRAMEWORK_EDITOR_EXTENSION_DIRS": ext}
            self.assertEqual(self._run(root, "template", env=env, which={}), [])
            warnings = self._run(root, "initialized", env=env, which={})
            self.assertTrue(any("OpenCode" in w for w in warnings) and any("Gemini/Antigravity" in w for w in warnings), warnings)

    def test_codex_instalado_sin_hooks_ni_reglas_ni_confianza(self) -> None:
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as ext, \
                tempfile.TemporaryDirectory() as codex_home:
            root = Path(proj)
            (root / ".agent" / "templates").mkdir(parents=True)
            shutil.copy2(ROOT / ".agent/templates/codex_hooks.json.base", root / ".agent/templates/codex_hooks.json.base")
            (root / ".codex").mkdir()
            (Path(codex_home) / "config.toml").write_text('[projects."/otro"]\ntrust_level = "trusted"\n', encoding="utf-8")
            env = {"FRAMEWORK_EDITOR_EXTENSION_DIRS": ext, "CODEX_HOME": codex_home}
            warnings = self._run(root, "initialized", env=env,
                                 which={"codex": "/bin/false", "python3": sys.executable})
            text = "\n".join(warnings)
            self.assertIn(".codex/hooks.json sin generar", text)
            self.assertIn("reglas de .codex/rules/", text)
            self.assertIn("no es de confianza", text)
            sin_python = self._run(root, "initialized", env=env, which={"codex": None, "python3": None})
            self.assertTrue(any("python3 no está en el PATH" in w for w in sin_python), sin_python)

    def test_codex_configurado_y_confiado_no_avisa(self) -> None:
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as ext, \
                tempfile.TemporaryDirectory() as codex_home:
            root = Path(proj).resolve()
            (root / ".agent" / "templates").mkdir(parents=True)
            shutil.copy2(ROOT / ".agent/templates/codex_hooks.json.base", root / ".agent/templates/codex_hooks.json.base")
            sync_codex_hooks.sync_hooks(root)
            eci._write_codex_rules(root)
            (Path(codex_home) / "config.toml").write_text(
                f'[projects."{root}"]\ntrust_level = "trusted"\n\n'
                f'[hooks.state."{root}/.codex/hooks.json:pre_tool_use:0:0"]\ntrusted_hash = "sha256:x"\n', encoding="utf-8")
            env = {"FRAMEWORK_EDITOR_EXTENSION_DIRS": ext, "CODEX_HOME": codex_home}
            which = {"codex": shutil.which("codex"), "python3": sys.executable}
            warnings = self._run(root, "initialized", env=env, which=which)
            self.assertEqual(warnings, [])
            if which["codex"]:
                # Un ejemplo `match` erróneo deja el fichero sin cargar: el doctor lo dice.
                rules = root / eci.CODEX_DESTRUCTIVE_RULES_REL
                rules.write_text(rules.read_text(encoding="utf-8").replace('"git push -f"', '"git status"', 1), encoding="utf-8")
                broken = self._run(root, "initialized", env=env, which=which)
                self.assertTrue(any("no da forbidden" in w for w in broken), broken)

    def test_plantilla_sin_codex_no_avisa(self) -> None:
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as ext:
            self.assertEqual(self._run(Path(proj), "template", env={"FRAMEWORK_EDITOR_EXTENSION_DIRS": ext},
                                       which={"codex": "/usr/local/bin/codex", "python3": sys.executable}), [])


if __name__ == "__main__":
    unittest.main()
