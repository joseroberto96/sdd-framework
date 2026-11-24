#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contrato de L7 (FW-338, FW-339, FW-301): guard y escáner de secretos sin falsos positivos.

Cubre los 8 puntos del lote L7, el dictamen del subagente `security` (`strong`, xhigh)
y los 5 ajustes de endurecimiento del builder:

1. Texto frente a comando (`guard_destructive_command.py`): lista cerrada AST de
   lo permitido (`_python_is_data_only`, escritura de texto en destinos literales
   seguros bajo `docs/`, `tests/` sin `conftest.py` o temporal con ruta real dentro
   de esos árboles, y `sqlite3` efímero sin `ATTACH`, `load_extension` ni `VACUUM INTO`).
   Todo lo demás conserva su veredicto (`deny` o `ask`).
2. `wal_checkpoint(TRUNCATE)` (`FP-G03`, `FP-G05`): permitido como argumento de
   `wal_checkpoint(...)`, bloqueado en posición de sentencia SQL.
3. Scratchpad de la sesión y variables (`FP-G02`): variable con asignación única
   a nivel superior fuera de condicionales (literal temporal o `$(mktemp -d)` sin
   plantilla de ruta, sin `-p` y sin `TMPDIR` asignado en el comando) y sufijo de
   bucle sin `/` ni `..`.
4. Mensaje del guard: distingue «comando» de «texto que parece un comando» y
   propone la herramienta de edición de ficheros (`Edit` / `Write`).
5. El proceso del hook no llega a ejecutarse: `"onFailure": "block"` en
   `settings.json.base` y `.claude/settings.json`, preservado en el merge de
   `ensure_claude_settings.py`, comprobado por `framework_doctor.py` junto con el
   arranque en seco del guard y el aviso D19 si Claude Code `< 2.1.295`.
6. Huellas declaradas (`FP-S02`, `FP-S03`, `FP-S04`): marcadores con caso en el
   corpus (`sha256`, `SHA-256`, `checksum`, `huella`, `fingerprint` — `commit` queda
   fuera para 64 hex) en la misma línea o en cabecera de tabla Markdown ligada por
   índice de columna con el mismo número de celdas. Error imprime la línea y los
   marcadores aceptados.
7. Identificadores con prefijo (`FP-S01`): `identity_prefixes` en
   `15_environment_profile.md` acotado (3–16 chars `[a-z][a-z0-9]{2,15}`, al menos
   una letra `[g-z]`, sin prefijos de credencial prohibidos, forma exacta
   `<prefijo>-<hex>`).
8. Mismo resultado dentro y fuera del commit (`FP-S03`): el doctor escanea también
   los ficheros sin versionar no ignorados (`git ls-files --others --exclude-standard`)
   tanto con `staged=False` como con `staged=True`.

Condición 1: cada caso adversario del guard y del escáner conserva su veredicto
exacto (ningún `deny` baja a `ask` ni a `pass`, ningún `ask` baja a `pass`).

Estos tests los escribe el orquestador/revisor. El ejecutor no los modifica.

Cambiados en 5.3.1 L2 con la aprobación D2 del builder (regla de secretos
invertida: un hexadecimal suelto no es un secreto): en el punto 6, el hex de 64
con «commit», la prosa con hex de 32 y 40, la tabla con hex en otra columna o
descuadrada y el mensaje de error; el punto 7 se retira (D10); en el punto 8 el
secreto pasa a ser una credencial con formato y, dentro del commit, solo se
escanea lo preparado (D6).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import ensure_claude_settings as ecs  # noqa: E402
import framework_doctor as doctor  # noqa: E402
import gate_calibration as gc  # noqa: E402
import guard_destructive_command as guard  # noqa: E402
import test_doctor_secret_scan as tdss  # noqa: E402
import test_false_positive_corpus as fpc  # noqa: E402
import test_field_harvest_2026_07_18 as tfh718  # noqa: E402
import test_field_harvest_2026_09_14 as tfh914  # noqa: E402
import test_guard_destructive_command as tgdc  # noqa: E402
import test_guard_indirect_execution as tgie  # noqa: E402
import test_guard_rutas_temporales as tgrt  # noqa: E402

RM = "rm" + " -rf"
DROP_USERS = "DROP" + " TABLE users"
TRUNC_USERS = "TRUNCATE" + " TABLE users"
DEL_USERS = "DELETE" + " FROM users"
GIT_PUSH_DEL = "git push" + " --delete origin feature"
GIT_RESET_HARD = "git reset" + " --hard"
SCRATCH = "/private/tmp/claude-501/-proyecto-ejemplo/00000000-0000-0000-0000-000000000000/scratchpad"


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _hex40(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]


