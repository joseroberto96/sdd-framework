#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contrato de L2 de la 5.3.1: pre-commit y secretos.

L2.1: regla de secretos invertida. Se marca un formato de credencial conocido o
un valor de alta entropía asignado a una clave que nombra un secreto; un
hexadecimal suelto (huella, commit, identificador derivado de hash) no es un
secreto. Si `gitleaks` está instalado, se usa en el modo preparado.

L2.2: el pre-commit mira lo preparado: el escáner solo lee las rutas que el
commit cambia, del índice; la autorreparación solo vuelve a preparar las salidas
del sync; la transición se valida con el `state_log.jsonl` del índice.

L2.3: el hook instalado es un lanzador de una línea que ejecuta el
`pre_commit.sh` del árbol; `install` migra la copia antigua con respaldo y el
doctor avisa si el hook no es el lanzador.

L2.4: `documentary_paths` y `suite_command` en el perfil del proyecto, leídos del
índice, sin editar el script (TL-FW-319, D8).

L2.5: la suite completa corre una vez por árbol: el commit reutiliza un informe
verde cuya huella es la del árbol preparado; el driver la corre una vez tras
integrar con `pre_commit.sh suite` (TL-FW-298, TL-FW-311, FW-358).

Los casos reales están reducidos a su forma, sin datos de los proyectos. Los
secretos de prueba se construyen en ejecución (hashlib y concatenaciones): este
fichero lo escanea el propio doctor de la plantilla y no debe contener ningún
literal con forma de credencial. El origen de cada caso está en la bitácora de
la 5.3.1 (sección L2).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import framework_doctor as doctor  # noqa: E402

_GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
_GIT_ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})


def _h(n: int, seed: str) -> str:
    full = hashlib.sha256(seed.encode()).hexdigest() + hashlib.sha256((seed + "x").encode()).hexdigest()
    return full[:n]


def _gh() -> str:
    return "gh" + "p_" + "Q7rT" * 9


def _aws() -> str:
    return "AK" + "IA" + "Z3QW8RT5YU2IO7PA"


def _b64ish(seed: str, n: int = 32) -> str:
    """Valor de alta entropía con letras y dígitos (no hex)."""
    raw = hashlib.sha256(seed.encode()).digest() + hashlib.sha256((seed + "y").encode()).digest()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(alphabet[b % len(alphabet)] for b in raw)[:n]


def _git(sb: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=sb, capture_output=True, text=True, env=_GIT_ENV, check=check)


def _init_repo(sb: Path) -> None:
    _git(sb, "init", "-q")
    _git(sb, "config", "user.email", "t@t.t")
    _git(sb, "config", "user.name", "t")


def _scan(files: dict, *, staged: bool = False, add: list | None = None, profile: str = "",
          warnings: list | None = None) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        sb = Path(tmp)
        _init_repo(sb)
        if profile:
            (sb / "_project").mkdir()
            (sb / "_project" / "15_environment_profile.md").write_text(profile, encoding="utf-8")
            _git(sb, "add", "_project/15_environment_profile.md")
        for rel_path, text in files.items():
            p = sb / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for rel_path in (files if add is None else add):
            _git(sb, "add", rel_path)
        orig = doctor.ROOT
        doctor.ROOT = sb
        try:
            errors: list = []
            limpio = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            with mock.patch.dict(os.environ, limpio, clear=True):
                doctor.scan_potential_secrets(errors, staged=staged, warnings=warnings)
            return errors
        finally:
            doctor.ROOT = orig


