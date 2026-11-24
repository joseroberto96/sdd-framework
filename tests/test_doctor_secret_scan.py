#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Escáner de secretos del doctor (scan_potential_secrets).

Cubre FW-040: en proyectos con blobs grandes (modelos ML/audio, p. ej. Whisper
MLX) el escáner hacía read_text sobre CADA archivo no excluido sin guarda de
tamaño ni de binario, colgándose >60s y forzando --no-verify. El escáner debe
saltar archivos binarios y por encima de un umbral de tamaño, sin dejar de
detectar credenciales reales en archivos de texto normales.

Los payloads de credencial se montan en runtime para no introducir un patrón
contiguo en este propio archivo que dispare el escáner sobre el repo.
"""
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import framework_doctor as doctor  # noqa: E402


def _assignment_secret() -> str:
    # api_key = "Akk…k9"  (asignación con valor de ≥12 chars, fuera de allowlist)
    return "api_key" + " = " + '"' + "A" + "k" * 24 + '9"'


class TestSecretScanGuards(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self._tmp.name)
        self._orig_root = doctor.ROOT
        doctor.ROOT = self.sandbox

    def tearDown(self):
        doctor.ROOT = self._orig_root
        self._tmp.cleanup()

    def test_binary_file_is_skipped_even_with_embedded_pattern(self):
        # Binario (NUL al frente) que SÍ contiene un patrón de credencial en ASCII.
        # El código sin guarda lo decodifica y lo reporta; con guarda lo salta.
        blob = self.sandbox / "model.bin"
        blob.write_bytes(b"\x00\x01\x02" + _assignment_secret().encode("utf-8"))
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(errors, [], f"un binario no debe escanearse, got: {errors}")

    def test_oversized_text_file_is_skipped(self):
        big = self.sandbox / "huge.txt"
        big.write_text(_assignment_secret() + "\n" + ("x" * 4096), encoding="utf-8")
        errors: list[str] = []
        with unittest.mock.patch.object(doctor, "SECRET_SCAN_MAX_BYTES", 64):
            doctor.scan_potential_secrets(errors)
        self.assertEqual(errors, [], f"un archivo sobre el umbral no debe escanearse, got: {errors}")

    def test_real_secret_in_small_text_is_still_detected(self):
        secret_file = self.sandbox / "config.py"
        secret_file.write_text(_assignment_secret() + "\n", encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(
            any("config.py" in e for e in errors),
            f"un secreto real en texto debe seguir detectándose, got: {errors}",
        )


class TestSecretScanFW045(unittest.TestCase):
    """PA FW-045: un fixture explícitamente falso en tests/ (`secret = "..."`)
    usado SOLO como marcador negativo en `assertNotIn(...)` bloqueaba el
    doctor tras cerrar un módulo, aunque el test demuestra precisamente que
    ese valor NO se filtra."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self._tmp.name)
        self._orig_root = doctor.ROOT
        doctor.ROOT = self.sandbox

    def tearDown(self):
        doctor.ROOT = self._orig_root
        self._tmp.cleanup()

    def test_negative_assertion_fixture_in_tests_dir_is_not_flagged(self):
        # Síntoma literal del field report: `secret = "token-super-secreto"`
        # inyectado en un env temporal, verificado ausente vía assertNotIn.
        tests_dir = self.sandbox / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_local_runtime.py").write_text(
            "import unittest\n\n"
            "class TestLocalRuntime(unittest.TestCase):\n"
            "    def test_secret_not_leaked_in_reasons(self):\n"
            '        secret = "token-super-secreto"\n'
            '        env = {"MY_TOKEN": secret}\n'
            "        reasons = compute_reasons(env)\n"
            "        self.assertNotIn(secret, reasons)\n",
            encoding="utf-8",
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(
            errors, [],
            f"fixture negativo explícito en tests/ no debe bloquear el doctor, got: {errors}",
        )

    def test_real_secret_in_tests_dir_without_negative_assertion_still_detected(self):
        # Salvaguarda: el mismo directorio tests/ con un secreto real que NO
        # participa de un assertNotIn debe seguir bloqueando.
        tests_dir = self.sandbox / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_leaks_real_key.py").write_text(
            _assignment_secret() + "\nprint(api_key)\n", encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(
            any("test_leaks_real_key.py" in e for e in errors),
            f"un secreto real sin assertNotIn debe seguir detectándose, got: {errors}",
        )

    def test_real_secret_with_unrelated_assertnotin_identifier_still_detected(self):
        # Salvaguarda: un assertNotIn en el archivo no exime CUALQUIER
        # secreto — solo el identificador que efectivamente se niega.
        tests_dir = self.sandbox / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_mismatched_identifier.py").write_text(
            _assignment_secret() + "\n"
            'unrelated = "not-a-secret-value"\n'
            'self.assertNotIn(unrelated, "somewhere")\n',
            encoding="utf-8",
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(
            any("test_mismatched_identifier.py" in e for e in errors),
            f"un secreto real con assertNotIn de OTRO identificador debe seguir detectándose, got: {errors}",
        )