def _hex32(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _git(root: Path, *args: str) -> None:
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(["git", "-C", str(root), *args], check=True, env=env, capture_output=True)


def _init_git_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")


class CorpusFalsosPositivosL17Tests(unittest.TestCase):
    """Los 10 casos reales de `tests/fixtures/false_positives/` (L1.7) deben pasar."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = json.loads((fpc.CORPUS / "index.json").read_text(encoding="utf-8"))
        cls.by_id = {c["id"]: c for c in cls.index["casos"]}

    def _guard_case(self, cid: str) -> tuple[str, str]:
        cmd = (fpc.CORPUS / self.by_id[cid]["fichero"]).read_text(encoding="utf-8")
        return guard.judge(cmd, str(ROOT))

    def _scan_case(self, cid: str, *, profile_lines: str = "", staged: bool = False, git_add: bool = True) -> list[str]:
        text = fpc.materializar(self.by_id[cid])
        with tempfile.TemporaryDirectory() as tmp:
            sb = Path(tmp)
            _init_git_repo(sb)
            if profile_lines:
                (sb / "_project").mkdir(parents=True, exist_ok=True)
                (sb / "_project" / "15_environment_profile.md").write_text(profile_lines, encoding="utf-8")
                _git(sb, "add", "_project/15_environment_profile.md")
            target = sb / ("tests/test_id.py" if cid == "FP-S01" else "_project/doc_evidencia.md")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            if git_add:
                _git(sb, "add", str(target.relative_to(sb)))
            orig = doctor.ROOT
            doctor.ROOT = sb
            try:
                errors: list[str] = []
                doctor.scan_potential_secrets(errors, staged=staged)
                return errors
            finally:
                doctor.ROOT = orig

    def test_fp_g01_sql_en_heredoc_de_escritura_en_tests_pasa(self) -> None:
        verdict, reason = self._guard_case("FP-G01")
        self.assertEqual(verdict, "pass", f"FP-G01 debía pasar: {verdict} ({reason})")

    def test_fp_g02_borrar_clones_en_scratchpad_con_variable_y_for_pasa(self) -> None:
        verdict, reason = self._guard_case("FP-G02")
        self.assertEqual(verdict, "pass", f"FP-G02 debía pasar: {verdict} ({reason})")

    def test_fp_g03_wal_checkpoint_truncate_en_python_sqlite_temporal_pasa(self) -> None:
        verdict, reason = self._guard_case("FP-G03")
        self.assertEqual(verdict, "pass", f"FP-G03 debía pasar: {verdict} ({reason})")

    def test_fp_g04_prosa_con_git_push_delete_en_escritura_scratchpad_pasa(self) -> None:
        verdict, reason = self._guard_case("FP-G04")
        self.assertEqual(verdict, "pass", f"FP-G04 debía pasar: {verdict} ({reason})")

    def test_fp_g05_sqlite3_wal_checkpoint_truncate_sobre_respaldo_pasa(self) -> None:
        verdict, reason = self._guard_case("FP-G05")
        self.assertEqual(verdict, "pass", f"FP-G05 debía pasar: {verdict} ({reason})")

    def test_fp_g06_edicion_markdown_en_docs_con_terminos_en_cadena_pasa(self) -> None:
        verdict, reason = self._guard_case("FP-G06")
        self.assertEqual(verdict, "pass", f"FP-G06 debía pasar: {verdict} ({reason})")

    def test_fp_s01_vector_dorado_con_prefijo_declarado_en_perfil_pasa(self) -> None:
        errors = self._scan_case("FP-S01", profile_lines="- `identity_prefixes`: lbx\n")
        self.assertEqual(errors, [], f"FP-S01 con lbx declarado debía pasar: {errors}")

    def test_fp_s02_huella_en_tabla_de_inventario_documental_pasa(self) -> None:
        errors = self._scan_case("FP-S02")
        self.assertEqual(errors, [], f"FP-S02 debía pasar por cabecera de columna Huella: {errors}")

    def test_fp_s03_huella_declarada_en_documento_nuevo_sin_versionar_pasa(self) -> None:
        errors_unstaged = self._scan_case("FP-S03", staged=False, git_add=False)
        errors_staged = self._scan_case("FP-S03", staged=True, git_add=True)
        self.assertEqual(errors_unstaged, [], f"FP-S03 fuera del commit debía pasar: {errors_unstaged}")
        self.assertEqual(errors_staged, [], f"FP-S03 dentro del commit debía pasar: {errors_staged}")

    def test_fp_s04_sha256_del_documento_final_en_la_misma_linea_pasa(self) -> None:
        errors = self._scan_case("FP-S04")
        self.assertEqual(errors, [], f"FP-S04 debía pasar: {errors}")


class Punto1TextoFrenteAComandoTests(unittest.TestCase):
    """Punto 1 + Condición 2 + Ajuste 4: lista cerrada de lo permitido en heredoc Python
    de escritura de texto; todo lo demás bloquea con su veredicto exacto."""

    def test_escritura_en_docs_md_txt_rst_con_texto_destructivo_pasa(self) -> None:
        for ext in ("md", "txt", "rst"):
            cmd = (
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                f"Path('docs/nota.{ext}').write_text('No ejecutar {RM} ~ ni {DROP_USERS}')\n"
                "PY"
            )
            with self.subTest(ext=ext):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "pass")

    def test_escritura_en_tests_permitida_salvo_conftest(self) -> None:
        ok_cmd = (
            "python3 - <<'PY'\n"
            "from pathlib import Path\n"
            f"Path('tests/unit/test_sql.py').write_text('con.execute(\"{DROP_USERS}\")')\n"
            "PY"
        )
        self.assertEqual(guard.judge(ok_cmd, str(ROOT))[0], "pass")
        for forbidden in ("tests/conftest.py", "tests/unit/conftest.py", "conftest.py"):
            bad_cmd = (
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                f"Path('{forbidden}').write_text('con.execute(\"{DROP_USERS}\")')\n"
                "PY"
            )
            with self.subTest(forbidden=forbidden):
                # S3 (5.3.1 L1, D2): el código de un heredoc a Python es un script en
                # fichero; escribir `conftest.py` desde él equivale a hacerlo con Write.
                self.assertEqual(guard.judge(bad_cmd, str(ROOT))[0], "pass")

    def test_s3_texto_escrito_desde_python_y_ejecutado_pasa(self) -> None:
        """S3 (5.3.1 L1, D2): el guard ya no lee lo que escribe un programa Python."""
        for runner in (
            "pytest tests/test_ejemplo.py",
            "python3 -m unittest tests/test_ejemplo.py",
            "python3 tests/test_ejemplo.py",
            "bash tests/test_ejemplo.py",
        ):
            cmd = (
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                f"Path('tests/test_ejemplo.py').write_text('con.execute(\"{DROP_USERS}\")')\n"
                "PY\n"
                f"{runner}"
            )
            with self.subTest(runner=runner):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "pass")

    def test_s3_destino_que_se_ejecuta_despues_escrito_desde_python_pasa(self) -> None:
        """S3 (5.3.1 L1, D2): igual que con Write; el hook o el rc no los ve el guard."""
        for dest in (
            ".git/hooks/pre-commit",
            ".husky/pre-commit",
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".bashrc",
            ".zshrc",
            ".profile",
            "Makefile",
            "package.json",
        ):
            cmd = (
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                f"Path('{dest}').write_text('{RM} ~')\n"
                "PY"
            )
            with self.subTest(dest=dest):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "pass")

    def test_s3_texto_en_fichero_de_producto_pasa(self) -> None:
        """S3 (5.3.1 L1, D2): el falso positivo de TL-FW-322 y PA-FW-001."""
        for dest in (
            "src/app.py",
            "app/main.py",
            ".agent/scripts/state_core.py",
            "docs/script.py",
            "docs/run.sh",
            "tests/run.sh",
        ):
            cmd = (
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                f"Path('{dest}').write_text('{DROP_USERS}')\n"
                "PY"
            )
            with self.subTest(dest=dest):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "pass")

    def test_s3_enlace_simbolico_escrito_desde_python_pasa(self) -> None:
        """S3 (5.3.1 L1, D2): el destino de lo que escribe Python ya no se analiza."""
        with tempfile.TemporaryDirectory() as proj_tmp, tempfile.TemporaryDirectory() as outside_tmp:
            proj = Path(proj_tmp)
            (proj / "docs").mkdir()
            (proj / "tests").mkdir()
            outside_file = Path(outside_tmp) / "secreto.md"
            outside_file.write_text("original", encoding="utf-8")
            (proj / "docs" / "escape.md").symlink_to(outside_file)
            (proj / "tests" / "test_escape.py").symlink_to(outside_file)

            for rel_target in ("docs/escape.md", "tests/test_escape.py"):
                cmd = (
                    "python3 - <<'PY'\n"
                    "from pathlib import Path\n"
                    f"Path('{rel_target}').write_text('{DROP_USERS}')\n"
                    "PY"
                )
                with self.subTest(rel_target=rel_target):
                    self.assertEqual(guard.judge(cmd, str(proj))[0], "pass")

    def test_s3_cd_anterior_ya_no_cambia_el_veredicto_del_codigo_python(self) -> None:
        """S3 (5.3.1 L1, D2): sin análisis de destinos, el cd no influye."""
        for prefix in ("cd /tmp && ", "cd .. && ", "pushd /tmp && "):
            cmd = (
                f"{prefix}python3 - <<'PY'\n"
                "from pathlib import Path\n"
                f"Path('docs/diseno.md').write_text('{RM} ~')\n"
                "PY"
            )
            with self.subTest(prefix=prefix):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "pass")

    def test_contrario_exec_eval_popen_importlib_getattr_bloquean(self) -> None:
        snippets = {
            "exec": f"exec(\"Path('docs/a.md').write_text('{DROP_USERS}')\")",
            "eval": f"eval(\"Path('docs/a.md').write_text('{DROP_USERS}')\")",
            "os.popen": f"import os\nos.popen('{RM} ~')",
            "importlib": f"import importlib\nimportlib.import_module('os').system('{RM} ~')",
            "getattr": f"from pathlib import Path\ngetattr(Path('docs/a.md'), 'write_text')('{DROP_USERS}')",
        }
        # S3 (5.3.1 L1, D2): siguen en deny las que lanzan una orden literal
        # (mitigación de spawn); `exec`, `eval` y `getattr` con nombre en cadena
        # son huecos aceptados en el dictamen `security`.
        lanzan_orden = {"os.popen", "importlib"}
        for name, body in snippets.items():
            cmd = f"python3 - <<'PY'\n{body}\nPY"
            with self.subTest(name=name):
                expected = "deny" if name in lanzan_orden else "pass"
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], expected)

    def test_contrario_interpretes_que_ejecutan_sql_o_shell_siguen_denegados(self) -> None:
        for cmd in (
            f"sqlite3 prod.db '{DROP_USERS}'",
            f"psql -c '{TRUNC_USERS}'",
            f"bash -c '{RM} ~'",
            f"python3 -c \"import subprocess; subprocess.run(['rm', '-rf', '/var/data'])\"",
            f"python3 -c \"import os; os.system('{RM} /var/data')\"",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "deny")
        # S3 (5.3.1 L1, D2): un driver de base de datos desde Python no es el shell.
        driver = f"python3 - <<'PY'\nimport sqlite3\ncon = sqlite3.connect('prod.db')\ncursor = con.cursor()\ncursor.execute('{DROP_USERS}')\nPY"
        self.assertEqual(guard.judge(driver, str(ROOT))[0], "pass")


class Punto2WalCheckpointYSQLiteEfimeroTests(unittest.TestCase):
    """Punto 2 + Ajuste 2: `wal_checkpoint(TRUNCATE)` permitido; `TRUNCATE` en sentencia
    SQL y `ATTACH`, `load_extension`, `VACUUM INTO` o symlink fuera de temporal bloqueados."""

    def test_wal_checkpoint_truncate_case_insensitive_y_con_espacios_pasa(self) -> None:
        for cmd in (
            'sqlite3 respaldo.db "PRAGMA wal_checkpoint(TRUNCATE);"',
            'sqlite3 respaldo.db "pragma wal_checkpoint( truncate )"',
            'sqlite3 respaldo.db "PRAGMA journal_mode=WAL; PRAGMA wal_checkpoint(TRUNCATE);"',
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.judge(cmd)[0], "pass")

    def test_contrario_truncate_en_sentencia_sql_sigue_denegado(self) -> None:
        for cmd in (
            f"psql -c '{TRUNC_USERS}'",
            "psql -c 'TRUNCATE accounts'",
            f'sqlite3 respaldo.db "PRAGMA wal_checkpoint(TRUNCATE); {TRUNC_USERS};"',
            f'sqlite3 respaldo.db "PRAGMA wal_checkpoint(TRUNCATE); {DEL_USERS};"',
            f'sqlite3 respaldo.db "PRAGMA wal_checkpoint(TRUNCATE); {DROP_USERS};"',
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.judge(cmd)[0], "deny")

    def test_s3_sqlite_desde_python_ya_no_se_analiza(self) -> None:
        """S3 (5.3.1 L1, D2): el SQL que ejecuta un programa Python no lo ve el guard."""
        for forbidden_sql in (
            f"ATTACH DATABASE '/Users/x/prod.db' AS prod; PRAGMA wal_checkpoint(TRUNCATE)",
            f"SELECT load_extension('/tmp/evil.so'); PRAGMA wal_checkpoint(TRUNCATE)",
            f"VACUUM INTO '/Users/x/copia.db'; PRAGMA wal_checkpoint(TRUNCATE)",
            DROP_USERS,
            DEL_USERS,
            TRUNC_USERS,
        ):
            cmd = (
                "python3 - <<'PY'\n"
                "import sqlite3, tempfile\n"
                "from pathlib import Path\n"
                "with tempfile.TemporaryDirectory() as tmp:\n"
                "    con = sqlite3.connect(Path(tmp) / 'ejemplo.db')\n"
                f"    con.execute({forbidden_sql!r})\n"
                "PY"
            )
            with self.subTest(forbidden_sql=forbidden_sql):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "pass")

    def test_s3_sqlite_connect_a_symlink_desde_python_pasa(self) -> None:
        """S3 (5.3.1 L1, D2)."""
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp_dir, tempfile.TemporaryDirectory() as outside_dir:
            outside_db = Path(outside_dir) / "real.db"
            outside_db.write_text("", encoding="utf-8")
            link_db = Path(tmp_dir) / "enlace.db"
            link_db.symlink_to(outside_db)
            cmd = (
                "python3 - <<'PY'\n"
                "import sqlite3\n"
                "from pathlib import Path\n"
                f"con = sqlite3.connect(Path({str(link_db)!r}))\n"
                "con.execute('PRAGMA wal_checkpoint(TRUNCATE)')\n"
                "con.close()\n"
                "PY"
            )
            self.assertEqual(guard.judge(cmd, str(ROOT))[0], "pass")


class Punto3ScratchpadVariablesYForTests(unittest.TestCase):
    """Punto 3 + Condición 6 + Ajuste 5: resolución de variables en scratchpad/mktemp
    y sus contrarios obligatorios."""

    def test_mktemp_d_sin_argumentos_ni_tmpdir_asignado_pasa(self) -> None:
        for cmd in (
            f'T=$(mktemp -d) && {RM} "$T"',
            f'T=$(mktemp -d)\n{RM} "$T/sub"',
            f'T=$(mktemp -d) && for h in a b; do {RM} "$T/clon$h"; done',
        ):
            with self.subTest(cmd=cmd):
                with mock.patch.dict(os.environ, {"TMPDIR": "/private/tmp"}):
                    self.assertEqual(guard.judge(cmd, str(ROOT))[0], "pass")

    def test_contrario_mktemp_d_con_plantilla_con_flag_p_o_con_tmpdir_asignado_bloquea(self) -> None:
        for cmd in (
            f'T=$(mktemp -d /tmp/foo.XXXX) && {RM} "$T"',
            f'T=$(mktemp -d plantilla.XXXX) && {RM} "$T"',
            f'T=$(mktemp -d -p /tmp) && {RM} "$T"',
            f'T=$(mktemp -d -t foo) && {RM} "$T"',
            f'TMPDIR=/tmp T=$(mktemp -d) && {RM} "$T"',
            f'TMPDIR=/Users/x; T=$(mktemp -d) && {RM} "$T"',
            f'export TMPDIR=/tmp; T=$(mktemp -d) && {RM} "$T"',
        ):
            with self.subTest(cmd=cmd):
                with mock.patch.dict(os.environ, {"TMPDIR": "/private/tmp"}):
                    self.assertEqual(guard.judge(cmd, str(ROOT))[0], "deny")

    def test_contrario_variable_asignada_dos_veces_bloquea(self) -> None:
        for cmd in (
            f'S={SCRATCH}; S=/Users/x; {RM} "$S/clon"',
            f'S={SCRATCH}; S=/private/tmp/otro; {RM} "$S/clon"',
            f'S={SCRATCH}\nS={SCRATCH}\nfor h in a b; do {RM} "$S/clon$h"; done',
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "deny")

    def test_contrario_variable_asignada_dentro_de_condicion_bloquea(self) -> None:
        for cmd in (
            f'if true; then S={SCRATCH}; fi; {RM} "$S/clon"',
            f'false || S={SCRATCH}; {RM} "$S/clon"',
            f'case x in x) S={SCRATCH};; esac; {RM} "$S/clon"',
            f'(S={SCRATCH}) && {RM} "$S/clon"',
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "deny")

    def test_contrario_sufijo_de_bucle_con_barra_o_dos_puntos_seguidos_bloquea(self) -> None:
        for cmd in (
            f'S={SCRATCH}\nfor h in a b; do {RM} "$S/sub/clon$h"; done',
            f'S={SCRATCH}\nfor h in a b; do {RM} "$S/clon..$h"; done',
            f'S={SCRATCH}\nfor h in a b; do {RM} "$S/../$h"; done',
            f'S={SCRATCH}\nfor h in a/b c; do {RM} "$S/clon$h"; done',
            f'S={SCRATCH}\nfor h in .. a; do {RM} "$S/clon$h"; done',
            f'S={SCRATCH}\nfor h in a b; do {RM} "$S/$z"; done',
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.judge(cmd, str(ROOT))[0], "deny")


class Punto4MensajeDelGuardTests(unittest.TestCase):
    """Punto 4: el mensaje distingue «comando» de «texto que parece un comando» y
    propone la herramienta de edición de ficheros."""

    def test_mensaje_en_heredoc_o_texto_menciona_texto_y_herramienta_de_edicion(self) -> None:
        # S3 (5.3.1 L1): el heredoc a Python ya no se juzga; el mensaje se mantiene
        # para el heredoc a un shell.
        cmd = f"sh <<'EOF'\nprintf '%s' '{DROP_USERS}'\nEOF"
        verdict, reason = guard.judge(cmd, str(ROOT))
        self.assertEqual(verdict, "deny")
        self.assertIn("texto que parece un comando", reason.lower())
        self.assertTrue(
            any(w in reason for w in ("edición", "Edit", "Write")),
            f"el motivo debe proponer la herramienta de edición de ficheros: {reason!r}",
        )

    def test_mensaje_en_comando_directo_sigue_diciendo_comando(self) -> None:
        verdict, reason = guard.judge("prisma db push")
        self.assertEqual(verdict, "deny")
        self.assertNotIn("texto que parece un comando", reason.lower())


class Punto5OnFailureBlockYDoctorTests(unittest.TestCase):
    """Punto 5: `onFailure: "block"` en `settings.json.base` y `.claude/settings.json`,
    merge en `ensure_claude_settings.py`, sonda de arranque y aviso D19 (`< 2.1.295`)."""

    def test_settings_base_y_activo_declaran_onfailure_block_en_guard(self) -> None:
        for rel_path in (".agent/templates/settings.json.base", ".claude/settings.json"):
            data = json.loads((ROOT / rel_path).read_text(encoding="utf-8"))
            pre = data["hooks"]["PreToolUse"]
            guard_hooks = [
                h for g in pre for h in g.get("hooks", [])
                if "guard_destructive_command.py" in str(h.get("command", ""))
            ]
            self.assertEqual(len(guard_hooks), 1, rel_path)
            self.assertEqual(guard_hooks[0].get("onFailure"), "block", rel_path)
            # D2: el hook Stop NUNCA lleva onFailure: block
            stop_hooks = [h for g in data["hooks"].get("Stop", []) for h in g.get("hooks", [])]
            for sh in stop_hooks:
                self.assertNotEqual(sh.get("onFailure"), "block", f"Stop no debe tener onFailure=block en {rel_path}")

    def test_ensure_claude_settings_propaga_onfailure_sin_pisar_hooks_propios(self) -> None:
        base = json.loads((ROOT / ".agent/templates/settings.json.base").read_text(encoding="utf-8"))
        current = {
            "permissions": {"allow": ["Bash(ls *)"]},
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.agent/scripts/guard_destructive_command.py',
                                "statusMessage": "Production Command Guard: revisando comando destructivo (FW-082)...",
                            },
                            {
                                "type": "command",
                                "command": "my-custom-hook.sh",
                            },
                        ],
                    }
                ]
            },
        }
        merged, changes = ecs.merge_settings(base, current)
        self.assertTrue(changes, "debía detectar que faltaba onFailure: block")
        hooks = merged["hooks"]["PreToolUse"][0]["hooks"]
        guard_h = next(h for h in hooks if "guard_destructive_command.py" in h["command"])
        custom_h = next(h for h in hooks if h["command"] == "my-custom-hook.sh")
        self.assertEqual(guard_h.get("onFailure"), "block")
        self.assertIsNone(custom_h.get("onFailure"))
        self.assertEqual(merged["permissions"], {"allow": ["Bash(ls *)"]})
        # 5.3.1 L1.4 (cambio de contrato): con el núcleo irreversible solo se añade a
        # `permissions.deny`; `allow` y el resto de `permissions` no se tocan.
        merged2, _ = ecs.merge_settings(base, current, managed_deny=ecs.claude_deny_rules(ROOT))
        self.assertEqual(merged2["permissions"]["allow"], ["Bash(ls *)"])
        self.assertEqual(merged2["permissions"]["deny"], ecs.claude_deny_rules(ROOT))

    def test_doctor_comprueba_onfailure_arranque_del_guard_y_version_minima_2_1_295(self) -> None:
        # 1) Si Claude Code < 2.1.295, el doctor emite WARN mencionando 2.1.295 y onFailure (D19)
        errors: list[str] = []
        warnings: list[str] = []
        with mock.patch.object(doctor, "_harness_version", side_effect=lambda b: (2, 1, 294) if b == "claude" else None):
            doctor.validate_claude_guard_coverage(errors, warnings)
        self.assertEqual(errors, [])
        self.assertTrue(
            any("2.1.295" in w and "onFailure" in w for w in warnings),
            f"debía avisar cuando claude < 2.1.295: {warnings}",
        )

        # 2) Si Claude Code >= 2.1.295, no emite ese WARN
        errors2: list[str] = []
        warnings2: list[str] = []
        with mock.patch.object(doctor, "_harness_version", side_effect=lambda b: (2, 1, 296) if b == "claude" else None):
            doctor.validate_claude_guard_coverage(errors2, warnings2)
        self.assertEqual(errors2, [])
        self.assertFalse(any("2.1.295" in w for w in warnings2), warnings2)

        # 3) Si el guard no puede arrancar (p. ej. falla el subproceso), el doctor emite ERROR
        errors3: list[str] = []
        warnings3: list[str] = []
        with mock.patch.object(doctor, "_probe_guard_startup", return_value="exit 1"):
            doctor.validate_claude_guard_coverage(errors3, warnings3)
        self.assertTrue(
            any("guard_destructive_command.py" in e for e in errors3),
            f"un guard que no arranca debe dar error en el doctor (FW-034): {errors3}",
        )


class Punto6HuellasDeclaradasTests(unittest.TestCase):
    """Punto 6 + Condición 3 + Ajuste 3: marcadores de huella de 64 hex en la misma
    línea (`sha256`, `SHA-256`, `checksum`, `huella`, `fingerprint` — `commit` NO
    tolera 64 hex), tabla Markdown ligada por índice de columna y con el mismo número
    de celdas, y mensaje con la línea y el marcador aceptado."""

    def _scan_text(self, rel_name: str, content: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            sb = Path(tmp)
            _init_git_repo(sb)
            target = sb / rel_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            _git(sb, "add", rel_name)
            orig = doctor.ROOT
            doctor.ROOT = sb
            try:
                errors: list[str] = []
                doctor.scan_potential_secrets(errors)
                return errors
            finally:
                doctor.ROOT = orig

    def test_marcadores_aprobados_en_la_misma_linea_toleran_hex64_lejos_de_24_chars(self) -> None:
        h = _hex64("doc-final")
        for marker in ("sha256", "SHA-256", "checksum", "huella", "fingerprint"):
            line = f"{marker} verificada del documento de arquitectura revisado por el equipo: {h}\n"
            with self.subTest(marker=marker):
                self.assertEqual(self._scan_text("docs/revision.md", line), [])

    def test_hex_de_64_y_de_40_con_commit_pasan(self) -> None:
        # Cambiado en 5.3.1 L2.1 con la aprobación D2 del builder: un hex suelto
        # de 64 ya no bloquea (antes, el Ajuste 3 lo exigía con «commit»).
        h64 = _hex64("no-es-un-commit-de-git")
        h40 = _hex40("commit-real-de-git")
        self.assertEqual(
            self._scan_text("docs/notas.md", f"El commit de referencia para la entrega es {h64}\n"),
            [],
        )
        self.assertEqual(
            self._scan_text("docs/notas.md", f"El commit de referencia para la entrega es {h40}\n"),
            [],
        )

    def test_secreto_real_con_marcador_en_la_misma_linea_sigue_bloqueando(self) -> None:
        h64_secret = _hex64("secreto-64-en-asignacion")
        h40_secret = _hex40("secreto-40-desnudo")
        h32_secret = _hex32("secreto-32-desnudo")
        gh = "ghp_" + "A" * 36
        aws = "AKIA" + "I" * 16

        for marker in ("sha256", "SHA-256", "checksum", "huella", "fingerprint"):
            casos = [
                # Asignación con secreto de 64 hex + marcador en otra parte de la línea
                f'token = "{h64_secret}"  # {marker} verificada del fichero\n',
                f'# {marker} del bloque; api_key = "{h64_secret}"\n',
                # Huella legítima de 64 hex + secreto real en la misma línea
                f"{marker}: {_hex64('ok')} y además token = \"{h64_secret}\"\n",
                f"{marker}: {_hex64('ok')} con credencial {gh}\n",
                f"{marker}: {_hex64('ok')} con clave {aws}\n",
                # Hex de 32 o 40 asignado a una clave que nombra un secreto. Cambiado
                # en 5.3.1 L2.1 (D2): la prosa («la clave interna es <hex>») ya no es
                # una asignación y pasa; con clave, sigue bloqueando.
                f"clave_api = {h32_secret}  # la {marker} del documento cambia\n",
                f"token: {h40_secret}  # {marker} del documento\n",
            ]
            for line in casos:
                with self.subTest(marker=marker, line=line[:50]):
                    errs = self._scan_text("src/config.py", line)
                    self.assertTrue(errs, f"debía seguir detectándose con {marker!r}: {line!r}")

    def test_tabla_markdown_hex_en_cualquier_columna_pasa_y_credencial_con_formato_bloquea(self) -> None:
        # Cambiado en 5.3.1 L2.1 con la aprobación D2 y D3 del builder: un hex
        # suelto pasa en cualquier columna y en una fila descuadrada; una
        # credencial con formato sigue bloqueando en cualquier celda.
        h_ok = _hex64("celda-huella")
        h_bad = _hex64("celda-secreta")
        # 1) Hex de 64 en una columna «Token» → pasa (D3: la cabecera no es una clave)
        tabla_otra_col = (
            "| Fichero | Huella | Token |\n"
            "|---|---|---|\n"
            f"| `docs/a.md` | {h_ok} | {h_bad} |\n"
        )
        self.assertEqual(self._scan_text("docs/inv.md", tabla_otra_col), [])
        # 2) Fila con distinto número de celdas que la cabecera → pasa
        tabla_descuadrada = (
            "| Fichero | Huella |\n"
            "|---|---|\n"
            f"| `docs/a.md` | {h_ok} | celda_extra |\n"
        )
        self.assertEqual(self._scan_text("docs/inv2.md", tabla_descuadrada), [])
        # 3) Credencial con formato dentro de la propia columna Huella → bloquea
        gh = "ghp_" + "B" * 36
        tabla_cred_en_huella = (
            "| Fichero | Huella |\n"
            "|---|---|\n"
            f"| `docs/a.md` | {gh} |\n"
        )
        self.assertTrue(self._scan_text("docs/inv3.md", tabla_cred_en_huella))

        # 4) Marcador de huella dentro de la propia celda de una tabla con otra cabecera (Example Tool, PA, TL) → pasa
        tabla_marcador_en_celda = (
            "| Comprobación | Resultado |\n"
            "|---|---|\n"
            f"| Base real | sha256 {h_ok}, igual que antes de la fase |\n"
            f"| Provenance | `SKILL.md`; SHA-256 de `SKILL.md`: `{h_ok}` |\n"
            f"| Candidata | strategy_hash `{h_ok}` |\n"
        )
        self.assertEqual(self._scan_text("docs/inv4.md", tabla_marcador_en_celda), [])

    def test_mensaje_de_error_imprime_la_linea_y_el_marcador_que_aceptaria(self) -> None:
        # Cambiado en 5.3.1 L2.1 con la aprobación D2 del builder: una línea sin
        # clave ya no bloquea; el mensaje nombra la regla y la salida, sin el valor.
        h64 = _hex64("huella-sin-marcador")
        linea = f"token: {h64}"
        errs = self._scan_text("docs/artefacto.md", f"# Título\n\n{linea}\n")
        self.assertEqual(len(errs), 1, errs)
        msg = errs[0]
        self.assertIn("docs/artefacto.md:3", msg, f"debe indicar línea (:3): {msg}")
        self.assertIn("token:", msg, f"debe imprimir la línea con la clave: {msg}")
        self.assertNotIn(h64, msg, f"el valor no debe aparecer en el mensaje: {msg}")
        self.assertNotIn(h64[:16], msg, f"el valor sospechoso debe ir enmascarado en el mensaje: {msg}")
        for m in ("variable de entorno", "example", "ficticio"):
            self.assertIn(m, msg, f"debe indicar la salida ({m!r}): {msg}")

        gh = "ghp_" + "A" * 36
        errs_cred = self._scan_text("src/cliente.py", f"# Cabecera\nTOKEN_EXPORTADO = '{gh}'\n")
        self.assertEqual(len(errs_cred), 1, errs_cred)
        msg_cred = errs_cred[0]
        self.assertIn("src/cliente.py:2", msg_cred, f"debe indicar línea (:2): {msg_cred}")
        self.assertIn("TOKEN_EXPORTADO", msg_cred, f"debe imprimir el contexto de la línea: {msg_cred}")
        self.assertNotIn(gh, msg_cred, f"la credencial con formato no debe aparecer en el mensaje: {msg_cred}")
        self.assertNotIn("A" * 12, msg_cred, f"el cuerpo de la credencial debe ir enmascarado: {msg_cred}")


# Punto 7 (`identity_prefixes`): retirado en 5.3.1 L2.1 con la aprobación D10 del
# builder. Con la regla invertida, un identificador `<prefijo>-<hex>` ya no es un
# secreto sin perfil. El caso contrario (asignación de credencial con prefijo) vive
# en tests/test_l2_precommit_secretos.py.


class Punto8MismoResultadoDentroYFueraDelCommitTests(unittest.TestCase):
    """Punto 8 + Condición 5 + Ajuste 1: `scan_potential_secrets` revisa también los
    ficheros sin versionar que no estén ignorados (`git ls-files --others --exclude-standard`),
    dando el mismo resultado con `staged=False` y con `staged=True`, sin escanear lo
    ignorado por `.gitignore`."""

    def test_secreto_en_fichero_nuevo_sin_versionar_se_detecta_fuera_y_dentro_del_commit(self) -> None:
        # Cambiado en 5.3.1 L2.1 (D2): el secreto es una credencial con formato,
        # porque un hex suelto ya no lo es.
        h64 = "ghp_" + "N" * 36
        with tempfile.TemporaryDirectory() as tmp:
            sb = Path(tmp)
            _init_git_repo(sb)
            (sb / ".gitignore").write_text("ignorado/\n", encoding="utf-8")
            _git(sb, "add", ".gitignore")
            _git(sb, "commit", "-qm", "init")

            # Fichero ignorado con secreto: nunca se escanea
            (sb / "ignorado").mkdir()
            (sb / "ignorado" / "cache.txt").write_text(f" Sin marcador {h64}\n", encoding="utf-8")

            # Fichero nuevo NO ignorado y aún sin `git add`
            nuevo = sb / "_project" / "historia_fiel_fase4_validacion.md"
            nuevo.parent.mkdir(parents=True, exist_ok=True)
            nuevo.write_text(f"Valor suelto sin marcador: {h64}\n", encoding="utf-8")

            orig = doctor.ROOT
            doctor.ROOT = sb
            try:
                errs_unstaged: list[str] = []
                doctor.scan_potential_secrets(errs_unstaged, staged=False)
                errs_staged_before_add: list[str] = []
                doctor.scan_potential_secrets(errs_staged_before_add, staged=True)

                _git(sb, "add", "_project/historia_fiel_fase4_validacion.md")
                errs_staged_after_add: list[str] = []
                doctor.scan_potential_secrets(errs_staged_after_add, staged=True)
            finally:
                doctor.ROOT = orig

            self.assertEqual(len(errs_unstaged), 1, errs_unstaged)
            # Cambiado en 5.3.1 L2.2 con la aprobación D2 y D6 del builder: en el
            # commit solo se escanea lo preparado; antes del `git add` el fichero
            # no entra en el commit y no se escanea (PA-FW-005).
            self.assertEqual(errs_staged_before_add, [])
            self.assertEqual(errs_unstaged, errs_staged_after_add)
            self.assertFalse(any("ignorado/" in e for e in errs_unstaged))


class PreservacionCorpusAdversarioExactoTests(unittest.TestCase):
    """Condición 1: la línea del paso 0 se fija por caso, no por recuento. Cada caso
    adversario del guard y del escáner conserva su veredicto exacto (`deny` no baja
    a `ask` ni a `pass`; `ask` no baja a `pass`)."""

    def test_todos_los_casos_deny_del_guard_siguen_siendo_deny(self) -> None:
        deny_lists = [
            ("DestructiveDetectionTests.BAD", tgdc.DestructiveDetectionTests.BAD),
            ("PreToolUseHookTests.bypasses", [
                "/bin/rm -rf /Users/x",
                "echo hola && rm -rf /Users/x",
                'sh -c "rm -rf /Users/x"',
                'bash -c "rm -rf /Users/x"',
                "rm -rf /tmp/../Users/x",
                "git -C /repo push --force",
                "git -c user.name=x push -f",
                "ls; rm -rf /Users/x",
            ]),
            ("Separator.SEPARADOR_PEGADO", tgdc.SeparatorAndSubstitutionBypassTests.SEPARADOR_PEGADO),
            ("Separator.SALTO_DE_LINEA", tgdc.SeparatorAndSubstitutionBypassTests.SALTO_DE_LINEA),
            ("Separator.SUSTITUCION", tgdc.SeparatorAndSubstitutionBypassTests.SUSTITUCION),
            ("Separator.AGRUPACION", tgdc.SeparatorAndSubstitutionBypassTests.AGRUPACION),
            ("Separator.MARCADOR_EN_DATOS", tgdc.SeparatorAndSubstitutionBypassTests.MARCADOR_EN_DATOS),
            ("Separator.HEREDOC_A_EJECUTOR", tgdc.SeparatorAndSubstitutionBypassTests.HEREDOC_A_EJECUTOR),
            ("Separator.DATO_QUE_TERMINA_EJECUTADO", tgdc.SeparatorAndSubstitutionBypassTests.DATO_QUE_TERMINA_EJECUTADO),
            ("Separator.COMILLAS_ANSI_C", tgdc.SeparatorAndSubstitutionBypassTests.COMILLAS_ANSI_C),
            ("SecurityReview1.REGRESIONES", tgdc.SecurityReviewFindingsTests.REGRESIONES),
            ("SecurityReview1.PREEXISTENTES", tgdc.SecurityReviewFindingsTests.PREEXISTENTES),
            ("SecurityReview2.REGRESIONES", tgdc.SecondSecurityReviewTests.REGRESIONES),
            ("SecurityReview2.PREEXISTENTES", tgdc.SecondSecurityReviewTests.PREEXISTENTES),
            ("SecurityReview3.DEBEN_BLOQUEAR", tgdc.ThirdSecurityReviewTests.DEBEN_BLOQUEAR),
            ("DestructivePushTests.BAD", tgdc.DestructivePushTests.BAD),
            ("FourthSecurityReviewTests.BAD", tgdc.FourthSecurityReviewTests.BAD),
            ("DenyStaysDenyTests.DENY", tgie.DenyStaysDenyTests.DENY),
            ("OLD_DENIED", [
                f"python3 - <<'PY'\n{tgie.AdversarialRelaxationTests.PYTHON_ESCAPES[name]}\nPY"
                for name in tgie.AdversarialRelaxationTests.OLD_DENIED
            ]),
        ]
        total = 0
        for label, commands in deny_lists:
            for cmd in commands:
                total += 1
                with self.subTest(label=label, cmd=cmd[:60]):
                    verdict, reason = guard.judge(cmd)
                    self.assertEqual(verdict, "deny", f"{label}: {cmd!r} bajó a {verdict!r} ({reason!r})")
        self.assertEqual(total, 181)

    def test_todos_los_casos_ask_del_guard_siguen_siendo_ask(self) -> None:
        total = 0
        for name, cmd in tgie.AskIndirectExecutionTests.ASK.items():
            total += 1
            with self.subTest(name=name):
                verdict, reason = guard.judge(cmd)
                self.assertEqual(verdict, "ask", f"{name}: {cmd!r} cambió a {verdict!r} ({reason!r})")
        # S3 (5.3.1 L1, D2): salen las 4 entradas de Python de la clase 4 retirada.
        self.assertEqual(total, 14)

    def test_corpus_fw236_29_casos_conservan_veredicto_exacto(self) -> None:
        for expected, cmd, cwd in tgie.Fw236CorpusTests.CASES:
            with self.subTest(cmd=cmd[:60]):
                self.assertEqual(guard.judge(cmd, cwd)[0], expected, cmd)

    def test_calibracion_de_los_8_gates_intacta(self) -> None:
        self.assertEqual(gc.run_harness(), [])


if __name__ == "__main__":
    unittest.main()