class ReglaDeSecretosInvertidaTests(unittest.TestCase):
    """L2.1: formato conocido o valor con clave; el hex suelto no es secreto."""

    def test_tl_fw_321_manifiesto_json_de_huellas_pasa(self) -> None:
        manifest = json.dumps({"ficheros_sha256": {"escenario_a.json": _h(64, "a"),
                                                   "escenario_b.json": _h(64, "b")}}, indent=2)
        self.assertEqual(_scan({"research/ipn/banco/examen/r3/contenido_sha256.json": manifest}), [])

    def test_pa_fw_012_linea_index_de_patch_y_manifiesto_de_commits_pasan(self) -> None:
        patch = (f"diff --git a/app/x.py b/app/x.py\nindex {_h(40, 'p1')}..{_h(40, 'p2')} 100644\n"
                 "--- a/app/x.py\n+++ b/app/x.py\n")
        manifest = json.dumps({"source_head": _h(40, "s"), "target_head": _h(40, "t"),
                               "product_commit": _h(40, "c")})
        self.assertEqual(_scan({"_project/02_technical_plan/integration.patch": patch,
                                "_project/02_technical_plan/manifest.json": manifest}), [])

    def test_pa_fw_005_provenance_con_huellas_y_url_de_github_con_commit_pasan(self) -> None:
        provenance = json.dumps({"skill_version": "4.5.2",
                                 "artifact_sha256": {"SKILL.md": _h(64, "k"), "scripts/live.js": _h(64, "k2")}})
        setup = f"Fuente: https://github.com/owner/repo/blob/{_h(40, 'u')}/skills/live.md\n"
        self.assertEqual(_scan({".agent/skills/imported/impeccable/PROVENANCE.json": provenance,
                                ".agent/skills/imported/impeccable/live-setup.md": setup}), [])

    def test_tl_fw_305_identificador_lbx_sin_perfil_pasa(self) -> None:
        self.assertEqual(_scan({"tests/unit/test_confirmatory.py": f'EXPECTED_ID = "lbx-{_h(32, "lbx")}"\n'}), [])

    def test_fw_287_manifiesto_tsv_y_formato_sha256sum_pasan(self) -> None:
        tsv = f"{_h(64, 'e1')}\tdata/a.csv\n{_h(64, 'e2')}\tdata/b.csv\n"
        sums = f"{_h(64, 'hu')}  r3_chatgpt/contenido_sha256.json\n"
        self.assertEqual(_scan({"research/evidence/manifest.tsv": tsv,
                                "research/ipn/banco/examen/HUELLAS.txt": sums}), [])

    def test_hex_desnudo_de_32_40_y_64_sin_clave_no_es_secreto(self) -> None:
        for n in (32, 40, 64):
            with self.subTest(n=n):
                files = {
                    "docs/nota.md": f"El valor interno es {_h(n, 'nota')}\n",
                    "src/ids.py": f"IDS = ['{_h(n, 'id')}']\n",
                    "data/lista.txt": f"{_h(n, 'lista')}\n",
                }
                self.assertEqual(_scan(files), [])

    def test_formatos_conocidos_siguen_bloqueando(self) -> None:
        formatos = {
            "ghp_": _gh(),
            "github_pat_": "github" + "_pat_" + "A1b2C3d4E5" * 8 + "Zz",
            "sk-": "s" + "k-" + "proj-" + _b64ish("openai", 40),
            "sk-ant": "s" + "k-ant-api03-" + _b64ish("anthropic", 40),
            "AKIA": _aws(),
            "ASIA": "AS" + "IA" + "Z3QW8RT5YU2IO7PA",
            "xoxb-": "xo" + "xb-" + "1234567890-" + _b64ish("slack", 20),
            "PEM": "-----BEGIN " + "RSA PRIVATE KEY-----",
            "JWT": "ey" + "J" + _b64ish("h", 16) + ".ey" + "J" + _b64ish("p", 20) + "." + _b64ish("s", 24),
            "URL con contraseña": "postgres://admin:" + "aB3dE6gH1j" + "@db.example.invalid:5432/prod",
        }
        for nombre, valor in formatos.items():
            for rel_path, contenido in (
                ("src/cliente.py", f"VALOR = '{valor}'\n"),
                ("docs/nota.md", f"Ejemplo de commit con {valor} dentro\n"),
                ("config/datos.json", json.dumps({"ref": valor})),
            ):
                with self.subTest(formato=nombre, fichero=rel_path):
                    self.assertTrue(_scan({rel_path: contenido}), f"{nombre} en {rel_path} debía bloquear")

    def test_valor_de_alta_entropia_asignado_a_clave_bloquea(self) -> None:
        h40 = _h(40, "y40")
        v = _b64ish("valor")
        casos = {
            "config/app.yml": f"auth:\n  token: {h40}\n",
            "deploy/prod.env": f"API_TOKEN={_h(32, 'e32')}\n",
            "scripts/entorno.sh": f"export GITHUB_TOKEN={v}\n",
            "src/ajustes.py": f"clave_api = {h40}\n",
            "web/config.ts": f"const cfg = {{ apiKey: {v} }}\n",
            "web/cliente.js": f"fetch('https://api.servicio.io/v1?access_token={v}&x=1')\n",
            "config/creds.json": json.dumps({"password": _h(64, "pw")}),
            "src/llamada.py": "headers = {'Authorization': 'Bear" + "er " + v + "'}\n",
            "src/dict.py": '{"pass' + 'word": "' + v + '"}\n',
        }
        for rel_path, contenido in casos.items():
            with self.subTest(fichero=rel_path):
                self.assertTrue(_scan({rel_path: contenido}), f"{rel_path} debía bloquear: {contenido!r}")

    def test_clave_que_nombra_una_huella_o_un_dato_publico_no_es_secreto(self) -> None:
        h64 = _h(64, "huella")
        v = _b64ish("publico")
        casos = {
            "config/a.yml": f"token_sha256: {h64}\n",
            "config/b.yml": f"secret_hash: {h64}\n",
            "config/c.yml": f"auth_id: {v}\n",
            "config/d.yml": f"source_head: {_h(40, 'head')}\n",
            "config/e.yml": f"author: {v}\n",
            "config/f.py": "tokenizer = bert-base-uncased-v12345\n",
            "config/g.json": json.dumps({"tests/test_auth_api.py": h64, "token.json": h64}),
            "config/h.yml": "password_file: /run/secrets/db_password_v2\n",
        }
        for rel_path, contenido in casos.items():
            with self.subTest(fichero=rel_path):
                self.assertEqual(_scan({rel_path: contenido}), [], contenido)

    def test_valor_ficticio_marcado_no_bloquea(self) -> None:
        v = _b64ish("ficticio")
        casos = {
            "config/a.yml": f"token: {v}  # example\n",
            "config/b.env": f"API_TOKEN=placeholder{v}\n",
            "config/c.json": json.dumps({"dummy_password": v}),
            "config/d.yml": "password: changeme1234567890abc\n",
        }
        for rel_path, contenido in casos.items():
            with self.subTest(fichero=rel_path):
                self.assertEqual(_scan({rel_path: contenido}), [], contenido)

    def test_mensaje_nombra_la_regla_y_la_salida_sin_filtrar_el_valor(self) -> None:
        h64 = _h(64, "token-real")
        errs = _scan({"config/app.yml": f"# cabecera\ntoken: {h64}\n"})
        self.assertEqual(len(errs), 1, errs)
        msg = errs[0]
        self.assertIn("config/app.yml:2", msg)
        self.assertIn("token:", msg)
        self.assertNotIn(h64[:8], msg)
        for trozo in ("formato de credencial", "clave que nombra un secreto", "variable de entorno", "example"):
            self.assertIn(trozo, msg)

        errs_json = _scan({"config/creds.json": json.dumps({"db": {"password": h64}})})
        self.assertEqual(len(errs_json), 1, errs_json)
        self.assertIn("clave `password`", errs_json[0])
        self.assertNotIn(h64[:8], errs_json[0])

        gh = _gh()
        errs_gh = _scan({"src/cliente.py": f"# Cabecera\nTOKEN_EXPORTADO = '{gh}'\n"})
        self.assertEqual(len(errs_gh), 1, errs_gh)
        self.assertIn("src/cliente.py:2", errs_gh[0])
        self.assertNotIn(gh[4:16], errs_gh[0])

    def test_asignacion_de_secreto_con_prefijo_lbx_sigue_bloqueando(self) -> None:
        # Movido desde test_l7_contrato.Punto7IdentityPrefixesTests (5.3.1 L2, D10).
        errs = _scan({"tests/test_sample.py": "api" + f'_key = "lbx-{_h(32, "secreto-disfrazado")}"\n'},
                     profile="- `identity_prefixes`: lbx\n")
        self.assertTrue(errs, "api_key = 'lbx-...' es una asignación de credencial y debe bloquear")

    def test_perfil_con_identity_prefixes_ya_no_da_error_ni_aviso(self) -> None:
        warnings: list = []
        for prefijos in ("lbx", "sk", "dead, ghp"):
            with self.subTest(prefijos=prefijos):
                errs = _scan({"docs/nota.md": "nada\n"}, profile=f"- `identity_prefixes`: {prefijos}\n",
                             warnings=warnings)
                self.assertEqual(errs, [])
        self.assertEqual(warnings, [])