class TestSecretScanFW110(unittest.TestCase):
    """FW-110: marcadores en español 'prueba'/'ensayo' faltaban en el
    allowlist — un fixture de test con un token de prueba explícito
    (p. ej. `TOKEN = "token-de-prueba"`) disparaba el gate de secretos
    como si fuera una credencial real. Coherente con la corrección 4.11.1
    que ya añadió 'falso'/'ficticio' al mismo allowlist."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self._tmp.name)
        self._orig_root = doctor.ROOT
        doctor.ROOT = self.sandbox

    def tearDown(self):
        doctor.ROOT = self._orig_root
        self._tmp.cleanup()

    def test_prueba_marker_is_not_flagged(self):
        (self.sandbox / "test_local_runtime.py").write_text(
            'TOKEN = "token-de-prueba"\n', encoding="utf-8"
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(
            errors, [], f"token-de-prueba no debe disparar el gate, got: {errors}"
        )

    def test_ensayo_marker_is_not_flagged(self):
        (self.sandbox / "test_local_runtime.py").write_text(
            'TOKEN = "clave-de-ensayo-temporal"\n', encoding="utf-8"
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(
            errors, [], f"clave-de-ensayo no debe disparar el gate, got: {errors}"
        )

    def test_real_secret_without_marker_still_detected(self):
        (self.sandbox / "config.py").write_text(
            _assignment_secret() + "\n", encoding="utf-8"
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(
            errors, "un secreto real sin marcador debe seguir detectándose"
        )


def _gh_token() -> str:
    return "ghp_" + "A" * 36  # GitHub PAT (regla 5)


def _aws_key() -> str:
    return "AKIA" + "I" * 16  # AWS Access Key ID (regla 3)


class TestSecretScanFW042(unittest.TestCase):
    """FW-042: el escáner no respetaba .gitignore (escaneaba cache de modelos ML) y
    marcaba SHAs de commit git y prosa documental como secretos. El fix escanea
    sobre git ls-files (solo lo versionado), amplía el allowlist con contexto de
    referencias git, y exime _feedback/field_report.md (documenta patrones). Sin
    debilitar la detección de credenciales reales."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self._tmp.name)
        self._orig_root = doctor.ROOT
        doctor.ROOT = self.sandbox

    def tearDown(self):
        doctor.ROOT = self._orig_root
        self._tmp.cleanup()

    def _git(self, *args):
        import subprocess
        subprocess.run(["git", *args], cwd=self.sandbox, check=True,
                       capture_output=True)

    def test_gitignored_file_is_not_scanned(self):
        # mlx_models/ gitignored con cache que parece secreto → no debe escanearse;
        # un archivo versionado con secreto real SÍ debe detectarse.
        self._git("init")
        self._git("config", "user.email", "t@t.t")
        self._git("config", "user.name", "t")
        (self.sandbox / ".gitignore").write_text("mlx_models/\n", encoding="utf-8")
        (self.sandbox / "mlx_models").mkdir()
        (self.sandbox / "mlx_models" / "blob.metadata").write_text(
            ("ab" * 20) + "\n", encoding="utf-8")  # 40-hex montado en runtime
        (self.sandbox / "tracked.py").write_text(_assignment_secret() + "\n", encoding="utf-8")
        self._git("add", ".gitignore", "tracked.py")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertFalse(any("mlx_models" in e for e in errors),
                         f"un archivo gitignored no debe escanearse, got: {errors}")
        self.assertTrue(any("tracked.py" in e for e in errors),
                        f"un secreto en archivo versionado SÍ debe detectarse, got: {errors}")

    def test_staged_secret_con_worktree_limpio_se_detecta_en_modo_staged(self):
        # Triaje 2026-07-09 C3: el commit lleva los BLOBS DEL ÍNDICE, no el
        # worktree. Un secreto staged con worktree ya corregido (sin re-add)
        # pasaba el pre-commit. En modo staged se escanea `git show :ruta`.
        self._git("init")
        self._git("config", "user.email", "t@t.t")
        self._git("config", "user.name", "t")
        cfg = self.sandbox / "config.py"
        cfg.write_text(_assignment_secret() + "\n", encoding="utf-8")
        self._git("add", "config.py")
        # worktree "corregido" después del add, sin re-stagear
        cfg.write_text("api_key = os.environ['API_KEY']\n", encoding="utf-8")

        errors_worktree: list[str] = []
        doctor.scan_potential_secrets(errors_worktree)
        self.assertEqual(errors_worktree, [],
                         "modo worktree ve el archivo limpio (este era el hueco)")

        errors_staged: list[str] = []
        doctor.scan_potential_secrets(errors_staged, staged=True)
        self.assertTrue(any("config.py" in e for e in errors_staged),
                        f"modo staged debe ver el secreto del índice, got: {errors_staged}")

    def test_git_sha_in_docs_is_not_flagged(self):
        sha = "cbb3d5602a972a18eae24ef99" + "beb90bb90096244"  # 40-hex montado (SHA commit)
        doc = self.sandbox / "release.md"
        doc.write_text(f"- Última publicación: `{sha}`\n- git checkout `{sha}`\n", encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(errors, [], f"un SHA de commit documentado no es secreto, got: {errors}")

    def test_field_report_documenting_patterns_is_not_flagged(self):
        fr = self.sandbox / "_feedback"
        fr.mkdir()
        (fr / "field_report.md").write_text(
            "El escáner detecta patrones como BEGIN PRIVATE KEY y ghp_ en credenciales.\n",
            encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(errors, [], f"el field report documenta patrones, no son secretos: {errors}")

    def test_internal_field_report_is_exempt(self):
        # FW-051: el field report INTERNO del template (.agent/docs/field_report.md)
        # registra fricción FW y puede citar patrones tipo credencial al documentar
        # una fricción del propio escáner. Debe quedar exento igual que el
        # _feedback/field_report.md downstream; un secreto REAL ahí no debe bloquear
        # el commit (y entrenar --no-verify). Sin exención, _assignment_secret() lo
        # marca (ver test_real_secret_in_small_text_is_still_detected).
        docs = self.sandbox / ".agent" / "docs"
        docs.mkdir(parents=True)
        (docs / "field_report.md").write_text(_assignment_secret() + "\n", encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(
            errors, [],
            f".agent/docs/field_report.md debe estar exento (FW-051), got: {errors}")

    def test_real_secrets_still_detected(self):
        # SALVAGUARDA: el fix no debe ocultar credenciales reales.
        for name, payload in [
            ("a.py", _gh_token()),
            ("b.py", _aws_key()),
            ("c.py", _assignment_secret()),
            ("d.pem", "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----"),
        ]:
            (self.sandbox / name).write_text(payload + "\n", encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        for name in ("a.py", "b.py", "c.py", "d.pem"):
            self.assertTrue(any(name in e for e in errors),
                            f"{name} (secreto real) debe seguir detectándose, got: {errors}")


class TestSecretScanFW216(unittest.TestCase):
    """FW-216: un hex de 40 caracteres es inocuo solo con contexto git explícito
    en la misma línea (commit, sha, base, tag, rev-parse, ls-remote, git ...).
    Sin ese contexto, el hex suelto en src/ se sigue detectando como secreto."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self._tmp.name)
        self._orig_root = doctor.ROOT
        doctor.ROOT = self.sandbox

    def tearDown(self):
        doctor.ROOT = self._orig_root
        self._tmp.cleanup()

    def test_git_sha_with_commit_base_context_in_docs_is_not_flagged(self):
        hex_40 = "a1b2c3d4e5" + "f60718293a" + "4b5c6d7e8f" + "9012345678"
        docs_dir = self.sandbox / "docs"
        docs_dir.mkdir()
        (docs_dir / "plan.md").write_text(
            f"El commit base para la rama es {hex_40} en el repositorio.\n",
            encoding="utf-8",
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(errors, [], f"40-hex con contexto git 'commit base' no debe ser secreto, got: {errors}")

    def test_bare_40_hex_in_src_without_git_context_is_flagged(self):
        hex_40 = "a1b2c3d4e5" + "f60718293a" + "4b5c6d7e8f" + "9012345678"
        src_dir = self.sandbox / "src"
        src_dir.mkdir()
        (src_dir / "module.py").write_text(
            f'TOKEN = "{hex_40}"\n',
            encoding="utf-8",
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(
            any("module.py" in e for e in errors),
            f"40-hex suelto en src/ sin contexto git debe detectarse como secreto, got: {errors}",
        )


class TestSecretScanN9(unittest.TestCase):
    """N9: SECRET_RE detecta credenciales en URLs scheme://user:pass@host.
    NO marca user@host sin contraseña, ni postgres://localhost, ni contraseñas
    con placeholder (:password@, :<pass>@, :***@, :${VAR}@)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self._tmp.name)
        self._orig_root = doctor.ROOT
        doctor.ROOT = self.sandbox

    def tearDown(self):
        doctor.ROOT = self._orig_root
        self._tmp.cleanup()

    def test_url_with_real_credentials_is_detected(self):
        user = "admin"
        password = "aB3" + "dE6" + "gH1j"  # 10 caracteres alfanuméricos
        url = "postgres://" + user + ":" + password + "@db.example.invalid:5432/prod"
        (self.sandbox / "config.py").write_text(f'DATABASE_URL = "{url}"\n', encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(
            any("config.py" in e for e in errors),
            f"URL con credenciales reales debe detectarse, got: {errors}",
        )

    def test_url_without_password_or_localhost_not_flagged(self):
        (self.sandbox / "settings.py").write_text(
            'DB_URL = "postgres://localhost/db"\n'
            'REPO_URL = "https://user@github.com/repo"\n',
            encoding="utf-8",
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(errors, [], f"URLs sin password no deben disparar el escáner, got: {errors}")

    def test_url_with_placeholders_not_flagged(self):
        (self.sandbox / "placeholders.py").write_text(
            'U1 = "postgres://user:password@db:5432/main"\n'
            'U2 = "postgres://user:<pass>@db:5432/main"\n'
            'U3 = "postgres://user:<placeholder>@db:5432/main"\n'
            'U4 = "postgres://user:***@db:5432/main"\n'
            'U5 = "postgres://user:${DB_PASS}@db:5432/main"\n',
            encoding="utf-8",
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(errors, [], f"URLs con placeholders no deben disparar el escáner, got: {errors}")


if __name__ == "__main__":
    unittest.main()


class TestSecretScanFormattedVsGeneric(unittest.TestCase):
    """5.2.2 L3: la allowlist (y el contexto git) solo tolera la regla
    GENÉRICA hex. Antes se aplicaba sin mirar qué regla saltó, así que una
    credencial con formato pasaba si había una palabra permitida cerca
    (`commit`, `tag`, `example`), en la misma cadena JSON o dentro del propio
    token (`0000`)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self._tmp.name)
        self._orig_root = doctor.ROOT
        doctor.ROOT = self.sandbox

    def tearDown(self):
        doctor.ROOT = self._orig_root
        self._tmp.cleanup()

    def _scan(self, name: str, content: str) -> list[str]:
        target = self.sandbox / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        return errors

    def test_formatted_token_next_to_allowlist_words_is_flagged(self):
        for word in ("commit", "tag", "example", "git "):
            with self.subTest(word=word):
                errors = self._scan("notes.md", f"{word} {_gh_token()}\n")
                self.assertTrue(errors, f"ghp_ junto a '{word}' debe reportarse")

    def test_formatted_token_in_json_with_ref_key_is_flagged(self):
        errors = self._scan("cfg.json", '{"ref": "' + _gh_token() + '"}')
        self.assertTrue(errors, "un ghp_ bajo la clave 'ref' debe reportarse")

    def test_formatted_token_containing_0000_is_flagged(self):
        token = "ghp_" + "A" * 16 + "0000" + "B" * 16
        errors = self._scan("config.py", f"GH = {token}\n")
        self.assertTrue(errors, "un token con '0000' dentro debe reportarse")

    def test_aws_key_next_to_sha_word_is_flagged(self):
        errors = self._scan("notes.md", f"sha1 {_aws_key()}\n")
        self.assertTrue(errors, "AKIA junto a 'sha1' debe reportarse")

    def test_assignment_wrapping_formatted_token_with_example_is_flagged(self):
        line = "api_key" + ' = "' + _gh_token() + '"  # example\n'
        errors = self._scan("config.py", line)
        self.assertTrue(errors, "una asignación con un token con formato no se tolera por 'example'")

    def test_formatted_token_in_settings_local_line_with_commit_is_flagged(self):
        (self.sandbox / ".claude").mkdir()
        (self.sandbox / ".claude" / "settings.local.json").write_text(
            '{"allow": ["Bash(git commit ' + _gh_token() + ')"]}', encoding="utf-8"
        )
        errors: list[str] = []
        doctor.validate_local_settings_safety(errors)
        self.assertTrue(errors, "settings.local.json: ghp_ en línea con 'commit' debe reportarse")

    def test_assignment_with_placeholder_marker_is_not_flagged(self):
        line = "api_key" + ' = "' + "your-example-value-here" + '"\n'
        self.assertEqual(self._scan("config.py", line), [])

    def test_fingerprint_hex_is_not_flagged(self):
        errors = self._scan("docs.md", "mismo fingerprint histórico " + "63d1" + "a" * 60 + "\n")
        self.assertEqual(errors, [])

    def test_campaign_prefixed_hex_is_not_flagged(self):
        errors = self._scan("evidence.json", '{"ids": ["campaign-' + "b" * 64 + '"]}')
        self.assertEqual(errors, [])

    def test_bare_hex_in_arch_decisions_projection_is_not_flagged(self):
        errors = self._scan("_project/03_arch_decisions.md", "V3→V4: " + "c" * 64 + "\n")
        self.assertEqual(errors, [])

    def test_formatted_token_in_arch_decisions_projection_is_flagged(self):
        errors = self._scan("_project/03_arch_decisions.md", "nota " + _aws_key() + "\n")
        self.assertTrue(errors, "la proyección tolera hex desnudo, no credenciales con formato")

    def test_bare_hex_with_commit_context_still_tolerated(self):
        errors = self._scan("notes.md", "commit " + "d" * 40 + "\n")
        self.assertEqual(errors, [])

    def test_pem_header_with_dashes_next_to_example_is_flagged(self):
        pem = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEow\n"
        errors = self._scan("key.txt", "example\n" + pem)
        self.assertTrue(errors, "una cabecera PEM real no se tolera por 'example'")

    def test_pem_pattern_cited_in_prose_is_not_flagged(self):
        errors = self._scan("notes.md", "el gate cita `BEGIN " + "PRIVATE KEY` como patrón\n")
        self.assertEqual(errors, [])

    def test_bare_hex_32_in_arch_decisions_projection_is_not_flagged(self):
        # Cambiado en 5.3.1 L2.1 (D2 del builder): un hexadecimal suelto, de
        # cualquier longitud, no es un secreto sin una clave que lo nombre.
        errors = self._scan("_project/03_arch_decisions.md", "clave del agente " + "e" * 32 + "\n")
        self.assertEqual(errors, [], "un hex suelto en prosa no es un secreto (regla invertida de 5.3.1 L2.1)")
