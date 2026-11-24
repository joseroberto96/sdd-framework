#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.2.2 L4: el guard resuelve `cd <temporal literal> && rm -rf <relativo>` y
`$TMPDIR` cuando caen bajo un prefijo temporal (PA FW-138, job FW-274).

Los comandos se montan en runtime (`RM`) para que el propio guard, que juzga
los comandos de la sesión, no los vea como texto literal."""
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import guard_destructive_command as guard  # noqa: E402

RM = "rm" + " -rf"
SCRATCH = "/private/tmp/claude-501/-Users-x-proj/sess/scratchpad"
PROJECT_CWD = "/Users/x/proj"
BT = "`"


class CdLiteralTemporalTests(unittest.TestCase):
    def verdict(self, command: str, cwd: str = PROJECT_CWD) -> str:
        return guard.judge(command, cwd)[0]

    def test_pa_fw138_cd_scratchpad_then_relative_rm_passes(self):
        self.assertEqual(self.verdict(f"cd {SCRATCH} && {RM} o1root && sed -n 1p notas.txt"), "pass")

    def test_cd_quoted_temporal_path_passes(self):
        self.assertEqual(self.verdict(f"cd '{SCRATCH}' && {RM} smoke"), "pass")

    def test_cd_non_temporal_path_still_blocked(self):
        self.assertEqual(self.verdict(f"cd /Users/x && {RM} y"), "deny")

    def test_cd_variable_still_blocked(self):
        self.assertEqual(self.verdict(f"cd $X && {RM} y"), "deny")

    def test_cd_relative_path_still_blocked(self):
        self.assertEqual(self.verdict(f"cd scratchpad && {RM} y", cwd="/private/tmp"), "deny")

    def test_cd_with_semicolon_still_blocked(self):
        # Con `;` el rm corre aunque el cd falle: en el cwd original.
        self.assertEqual(self.verdict(f"cd {SCRATCH}; {RM} y"), "deny")

    def test_cd_temporal_then_list_break_still_blocked(self):
        # Si el cd falla, lo que va tras `;`, `||`, `&` o un salto de línea
        # corre en el cwd original.
        for sep in ("; ", " || ", " & ", "\n"):
            with self.subTest(sep=sep):
                self.assertEqual(self.verdict(f"cd {SCRATCH} && true{sep}{RM} y"), "deny")

    def test_cd_temporal_without_event_cwd_passes(self):
        self.assertEqual(guard.judge(f"cd {SCRATCH} && {RM} o1root")[0], "pass")

    def test_cd_temporal_with_dotdot_or_dot_component_still_blocked(self):
        # El kernel resuelve `/tmp/lnk` antes del `..`: normalizar el texto miente.
        for path in ("/tmp/lnk/../x", "/tmp/./x", "/private/tmp/a/.."):
            with self.subTest(path=path):
                self.assertEqual(self.verdict(f"cd {path} && {RM} y"), "deny")

    def test_cd_temporal_escaping_with_dotdot_still_blocked(self):
        self.assertEqual(self.verdict(f"cd /tmp/../Users/x && {RM} y"), "deny")

    def test_second_cd_after_temporal_still_blocked(self):
        self.assertEqual(self.verdict(f"cd {SCRATCH} && cd /Users/x && {RM} y"), "deny")

    def test_cd_temporal_then_dot_or_parent_still_blocked(self):
        for target in (".", "..", "*", "../x"):
            with self.subTest(target=target):
                self.assertEqual(self.verdict(f"cd {SCRATCH} && {RM} {target}"), "deny")

    def test_cd_temporal_then_absolute_home_still_blocked(self):
        self.assertEqual(self.verdict(f"cd {SCRATCH} && {RM} ~"), "deny")


class TmpdirTests(unittest.TestCase):
    def verdict(self, command: str, tmpdir: str) -> str:
        with unittest.mock.patch.dict(os.environ, {"TMPDIR": tmpdir}):
            return guard.judge(command, PROJECT_CWD)[0]

    def test_tmpdir_under_temporal_prefix_passes(self):
        for ref in ("$TMPDIR/smoke", "${TMPDIR}/smoke", '"$TMPDIR/smoke"'):
            with self.subTest(ref=ref):
                self.assertEqual(self.verdict(f"{RM} {ref}", "/var/folders/ab/cd/T/"), "pass")

    def test_tmpdir_outside_prefix_still_blocked(self):
        self.assertEqual(self.verdict(f"{RM} $TMPDIR/smoke", "/Users/x"), "deny")

    def test_tmpdir_itself_still_blocked(self):
        # Borrar el propio TMPDIR no es un subdirectorio efímero de sesión.
        self.assertEqual(self.verdict(f"{RM} $TMPDIR", "/var/folders/ab/cd/T/"), "deny")

    def test_tmpdir_reassigned_in_command_is_not_trusted(self):
        for cmd in (f"TMPDIR=$HOME; {RM} $TMPDIR/x", f"export TMPDIR=$HOME && {RM} $TMPDIR/x",
                    f"read TMPDIR; {RM} $TMPDIR/x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd, "/var/folders/ab/cd/T/"), "deny")

    def test_tmpdir_reassigned_with_split_name_is_not_trusted(self):
        # bash quita las comillas antes de que `export` reciba el nombre.
        for cmd in (f"export TMP''DIR=$HOME; {RM} \"$TMPDIR/Documents\"",
                    f"export TMP\\DIR=$HOME; {RM} $TMPDIR/Documents",
                    f"printf -v TMP''DIR %s \"$HOME\"; {RM} $TMPDIR/Documents",
                    f"x=TMP; export ${{x}}DIR=$HOME; {RM} $TMPDIR/Documents",
                    f"unset TMP''DIR; {RM} $TMPDIR/Documents",
                    f"a=TMP; b=DIR; sleep 0 & wait -p $a$b; {RM} \"$TMPDIR/Documents\"",
                    f"n=TMP; n=${{n}}DIR; print -v $n $HOME; {RM} \"$TMPDIR/Documents\"",
                    f"n=TMPDIR; : ${{(P)n::=$HOME}}; {RM} \"$TMPDIR/Documents\""):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd, "/var/folders/ab/cd/T/"), "deny")

    def test_tmpdir_not_trusted_inside_nested_shell(self):
        cmd = f"export TMPDIR=$HOME; bash -c '{RM} \"$TMPDIR/Documents\"'"
        self.assertNotEqual(self.verdict(cmd, "/var/folders/ab/cd/T/"), "pass")

    def test_tmpdir_symlink_out_of_temporal_is_not_trusted(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir="/tmp") as base:
            link = os.path.join(base, "lnk")
            os.symlink(str(Path.home()), link)
            self.assertEqual(self.verdict(f"{RM} $TMPDIR/Documents", link), "deny")

    def test_tmpdir_with_dotdot_escape_still_blocked(self):
        self.assertEqual(self.verdict(f"{RM} $TMPDIR/../../Users", "/var/folders/ab/cd/T/"), "deny")


class HeredocJobFw274Tests(unittest.TestCase):
    """job FW-274 (heredoc): con delimitador entrecomillado el cuerpo es texto
    y pasa; sin comillas bash EJECUTA las comillas invertidas del cuerpo, así
    que el bloqueo es correcto y el mensaje debe decir cómo escribir el texto."""

    def test_quoted_heredoc_describing_command_passes(self):
        cmd = f"cat >> _feedback/field_report.md <<'EOF'\nun {BT}{RM} smoke{BT} en el scratchpad\nEOF"
        self.assertEqual(guard.judge(cmd, PROJECT_CWD)[0], "pass")

    def test_unquoted_heredoc_with_backticks_blocked_with_hint(self):
        cmd = f"cat >> _feedback/field_report.md <<EOF\nun {BT}{RM} smoke{BT}\nEOF"
        verdict, reason = guard.judge(cmd, PROJECT_CWD)
        self.assertEqual(verdict, "deny")
        self.assertIn("<<'EOF'", reason)


if __name__ == "__main__":
    unittest.main()