def _gitleaks_stub(bin_dir: Path, witness: Path, *, rc: int, report: list | None) -> None:
    body = (
        f"#!{sys.executable}\n"
        "import json, sys, pathlib\n"
        f"pathlib.Path({str(witness)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "args = sys.argv[1:]\n"
        f"report = {report!r}\n"
        "if report is not None and '--report-path' in args:\n"
        "    pathlib.Path(args[args.index('--report-path') + 1]).write_text(json.dumps(report))\n"
        f"sys.exit({rc})\n"
    )
    exe = bin_dir / "gitleaks"
    exe.write_text(body, encoding="utf-8")
    exe.chmod(0o755)


class GitleaksTests(unittest.TestCase):
    """L2.1 (D5): gitleaks, si está en el PATH, solo en el modo preparado."""

    def _run(self, *, staged: bool, rc: int, report: list | None) -> tuple[list, list, Path]:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            witness = Path(tmp) / "argv.json"
            _gitleaks_stub(bin_dir, witness, rc=rc, report=report)
            warnings: list = []
            with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
                errs = _scan({"src/ok.py": "x = 1\n"}, staged=staged, warnings=warnings)
            argv = json.loads(witness.read_text()) if witness.exists() else None
            return errs, warnings, argv

    def test_gitleaks_instalado_se_usa_en_modo_preparado(self) -> None:
        secreto = _gh()
        report = [{"RuleID": "github-pat", "File": "src/ok.py", "StartLine": 3,
                   "Secret": secreto, "Match": "token = " + secreto}]
        errs, warnings, argv = self._run(staged=True, rc=doctor._GITLEAKS_LEAK_RC, report=report)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("gitleaks", errs[0])
        self.assertIn("github-pat", errs[0])
        self.assertIn("src/ok.py:3", errs[0])
        self.assertNotIn(secreto, errs[0])
        self.assertIsNotNone(argv)
        self.assertIn("--staged", argv)
        self.assertIn("--redact", argv)
        self.assertEqual(warnings, [])

    def test_gitleaks_limpio_no_anade_nada(self) -> None:
        errs, warnings, argv = self._run(staged=True, rc=0, report=[])
        self.assertEqual((errs, warnings), ([], []))
        self.assertIsNotNone(argv)

    def test_gitleaks_que_falla_avisa_y_decide_el_escaner_propio(self) -> None:
        for rc in (1, 2, 126):
            with self.subTest(rc=rc):
                errs, warnings, _ = self._run(staged=True, rc=rc, report=None)
                self.assertEqual(errs, [])
                self.assertTrue(any("gitleaks" in w and "escáner propio" in w for w in warnings), warnings)

    def test_gitleaks_con_hallazgos_e_informe_ilegible_bloquea(self) -> None:
        errs, _, _ = self._run(staged=True, rc=doctor._GITLEAKS_LEAK_RC, report=None)
        self.assertTrue(any("gitleaks" in e for e in errs), errs)

    def test_sin_gitleaks_o_fuera_del_commit_no_se_llama(self) -> None:
        errs, warnings, argv = self._run(staged=False, rc=doctor._GITLEAKS_LEAK_RC, report=[])
        self.assertIsNone(argv, "fuera del commit gitleaks no se llama")
        self.assertEqual((errs, warnings), ([], []))
        with mock.patch.object(doctor.shutil, "which", return_value=None):
            warnings2: list = []
            self.assertEqual(_scan({"src/ok.py": "x = 1\n"}, staged=True, warnings=warnings2), [])
            self.assertEqual(warnings2, [])


# --- Sandbox del pre-commit real con stubs (patrón de test_pre_commit_suite_skip) ---

REAL_HOOK = ROOT / ".agent" / "scripts" / "pre_commit.sh"
REAL_TRANSITION = ROOT / ".agent" / "scripts" / "validate_transition.py"
_HOOK_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
_HOOK_ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "FRAMEWORK_PYTHON": ""})


def _exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _hook_sandbox(tmp: Path, *, doctor_body: str | None = None, sync_body: str | None = None,
                  tests: bool = True) -> Path:
    """Repo con el pre_commit.sh real, el validate_transition.py real y stubs
    para el doctor, el gate de la bitácora y el sync. Con `tests`, la suite es
    un test que suma una marca en `tmp/runs` cada vez que corre."""
    sb = tmp / "repo"
    sc = sb / ".agent" / "scripts"
    sc.mkdir(parents=True)
    (sc / "pre_commit.sh").write_bytes(REAL_HOOK.read_bytes())
    _exec(sc / "framework_doctor.py", doctor_body or "import sys\nsys.exit(0)\n")
    _exec(sc / "validate_state_delta.py", "import sys\nsys.exit(0)\n")
    _exec(sc / "sync_claude_commands.py", sync_body or "import sys\nsys.exit(0)\n")
    (sc / "validate_transition.py").write_bytes(REAL_TRANSITION.read_bytes())
    (sc / "_transitions.py").write_bytes((REAL_TRANSITION.parent / "_transitions.py").read_bytes())
    if tests:
        (sb / "tests").mkdir()
        (sb / "tests" / "__init__.py").write_text("")
        (sb / "tests" / "test_marker.py").write_text(
            "import unittest, pathlib\n"
            "class T(unittest.TestCase):\n"
            "    def test_marker(self):\n"
            f"        p = pathlib.Path({str(tmp / 'runs')!r})\n"
            "        p.write_text((p.read_text() if p.exists() else '') + 'x')\n"
        )
    _hgit(sb, "init", "-q")
    _hgit(sb, "config", "user.email", "t@t")
    _hgit(sb, "config", "user.name", "t")
    _hgit(sb, "add", "-A")
    _hgit(sb, "commit", "-qm", "init", "--no-verify")
    return sb


def _hgit(sb: Path, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=sb, capture_output=True, text=True,
                          env=env or _HOOK_ENV, check=check)


def _run_hook(sb: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", ".agent/scripts/pre_commit.sh", *args], cwd=sb, capture_output=True,
                          text=True, env=env or _HOOK_ENV)


def _suite_runs(tmp: Path) -> int:
    p = tmp / "runs"
    return len(p.read_text()) if p.exists() else 0


def _doctor_que_pide_sync(flag: Path) -> str:
    """Doctor stub: la primera llamada sale 2 (deriva de wrappers), las demás 0."""
    return (
        "import sys, pathlib\n"
        f"p = pathlib.Path({str(flag)!r}); n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1)); sys.exit(2 if n == 0 else 0)\n"
    )


class PreCommitMiraLoPreparadoTests(unittest.TestCase):
    """L2.2: el pre-commit mira lo preparado, no el árbol (TL-FW-315, PA-FW-005)."""

    def _repo(self, tmp: Path) -> Path:
        sb = tmp / "repo"
        sb.mkdir()
        _init_repo(sb)
        (sb / "base.md").write_text("base\n")
        _git(sb, "add", "base.md")
        _git(sb, "commit", "-qm", "init")
        return sb

    def _scan_at(self, sb: Path, env_extra: dict | None = None) -> list:
        orig = doctor.ROOT
        doctor.ROOT = sb
        try:
            errors: list = []
            limpio = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            limpio.update(env_extra or {})
            with mock.patch.dict(os.environ, limpio, clear=True):
                doctor.scan_potential_secrets(errors, staged=True)
            return errors
        finally:
            doctor.ROOT = orig

    def test_fichero_no_preparado_no_se_escanea_en_modo_preparado(self) -> None:
        # PA-FW-005c: excluir un fichero del commit basta.
        errs = _scan({"_project/no_preparado.py": f"GH = '{_gh()}'\n", "src/ok.py": "x = 1\n"},
                     staged=True, add=["src/ok.py"])
        self.assertEqual(errs, [])

    def test_fichero_versionado_que_el_commit_no_toca_no_se_escanea_en_modo_preparado(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sb = self._repo(Path(t))
            (sb / "viejo.py").write_text(f"GH = '{_gh()}'\n")
            _git(sb, "add", "viejo.py")
            _git(sb, "commit", "-qm", "historia", "--no-verify")
            (sb / "nuevo.md").write_text("nada\n")
            _git(sb, "add", "nuevo.md")
            self.assertEqual(self._scan_at(sb), [])
            # Fuera del commit sí se escanea lo versionado.
            orig = doctor.ROOT
            doctor.ROOT = sb
            try:
                errors: list = []
                doctor.scan_potential_secrets(errors)
            finally:
                doctor.ROOT = orig
            self.assertTrue(errors)

    def test_commit_a_usa_el_indice_temporal_de_git(self) -> None:
        # `git commit -a` prepara en un índice temporal (GIT_INDEX_FILE): se escanea ese.
        with tempfile.TemporaryDirectory() as t:
            sb = self._repo(Path(t))
            (sb / "base.md").write_text(f"base\nGH = {_gh()}\n")
            self.assertEqual(self._scan_at(sb), [], "con el índice real, el cambio no está preparado")
            tmp_index = Path(t) / "index.tmp"
            tmp_index.write_bytes((sb / ".git" / "index").read_bytes())
            env = {**_GIT_ENV, "GIT_INDEX_FILE": str(tmp_index)}
            subprocess.run(["git", "add", "-u"], cwd=sb, env=env, check=True, capture_output=True)
            errs = self._scan_at(sb, {"GIT_INDEX_FILE": str(tmp_index)})
            self.assertTrue(any("base.md:2" in e for e in errs), errs)

    def _estado(self, tmp: Path, *, preparar_log: bool) -> subprocess.CompletedProcess:
        sb = _hook_sandbox(tmp, tests=False)
        pj = sb / "_project"
        pj.mkdir()
        (pj / "03_project_state.json").write_text(json.dumps({"module_statuses": {"M": "in_progress"}}))
        (pj / "state_log.jsonl").write_text("")
        _hgit(sb, "add", "-A")
        _hgit(sb, "commit", "-qm", "s", "--no-verify")
        (pj / "03_project_state.json").write_text(json.dumps({"module_statuses": {"M": "completed"}}))
        (pj / "state_log.jsonl").write_text(json.dumps({"action": "complete_module", "payload": {"id": "M"}}) + "\n")
        _hgit(sb, "add", "_project/03_project_state.json")
        if preparar_log:
            _hgit(sb, "add", "_project/state_log.jsonl")
        return _run_hook(sb)

    def test_transicion_se_valida_con_el_log_del_indice(self) -> None:
        # TL-FW-315b: el evento que respalda el salto está en el log del árbol, sin preparar.
        with tempfile.TemporaryDirectory() as t:
            res = self._estado(Path(t), preparar_log=False)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("transición de estado de módulo ilegal", res.stdout)

    def test_transicion_respaldada_por_el_log_preparado_pasa(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            res = self._estado(Path(t), preparar_log=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def _sandbox_sync(self, tmp: Path, sync_body: str | None = None) -> Path:
        flag = tmp / "doctor_calls"
        sb = _hook_sandbox(tmp, doctor_body=_doctor_que_pide_sync(flag), sync_body=sync_body, tests=False)
        for d in (".claude/commands", ".claude/agents", ".opencode/commands", ".agents/skills"):
            (sb / d).mkdir(parents=True, exist_ok=True)
            (sb / d / "w.md").write_text("w\n")
        for f in ("AGENTS.md", "CLAUDE.md", "llms.txt", "README.md"):
            (sb / f).write_text("base\n")
        _hgit(sb, "add", "-A")
        _hgit(sb, "commit", "-qm", "r", "--no-verify")
        flag.unlink(missing_ok=True)
        return sb

    def test_autorreparacion_no_prepara_readme_ni_llms(self) -> None:
        # TL-FW-315a: README.md y llms.txt no son salidas del sync.
        with tempfile.TemporaryDirectory() as t:
            sb = self._sandbox_sync(Path(t))
            (sb / "README.md").write_text("base\nedición del builder sin preparar\n")
            (sb / "llms.txt").write_text("base\notra edición\n")
            (sb / "docs.md").write_text("x\n")
            _hgit(sb, "add", "docs.md")
            res = _run_hook(sb)
            staged = _hgit(sb, "diff", "--cached", "--name-only").stdout.split()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("README.md", staged)
        self.assertNotIn("llms.txt", staged)

    def test_autorreparacion_aborta_si_una_salida_del_sync_tiene_cambios_sin_preparar(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sb = self._sandbox_sync(Path(t))
            (sb / "CLAUDE.md").write_text("base\nsección del proyecto sin preparar\n")
            (sb / ".claude" / "commands" / "propio.md").write_text("comando propio sin preparar\n")
            (sb / "docs.md").write_text("x\n")
            _hgit(sb, "add", "docs.md")
            res = _run_hook(sb)
            staged = _hgit(sb, "diff", "--cached", "--name-only").stdout.split()
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("CLAUDE.md", res.stdout)
        self.assertIn(".claude/commands/propio.md", res.stdout)
        self.assertIn("sync_claude_commands.py", res.stdout)
        self.assertEqual(staged, ["docs.md"])

    def test_autorreparacion_prepara_las_salidas_aunque_falte_alguna_ruta(self) -> None:
        # TL-FW-315c: sin .claude/rules ni GEMINI.md, `git add` no aborta entero.
        sync = (
            "import pathlib\n"
            "pathlib.Path('.claude/commands/w.md').write_text('regenerado\\n')\n"
            "pathlib.Path('GEMINI.md').write_text('nuevo\\n')\n"
        )
        with tempfile.TemporaryDirectory() as t:
            sb = self._sandbox_sync(Path(t), sync_body=sync)
            (sb / "docs.md").write_text("x\n")
            _hgit(sb, "add", "docs.md")
            res = _run_hook(sb)
            staged = _hgit(sb, "diff", "--cached", "--name-only").stdout.split()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn(".claude/commands/w.md", staged)
        self.assertIn("GEMINI.md", staged)


def _launcher(sb: Path) -> str:
    res = _run_hook(sb, "print-launcher")
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def _hook_path(sb: Path) -> Path:
    out = _hgit(sb, "rev-parse", "--git-path", "hooks/pre-commit").stdout.strip()
    return Path(out) if os.path.isabs(out) else sb / out


class HookLanzadorTests(unittest.TestCase):
    """L2.3: el hook instalado es un lanzador del pre_commit.sh del árbol (TL-FW-320, FW-273)."""

    def test_install_escribe_el_lanzador_y_el_commit_ejecuta_el_script_del_arbol(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp, tests=False)
            res = _run_hook(sb, "install")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            hook = _hook_path(sb)
            self.assertEqual(hook.read_text(encoding="utf-8"), _launcher(sb))
            self.assertTrue(os.access(hook, os.X_OK))
            self.assertLessEqual(len(hook.read_text().splitlines()), 3)
            # TL-FW-320: se edita el script del árbol y el commit ejecuta la versión nueva.
            script = sb / ".agent" / "scripts" / "pre_commit.sh"
            marca = tmp / "marca"
            script.write_text(script.read_text().replace(
                "set -o pipefail\n", f"set -o pipefail\necho MARCA >> {marca}\n", 1))
            _hgit(sb, "add", "-A")
            commit = _hgit(sb, "commit", "-qm", "cambio del script", check=False)
            self.assertEqual(commit.returncode, 0, commit.stdout + commit.stderr)
            self.assertTrue(marca.exists(), "el commit debía ejecutar el pre_commit.sh del árbol")

    def test_install_migra_la_copia_antigua_con_respaldo(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sb = _hook_sandbox(Path(t), tests=False)
            hook = _hook_path(sb)
            hook.parent.mkdir(parents=True, exist_ok=True)
            antigua = "#!/usr/bin/env bash\n# Framework v3 pre-commit hook\n# copia instalada antes de 5.3.1\nexit 0\n"
            hook.write_text(antigua)
            res = _run_hook(sb, "install")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(hook.read_text(), _launcher(sb))
            respaldos = list(hook.parent.glob("pre-commit.bak-*"))
            self.assertEqual(len(respaldos), 1, respaldos)
            self.assertEqual(respaldos[0].read_text(), antigua)
            # Una segunda instalación no hace nada (ni otro respaldo).
            res2 = _run_hook(sb, "install")
            self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
            self.assertIn("ya está instalado", res2.stdout)
            self.assertEqual(len(list(hook.parent.glob("pre-commit.bak-*"))), 1)

    def test_install_no_pisa_un_hook_propio(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sb = _hook_sandbox(Path(t), tests=False)
            hook = _hook_path(sb)
            hook.parent.mkdir(parents=True, exist_ok=True)
            propio = "#!/bin/sh\n# hook del proyecto\nexit 0\n"
            hook.write_text(propio)
            res = _run_hook(sb, "install")
            self.assertEqual(res.returncode, 1)
            self.assertEqual(hook.read_text(), propio)

    def test_install_desde_un_worktree_escribe_el_lanzador_compartido(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp, tests=False)
            wt = tmp / "carril con espacios"
            _hgit(sb, "worktree", "add", "-q", "-b", "carril", str(wt))
            res = _run_hook(wt, "install")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            hook = sb / ".git" / "hooks" / "pre-commit"
            self.assertEqual(hook.read_text(), _launcher(sb))
            # El commit del worktree ejecuta el script de SU árbol.
            marca = tmp / "marca_wt"
            script = wt / ".agent" / "scripts" / "pre_commit.sh"
            script.write_text(script.read_text().replace(
                "set -o pipefail\n", f"set -o pipefail\necho WT >> '{marca}'\n", 1))
            _hgit(wt, "add", "-A")
            commit = _hgit(wt, "commit", "-qm", "en el carril", check=False)
            self.assertEqual(commit.returncode, 0, commit.stdout + commit.stderr)
            self.assertTrue(marca.exists())

    def test_lanzador_sin_script_en_el_arbol_bloquea_con_mensaje_propio(self) -> None:
        # D9 y FW-273 (a): sin .agent/scripts/pre_commit.sh, el commit se bloquea con un mensaje propio.
        with tempfile.TemporaryDirectory() as t:
            sb = _hook_sandbox(Path(t), tests=False)
            self.assertEqual(_run_hook(sb, "install").returncode, 0)
            _hgit(sb, "rm", "-q", "--cached", ".agent/scripts/pre_commit.sh")
            (sb / ".agent" / "scripts" / "pre_commit.sh").unlink()
            commit = _hgit(sb, "commit", "-qm", "sin script", check=False)
            self.assertNotEqual(commit.returncode, 0)
            self.assertIn("falta", commit.stdout + commit.stderr)
            self.assertIn("pre_commit.sh", commit.stdout + commit.stderr)

    def test_doctor_no_avisa_con_el_lanzador_y_avisa_con_la_copia_antigua(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sb = _hook_sandbox(Path(t), tests=False)
            hook = _hook_path(sb)
            hook.parent.mkdir(parents=True, exist_ok=True)
            orig = doctor.ROOT
            doctor.ROOT = sb
            try:
                hook.write_text(_launcher(sb))
                warnings: list = []
                doctor.validate_precommit_hook_installed(warnings)
                self.assertEqual(warnings, [])
                hook.write_text((sb / ".agent" / "scripts" / "pre_commit.sh").read_text())
                warnings = []
                doctor.validate_precommit_hook_installed(warnings)
                self.assertTrue(any("TL-FW-320" in w and "pre_commit.sh install" in w for w in warnings), warnings)
                hook.write_text("#!/bin/sh\nexit 0\n")
                warnings = []
                doctor.validate_precommit_hook_installed(warnings)
                self.assertTrue(any("difiere" in w for w in warnings), warnings)
            finally:
                doctor.ROOT = orig


def _con_perfil(sb: Path, lineas: str) -> None:
    pj = sb / "_project"
    pj.mkdir(exist_ok=True)
    (pj / "15_environment_profile.md").write_text(lineas, encoding="utf-8")
    _hgit(sb, "add", "-A")
    _hgit(sb, "commit", "-qm", "perfil", "--no-verify")


def _preparar(sb: Path, ficheros: dict) -> None:
    for rel_path, texto in ficheros.items():
        p = sb / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texto, encoding="utf-8")
        _hgit(sb, "add", rel_path)


class RutasDocumentalesDelPerfilTests(unittest.TestCase):
    """L2.4: `documentary_paths` y `suite_command` en el perfil (TL-FW-319, D8)."""

    PERFIL = "- `documentary_paths`: docs/ipn/*, research/ipn/*\n"

    def test_ruta_documental_declarada_no_lanza_la_suite(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp)
            _con_perfil(sb, self.PERFIL)
            _preparar(sb, {"docs/ipn/nota.md": "x\n", "research/ipn/banco.py": "x = 1\n"})
            res = _run_hook(sb)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(_suite_runs(tmp), 0)
            self.assertIn("rutas documentales del perfil", res.stdout)

    def test_mezcla_de_ruta_declarada_y_codigo_lanza_la_suite(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp)
            _con_perfil(sb, self.PERFIL)
            _preparar(sb, {"docs/ipn/nota.md": "x\n", "src/app.py": "x = 1\n"})
            res = _run_hook(sb)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(_suite_runs(tmp), 1)

    def test_ruta_documental_invalida_se_ignora_y_se_avisa(self) -> None:
        malas = ["*", "*.py", "../x/*", "/abs/*", ".agent/*", "tests/*", "do cs/*", "docs/$(id)/*"]
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp)
            _con_perfil(sb, "- `documentary_paths`: " + ", ".join(malas) + "\n")
            _preparar(sb, {"src/app.py": "x = 1\n", "tests/test_b.py": "\n", "docs/x.md": "y\n"})
            res = _run_hook(sb)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(_suite_runs(tmp), 1, "ninguna ruta inválida debe omitir la suite")
            for mala in malas:
                if " " in mala:
                    continue  # se parte por comas; el aviso nombra el patrón entero
                self.assertIn(f"documentary_paths ignora '{mala}'", res.stdout)
            self.assertIn("documentary_paths ignora 'do cs/*'", res.stdout)

    def test_perfil_se_lee_del_indice_no_del_arbol(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp)
            _con_perfil(sb, "- `fw_prefix`: XX\n")
            # El perfil del árbol declara la ruta, pero sin preparar: no cuenta.
            (sb / "_project" / "15_environment_profile.md").write_text(self.PERFIL)
            _preparar(sb, {"docs/ipn/nota.md": "x\n"})
            res = _run_hook(sb)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(_suite_runs(tmp), 1)

    def test_plantilla_del_perfil_declara_documentary_paths_y_suite_command(self) -> None:
        text = (ROOT / "_project" / "15_environment_profile.md").read_text(encoding="utf-8")
        self.assertIn("- `documentary_paths`: [", text)
        self.assertIn("- `suite_command`: [", text)
        self.assertNotIn("identity_prefixes", text)

    def _suite_cmd(self, tmp: Path, comando: str, script: dict, *, tests: bool = False) -> subprocess.CompletedProcess:
        sb = _hook_sandbox(tmp, tests=tests)
        for rel_path, (texto, ejecutable) in script.items():
            p = sb / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(texto, encoding="utf-8")
            if ejecutable:
                p.chmod(0o755)
        _con_perfil(sb, f"- `suite_command`: {comando}\n")
        _preparar(sb, {"src/app.py": "x = 1\n"})
        return _run_hook(sb)

    def test_suite_command_del_perfil_sustituye_al_runner(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            testigo = tmp / "suite_propia"
            py = (f"import sys, pathlib\npathlib.Path({str(testigo)!r}).write_text(' '.join(sys.argv[1:]))\n", False)
            res = self._suite_cmd(tmp, "scripts/validate_project.py --profile full --workers 4",
                                  {"scripts/validate_project.py": py}, tests=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(testigo.read_text(), "--profile full --workers 4")
            self.assertEqual(_suite_runs(tmp), 0, "con suite_command no corre el runner por defecto")
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            testigo = tmp / "suite_sh"
            sh = (f"#!/usr/bin/env bash\necho \"$@\" > '{testigo}'\n", True)
            res = self._suite_cmd(tmp, "scripts/suite.sh rapido", {"scripts/suite.sh": sh})
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(testigo.read_text().strip(), "rapido")

    def test_suite_command_en_rojo_bloquea(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            res = self._suite_cmd(Path(t), "scripts/suite.py", {"scripts/suite.py": ("import sys\nprint('ROJO')\nsys.exit(5)\n", False)})
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("ROJO", res.stdout)

    def test_suite_command_fuera_del_repo_o_con_puntos_se_rechaza(self) -> None:
        casos = {
            "/bin/true": "ruta absoluta",
            "../fuera/suite.sh": "..",
            "scripts/no_existe.sh": "no existe",
            "true": "no existe",
            "scripts/sin_x.sh": "no es ejecutable",
        }
        for comando, motivo in casos.items():
            with self.subTest(comando=comando), tempfile.TemporaryDirectory() as t:
                res = self._suite_cmd(Path(t), comando, {"scripts/sin_x.sh": ("#!/bin/sh\nexit 0\n", False)})
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn("suite_command", res.stdout)
                self.assertIn(motivo, res.stdout)


class SuiteUnaVezPorArbolTests(unittest.TestCase):
    """L2.5: la suite completa corre una vez por árbol (TL-FW-298, TL-FW-311, FW-358)."""

    def _verde(self, tmp: Path) -> Path:
        sb = _hook_sandbox(tmp)
        _preparar(sb, {"src/app.py": "x = 1\n"})
        return sb

    def test_informe_verde_del_mismo_arbol_se_reutiliza(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = self._verde(tmp)
            r1 = _run_hook(sb)
            r2 = _run_hook(sb)
            self.assertEqual((r1.returncode, r2.returncode), (0, 0), r1.stdout + r2.stdout)
            self.assertEqual(_suite_runs(tmp), 1)
            self.assertIn("suite reutilizada", r2.stdout)

    def test_informe_verde_de_otro_arbol_no_se_reutiliza(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = self._verde(tmp)
            self.assertEqual(_run_hook(sb).returncode, 0)
            _preparar(sb, {"src/otro.py": "y = 2\n"})
            r2 = _run_hook(sb)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertEqual(_suite_runs(tmp), 2)
            self.assertNotIn("suite reutilizada", r2.stdout)

    def test_suite_roja_bloquea_el_commit_y_no_deja_informe(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp)
            _preparar(sb, {"tests/test_rojo.py": "import unittest\nclass R(unittest.TestCase):\n"
                                                  "    def test_r(self):\n        self.fail('rojo')\n"})
            r1 = _run_hook(sb)
            r2 = _run_hook(sb)
            informe = Path(_hgit(sb, "rev-parse", "--git-path", "framework-suite-green").stdout.strip())
            informe = informe if informe.is_absolute() else sb / informe
            self.assertEqual((r1.returncode, r2.returncode), (1, 1))
            self.assertIn("tests failed", r1.stdout)
            self.assertFalse(informe.exists())
            self.assertEqual(_suite_runs(tmp), 2)

    def test_no_se_registra_informe_si_el_arbol_difiere_de_lo_preparado(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = self._verde(tmp)
            (sb / "src" / "sin_preparar.py").write_text("z = 3\n")
            self.assertEqual(_run_hook(sb).returncode, 0)
            self.assertEqual(_run_hook(sb).returncode, 0)
            self.assertEqual(_suite_runs(tmp), 2)

    def test_otro_python_no_reutiliza_el_informe(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = self._verde(tmp)
            self.assertEqual(_run_hook(sb).returncode, 0)
            otro = {**_HOOK_ENV, "FRAMEWORK_PYTHON": sys.executable}
            self.assertEqual(_run_hook(sb, env=otro).returncode, 0)
            self.assertEqual(_suite_runs(tmp), 2)
            self.assertEqual(_run_hook(sb, env=otro).returncode, 0)
            self.assertEqual(_suite_runs(tmp), 2)

    def test_subcomando_suite_deja_un_informe_que_el_commit_reutiliza(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp)
            (sb / "src").mkdir()
            (sb / "src" / "app.py").write_text("x = 1\n")
            res = _run_hook(sb, "suite")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(_suite_runs(tmp), 1)
            _hgit(sb, "add", "-A")
            r = _run_hook(sb)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(_suite_runs(tmp), 1)
            self.assertIn("suite reutilizada", r.stdout)

    def test_subcomando_suite_en_rojo_sale_1_y_no_deja_informe(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp)
            (sb / "tests" / "test_rojo.py").write_text(
                "import unittest\nclass R(unittest.TestCase):\n    def test_r(self):\n        self.fail('rojo')\n")
            res = _run_hook(sb, "suite")
            self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
            _hgit(sb, "add", "-A")
            self.assertEqual(_run_hook(sb).returncode, 1)
            self.assertEqual(_suite_runs(tmp), 2)

    def test_executor_y_ciclo_dicen_quien_corre_la_suite_completa(self) -> None:
        executor = " ".join((ROOT / ".agent" / "subagents" / "executor.md").read_text(encoding="utf-8").split())
        self.assertIn("nunca la suite completa", executor)
        self.assertIn("FW-358", executor)
        ciclo = (ROOT / ".agent" / "workflows" / "ciclo.md").read_text(encoding="utf-8")
        self.assertIn("pre_commit.sh suite", ciclo)


class RevisionSecurityDeL2Tests(unittest.TestCase):
    """Hallazgos de la revisión `security` del lote, reproducidos antes de corregirlos."""

    def test_linea_larga_se_escanea_en_tiempo_lineal(self) -> None:
        import time
        inicio = time.monotonic()
        self.assertEqual(_scan({"data/blob.txt": "a" * 200000 + "\n", "data/b.txt": "a.b-" * 50000 + "\n"}), [])
        self.assertLess(time.monotonic() - inicio, 5)

    def test_asignacion_partida_en_dos_lineas_bloquea(self) -> None:
        v = _b64ish("partida", 18)
        for rel_path, contenido in {"web/config.js": f'const apiToken =\n  "{v}";\n',
                                    "c.yml": f'password:\n  "{v}"\n'}.items():
            with self.subTest(fichero=rel_path):
                self.assertTrue(_scan({rel_path: contenido}))
        # Una línea que acaba en «:» sin clave de secreto no se une a la siguiente.
        self.assertEqual(_scan({"tests/test_x.py": "def test_a(self):\n    secret = \"token-super-secreto\"\n"
                                                   "    self.assertNotIn(secret, x)\n"}), [])

    def test_asignacion_con_tipo_o_subindice_bloquea(self) -> None:
        casos = {
            "s.py": f'API_TOKEN: str = "{_h(40, "a")}"\n',
            "s.ts": f'const apiKey: string = "{_h(32, "b")}"\n',
            "s2.py": f'os.environ["API_TOKEN"] = "{_h(40, "c")}"\n',
            "s3.py": f"cfg['password'] = '{_h(32, 'd')}'\n",
        }
        for rel_path, contenido in casos.items():
            with self.subTest(fichero=rel_path):
                self.assertTrue(_scan({rel_path: contenido}))

    def test_la_clave_solo_se_niega_por_su_ultima_parte_y_las_cifras_no_la_esconden(self) -> None:
        bloquean = {
            "a.env": f"URL_SIGNING_SECRET={_h(64, 'e')}\n",
            "b.env": f"FILE_ENCRYPTION_PASSWORD={_h(32, 'f')}\n",
            "c.properties": f"aws.secret.key={_h(40, 'g')}\n",
            "d.env": f"DB_PASSWORD2={_h(32, 'i')}\n",
        }
        for rel_path, contenido in bloquean.items():
            with self.subTest(fichero=rel_path):
                self.assertTrue(_scan({rel_path: contenido}))
        pasan = {
            "e.yml": f"token_sha256: {_h(64, 'j')}\n",
            "f.yml": f"secret_hash2: {_h(64, 'k')}\n",
            "g.json": json.dumps({"token.json": _h(64, "l")}),
        }
        for rel_path, contenido in pasan.items():
            with self.subTest(fichero=rel_path):
                self.assertEqual(_scan({rel_path: contenido}), [])

    def test_texto_de_los_valores_json_se_mira_con_la_regla_por_clave(self) -> None:
        v = _b64ish("json-valor")
        casos = {
            ".mcp.json": json.dumps({"headers": {"Authorization": "Bear" + "er " + v}}),
            "x.json": json.dumps({"args": ["--api-key=" + v]}),
            "y.json": json.dumps({"cmd": "export GITHUB_TOKEN=" + v}),
        }
        for rel_path, contenido in casos.items():
            with self.subTest(fichero=rel_path):
                self.assertTrue(_scan({rel_path: contenido}))

    def test_ruta_que_empieza_por_cifra_y_dos_puntos_se_lee_del_indice(self) -> None:
        self.assertTrue(_scan({"0:creds.py": f"GH = '{_gh()}'\n"}, staged=True))

    def test_enmascarado_fusiona_tramos_solapados(self) -> None:
        linea = ("Authorization: Bear" + "er ey" + "J" + "a" * 12 + ".ey" + "J" + "b" * 12 + "." + "c" * 12
                 + ".p4p4p4.p5p5p5")
        errs = _scan({"n.md": linea + "\n"})
        self.assertTrue(errs)
        self.assertNotIn("p5p5p5", errs[0])
        self.assertNotIn("p4p4p4", errs[0])

    def test_marcador_ficticio_de_valor_no_afloja_la_regla_1(self) -> None:
        # «changeme», «xxxx» y ceros solo cuentan dentro del valor.
        self.assertTrue(_scan({"a.js": "{api" + 'Token: "' + _b64ish("r1") + '", max: 100000000}\n'}))

    def test_subcomando_suite_corre_desde_la_raiz(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            sb = _hook_sandbox(tmp)
            (sb / "tests" / "test_rojo.py").write_text(
                "import unittest\nclass R(unittest.TestCase):\n    def test_r(self):\n        self.fail('rojo')\n")
            (sb / "sub" / "tests").mkdir(parents=True)
            (sb / "sub" / "tests" / "test_v.py").write_text("import unittest\nclass V(unittest.TestCase):\n"
                                                            "    def test_v(self):\n        pass\n")
            res = subprocess.run(["bash", "../.agent/scripts/pre_commit.sh", "suite"], cwd=sb / "sub",
                                 capture_output=True, text=True, env=_HOOK_ENV)
            self.assertEqual(res.returncode, 1, res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
