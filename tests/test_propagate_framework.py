"""Tests del detector de deriva de propagación (FW-169).

La deriva plantilla→proyectos era invisible: el único aviso de atraso del doctor
compara `state.framework_version` con `manifest.framework_version`, y ambos
viajan en la misma copia, así que en un proyecto sin actualizar coinciden. Estos
tests fijan el comportamiento del detector que sí la ve.
"""
import contextlib
import importlib.util
import inspect
import io
import json
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agent" / "scripts" / "propagate_framework.py"


def load_module():
    spec = importlib.util.spec_from_file_location("propagate_framework", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_project(root: Path, name: str, version: str, git: bool = True) -> Path:
    project = root / name
    (project / "_project").mkdir(parents=True)
    (project / ".agent" / "workflows").mkdir(parents=True)
    (project / ".agent" / "scripts").mkdir(parents=True)
    (project / "_project" / "03_project_state.json").write_text(
        json.dumps({"framework_version": version}), encoding="utf-8"
    )
    (project / ".agent" / "workflows" / "manifest.json").write_text(
        json.dumps({"framework_version": version}), encoding="utf-8"
    )
    if git:
        subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
        # FW-198: identidad LOCAL al fixture. Sin esto, cualquier código bajo
        # prueba que llame a `git commit` depende de la identidad del entorno:
        # en un portátil existe, en el runner de Actions no
        # (`fatal: empty ident name`). Se configura en la fábrica, no test a
        # test, para que ningún fixture futuro pueda volver a heredarla.
        subprocess.run(["git", "-C", str(project), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(project), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "init"], check=True,
        )
    return project


@contextlib.contextmanager
def plantilla_con_release(version: str, contenido: str):
    """Plantilla sintética con un commit `release: <version>` propio.

    Los tests NO pueden depender del historial del repo real: `actions/checkout`
    clona en profundidad 1, así que en CI no existe ningún commit de release y
    los tests pasaban en local y fallaban en Actions (FW-183)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plantilla"
        (root / ".agent" / "scripts").mkdir(parents=True)
        (root / ".agent" / "scripts" / "state_core.py").write_text(contenido, encoding="utf-8")
        (root / ".agent" / "workflows").mkdir(parents=True)
        (root / ".agent" / "workflows" / "manifest.json").write_text(
            json.dumps({"framework_version": version}), encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", f"release: {version} -- sintetico"], check=True,
        )
        yield root


def commit_all(project: Path) -> None:
    """El parche debe quedar commiteado: un árbol sucio es OTRO bloqueo y
    enmascararía lo que el test quiere comprobar."""
    subprocess.run(["git", "-C", str(project), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "parche"], check=True,
    )


class PropagateFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.exists())

    def test_discovers_initialized_projects_and_skips_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_project(root, "alpha", "4.20.0")
            make_project(root, "beta", "4.20.0")
            found = {p.name for p in self.mod.discover([root])}

            self.assertEqual({"alpha", "beta"}, found)

    def test_project_behind_template_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.20.0")

            report = self.mod.inspect(project, "4.24.5")

            self.assertTrue(report["behind"])
            self.assertEqual("limpio", report["git"])

    def test_project_at_template_version_is_not_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.5")

            self.assertFalse(self.mod.inspect(project, "4.24.5")["behind"])

    def test_dirty_tree_blocks_upgrade(self):
        """`/actualizar-framework` es tier_2 y exige árbol limpio o backup."""
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.20.0")
            (project / "sucio.txt").write_text("x", encoding="utf-8")

            report = self.mod.inspect(project, "4.24.5")

            self.assertTrue(report["git"].startswith("sucio"))
            self.assertTrue(any("sucio" in b for b in report["blockers"]), report["blockers"])

    def test_missing_git_blocks_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.20.0", git=False)

            report = self.mod.inspect(project, "4.24.5")

            self.assertEqual("sin-git", report["git"])
            self.assertTrue(report["blockers"])

    def test_state_manifest_mismatch_blocks_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.20.0")
            (project / ".agent" / "workflows" / "manifest.json").write_text(
                json.dumps({"framework_version": "4.21.0"}), encoding="utf-8"
            )

            blockers = self.mod.inspect(project, "4.24.5")["blockers"]

            self.assertTrue(any("state=" in b for b in blockers), blockers)

    def test_local_patch_blocks_upgrade(self):
        """FW-040: un upgrade que pisa un parche local es una regresión silenciosa."""
        version = "4.24.0"
        original = self.mod.ROOT
        with plantilla_con_release(version, "# original de la plantilla\n") as plantilla:
            try:
                self.mod.ROOT = plantilla
                with tempfile.TemporaryDirectory() as tmp:
                    project = make_project(Path(tmp), "alpha", version)
                    (project / ".agent" / "scripts" / "state_core.py").write_text(
                        "# parche local del producto\n", encoding="utf-8"
                    )

                    report = self.mod.inspect(project, "4.25.0")

                    self.assertIn("state_core.py", report["local_patches"])
                    self.assertTrue(any("parches locales" in b for b in report["blockers"]))
            finally:
                self.mod.ROOT = original

    def test_sin_release_de_referencia_se_declara_indeterminado_y_bloquea(self):
        """FW-179: devolver [] se leería como "sin parches", que es afirmar lo
        que no se sabe. Un detector que existe para evitar pérdidas silenciosas
        no puede él mismo callar una incertidumbre."""
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "0.0.1")
            (project / ".agent" / "scripts" / "state_core.py").write_text("x", encoding="utf-8")

            report = self.mod.inspect(project, "4.24.5")

            self.assertEqual([self.mod.INDETERMINATE], report["local_patches"])
            self.assertTrue(
                any("imposible saber" in b for b in report["blockers"]), report["blockers"]
            )

    def test_patches_upstreamed_desbloquea_solo_al_proyecto_nombrado(self):
        """Tras retroportar los parches de un proyecto a la plantilla, el
        detector no puede saberlo por sí solo: sigue viendo diff contra la
        versión declarada. El reconocimiento es explícito, por proyecto, y no
        debilita el gate para los demás."""
        version = "4.24.0"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("alpha", "beta"):
                project = make_project(root, name, version)
                (project / ".agent" / "scripts" / "state_core.py").write_text(
                    "# parche local\n", encoding="utf-8"
                )
                commit_all(project)

            reports = {
                r["project"]: r
                for r in (
                    self.mod.inspect(p, "4.24.5", frozenset({"alpha"}))
                    for p in self.mod.discover([root])
                )
            }

            self.assertTrue(reports["alpha"]["patches_upstreamed"])
            self.assertEqual([], reports["alpha"]["blockers"])
            self.assertFalse(reports["beta"]["patches_upstreamed"])
            self.assertTrue(
                any("parches locales" in b for b in reports["beta"]["blockers"]),
                reports["beta"]["blockers"],
            )

    def test_parche_ya_incorporado_a_la_plantilla_no_bloquea(self):
        """FW-1xx: el comparador no sabía que un parche ya estaba upstream.

        Caso del plan: el proyecto añade `X` cerca del principio; la plantilla
        actual (evolucionada tras la release) ya trae `X` en el mismo sitio Y
        ADEMÁS `D` al final, en una región separada por líneas sin cambios.
        El merge de tres vías resuelve sin conflicto (las dos regiones no se
        solapan) y el resultado coincide con la plantilla: el parche ya está
        incorporado, no debe bloquear."""
        version = "4.24.0"
        base = "a\nb\nc\nd\ne\n"
        with plantilla_con_release(version, base) as plantilla:
            # La plantilla AVANZA tras la release: incorpora X (el parche del
            # proyecto) y además D (algo más, ajeno al proyecto, en otra región).
            script = plantilla / ".agent" / "scripts" / "state_core.py"
            script.write_text("a\nX\nb\nc\nd\ne\nD\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(plantilla), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(plantilla), "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "feat: añade D sobre X"], check=True,
            )

            original = self.mod.ROOT
            try:
                self.mod.ROOT = plantilla
                with tempfile.TemporaryDirectory() as tmp:
                    project = make_project(Path(tmp), "alpha", version)
                    (project / ".agent" / "scripts" / "state_core.py").write_text(
                        "a\nX\nb\nc\nd\ne\n", encoding="utf-8"
                    )

                    report = self.mod.inspect(project, "4.25.0")

                    self.assertNotIn("state_core.py", report["local_patches"])
                    self.assertFalse(
                        any("parches locales" in b for b in report["blockers"]),
                        report["blockers"],
                    )
            finally:
                self.mod.ROOT = original

    def test_parche_ausente_de_la_plantilla_sigue_bloqueando(self):
        version = "4.24.0"
        base = "a\nb\nc\n"
        with plantilla_con_release(version, base) as plantilla:
            # La plantilla no incorporó el parche del proyecto: sigue en base.
            original = self.mod.ROOT
            try:
                self.mod.ROOT = plantilla
                with tempfile.TemporaryDirectory() as tmp:
                    project = make_project(Path(tmp), "alpha", version)
                    (project / ".agent" / "scripts" / "state_core.py").write_text(
                        "a\nb\nc\nX\n", encoding="utf-8"
                    )

                    report = self.mod.inspect(project, "4.25.0")

                    self.assertIn("state_core.py", report["local_patches"])
                    self.assertTrue(any("parches locales" in b for b in report["blockers"]))
            finally:
                self.mod.ROOT = original

    def test_fichero_ausente_en_la_plantilla_actual_sigue_bloqueando(self):
        """Sin plantilla actual con la que mergear, no hay forma de saber que el
        parche ya está incorporado: no hay merge posible, y perderlo sería real."""
        version = "4.24.0"
        base = "a\nb\nc\n"
        with plantilla_con_release(version, base) as plantilla:
            (plantilla / ".agent" / "scripts" / "state_core.py").unlink()
            subprocess.run(["git", "-C", str(plantilla), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(plantilla), "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "chore: retira state_core.py"], check=True,
            )

            original = self.mod.ROOT
            try:
                self.mod.ROOT = plantilla
                with tempfile.TemporaryDirectory() as tmp:
                    project = make_project(Path(tmp), "alpha", version)
                    (project / ".agent" / "scripts" / "state_core.py").write_text(
                        "a\nb\nc\nX\n", encoding="utf-8"
                    )

                    report = self.mod.inspect(project, "4.25.0")

                    self.assertIn("state_core.py", report["local_patches"])
            finally:
                self.mod.ROOT = original

    def test_conflicto_real_de_merge_sigue_bloqueando(self):
        """El proyecto y la plantilla cambiaron la MISMA región de forma
        distinta: no hay forma automática de saber que el parche ya está
        incorporado, así que debe seguir contando como parche local."""
        version = "4.24.0"
        base = "a\nb\n"
        with plantilla_con_release(version, base) as plantilla:
            script = plantilla / ".agent" / "scripts" / "state_core.py"
            script.write_text("a\nY\nb\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(plantilla), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(plantilla), "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "feat: cambia la misma región con Y"], check=True,
            )

            original = self.mod.ROOT
            try:
                self.mod.ROOT = plantilla
                with tempfile.TemporaryDirectory() as tmp:
                    project = make_project(Path(tmp), "alpha", version)
                    (project / ".agent" / "scripts" / "state_core.py").write_text(
                        "a\nX\nb\n", encoding="utf-8"
                    )

                    report = self.mod.inspect(project, "4.25.0")

                    self.assertIn("state_core.py", report["local_patches"])
            finally:
                self.mod.ROOT = original

    def test_sin_marca_de_release_sigue_indeterminado_pese_al_arreglo_1(self):
        """El arreglo 1 no cambia el caso INDETERMINATE: sin base no hay merge
        de tres vías posible."""
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "0.0.1")
            (project / ".agent" / "scripts" / "state_core.py").write_text("x", encoding="utf-8")

            report = self.mod.inspect(project, "4.24.5")

            self.assertEqual([self.mod.INDETERMINATE], report["local_patches"])

    def test_los_parches_reconocidos_siguen_listandose(self):
        """Reconocer no es ocultar: los ficheros deben seguir siendo visibles
        para que quien lo declara pueda comprobar lo que está asumiendo."""
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            (project / ".agent" / "scripts" / "state_core.py").write_text("x", encoding="utf-8")
            commit_all(project)
            original = self.mod.ROOT
            with plantilla_con_release("4.24.0", "# original\n") as plantilla:
                try:
                    self.mod.ROOT = plantilla
                    report = self.mod.inspect(project, "4.25.0", frozenset({"alpha"}))
                finally:
                    self.mod.ROOT = original

            self.assertIn("state_core.py", report["local_patches"])

    def test_reconocer_un_proyecto_inexistente_es_error_de_uso(self):
        """Un nombre mal escrito no debe pasar por reconocimiento silencioso."""
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp), "alpha", "4.24.0")
            result = subprocess.run(
                ["python3", str(SCRIPT), "--search-root", tmp,
                 "--patches-upstreamed", "no-existe"],
                capture_output=True, text=True,
            )

            self.assertEqual(2, result.returncode, result.stdout)
            self.assertIn("no encontrados", result.stderr)

    def test_skip_excluye_del_apply_a_un_proyecto_listo(self):
        """FW-191: excluir un proyecto debe ser una decisión declarada, no el
        efecto colateral de que su árbol esté sucio por casualidad. Un proyecto
        limpio y atrasado — o sea, perfectamente elegible — debe quedarse fuera
        si se le nombra, y su versión no puede avanzar."""
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp), "alpha", "4.24.0")
            make_project(Path(tmp), "beta", "4.24.0")

            result = subprocess.run(
                ["python3", str(SCRIPT), "--search-root", tmp, "--skip", "alpha"],
                capture_output=True, text=True,
            )

            self.assertIn("OMITIDO (--skip)", result.stdout)
            reports = {
                r["project"]: r
                for r in json.loads(
                    subprocess.run(
                        ["python3", str(SCRIPT), "--search-root", tmp,
                         "--skip", "alpha", "--json"],
                        capture_output=True, text=True,
                    ).stdout
                )["projects"]
            }
            self.assertTrue(reports["alpha"]["skipped"])
            self.assertFalse(reports["beta"]["skipped"])
            self.assertTrue(reports["alpha"]["behind"])
            # Omitir no inventa ni borra bloqueos: el veredicto de alpha debe ser
            # el mismo con y sin --skip. Se compara en vez de exigir la lista
            # vacía porque los blockers dependen del historial del repo anfitrión
            # (sin commit de release, INDETERMINATE) y un clon --depth 1 no lo
            # tiene: esa dependencia es justo la que rompió el CI en FW-183.
            sin_skip = {
                r["project"]: r
                for r in json.loads(
                    subprocess.run(
                        ["python3", str(SCRIPT), "--search-root", tmp, "--json"],
                        capture_output=True, text=True,
                    ).stdout
                )["projects"]
            }
            self.assertEqual(sin_skip["alpha"]["blockers"], reports["alpha"]["blockers"])

    def test_skip_no_se_cuenta_como_bloqueado_al_aplicar(self):
        """El resumen debe distinguir 'no pude' de 'no quise': un omitido no es
        un bloqueo, y confundirlos oculta que la propagación quedó a medias."""
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp), "alpha", "4.24.0")

            result = subprocess.run(
                ["python3", str(SCRIPT), "--search-root", tmp,
                 "--apply", "--yes", "--skip", "alpha"],
                capture_output=True, text=True,
            )

            self.assertIn("omitidos por --skip", result.stdout)
            self.assertIn("alpha", result.stdout)
            self.assertIn("0 bloqueados sin tocar", result.stdout)
            declarada = json.loads(
                (Path(tmp) / "alpha" / "_project" / "03_project_state.json").read_text(
                    encoding="utf-8"
                )
            )["framework_version"]
            self.assertEqual("4.24.0", declarada)

    def test_omitir_un_proyecto_inexistente_es_error_de_uso(self):
        """Un nombre mal escrito no debe dar una falsa sensación de protección:
        fail-closed, igual que --patches-upstreamed."""
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp), "alpha", "4.24.0")
            result = subprocess.run(
                ["python3", str(SCRIPT), "--search-root", tmp, "--skip", "Alpha"],
                capture_output=True, text=True,
            )

            self.assertEqual(2, result.returncode, result.stdout)
            self.assertIn("no encontrados", result.stderr)

    def test_un_directorio_nuevo_de_agent_se_propaga_sin_tocar_la_lista(self):
        """FW-195: la lista era una tupla enumerada a mano, así que un directorio
        nuevo del framework no llegaba a ningún adoptante. `.agent/prompts/`
        (4.30.0) se quedó fuera y los 9 upgrades a 4.31.0 murieron con
        `doctor FALLA tras el upgrade`. Derivar del disco lo hace nacer
        propagado: para quedar fuera hay que nombrarlo."""
        dirs = self.mod.overwrite_dirs()
        en_disco = {
            d.name
            for d in (self.mod.ROOT / ".agent").iterdir()
            if d.is_dir() and not d.name.startswith(".")
        }
        self.assertEqual(set(dirs), en_disco - set(self.mod.NOT_OVERWRITTEN))
        self.assertIn("prompts", dirs)

    def test_lo_del_producto_nunca_se_sobrescribe(self):
        """`skills` se copia aparte para no pisar imported/ y domain/;
        `extensions` es del producto. Derivar no puede relajar eso."""
        dirs = self.mod.overwrite_dirs()
        self.assertNotIn("skills", dirs)
        self.assertNotIn("extensions", dirs)

    def test_apply_requires_explicit_yes(self):
        result = subprocess.run(
            ["python3", str(SCRIPT), "--apply"], capture_output=True, text=True
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("--yes", result.stderr)

    def test_report_exit_code_signals_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp), "alpha", "4.20.0")
            behind = subprocess.run(
                ["python3", str(SCRIPT), "--search-root", tmp],
                capture_output=True, text=True,
            )
            self.assertEqual(1, behind.returncode, behind.stdout)

        with tempfile.TemporaryDirectory() as empty:
            clean = subprocess.run(
                ["python3", str(SCRIPT), "--search-root", empty],
                capture_output=True, text=True,
            )
            self.assertEqual(0, clean.returncode, clean.stdout)


class ReleaseMarkerWarningTests(unittest.TestCase):
    """Arreglo 2: nadie comprobaba que la versión de la plantilla tuviera su
    commit `release: X.Y.Z -- ...`. Sin él, todo proyecto que la adopte queda
    INDETERMINATE. El aviso es informativo, no bloqueante (FW-plan)."""

    def setUp(self):
        self.mod = load_module()

    def test_falta_la_marca_de_release_se_reporta(self):
        with plantilla_con_release("4.24.0", "# original\n") as plantilla:
            # La plantilla avanza a 4.25.0 SIN crear su commit de release.
            (plantilla / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (plantilla / ".agent" / "workflows" / "manifest.json").write_text(
                json.dumps({"framework_version": "4.25.0"}), encoding="utf-8"
            )
            subprocess.run(["git", "-C", str(plantilla), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(plantilla), "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "chore: sube manifest sin marca de release"], check=True,
            )
            original = self.mod.ROOT
            try:
                self.mod.ROOT = plantilla
                self.assertTrue(self.mod.missing_release_marker("4.25.0"))
            finally:
                self.mod.ROOT = original

    def test_con_marca_de_release_no_hay_aviso(self):
        with plantilla_con_release("4.25.0", "# original\n") as plantilla:
            original = self.mod.ROOT
            try:
                self.mod.ROOT = plantilla
                self.assertFalse(self.mod.missing_release_marker("4.25.0"))
            finally:
                self.mod.ROOT = original

    def test_main_imprime_el_aviso_cuando_falta_la_marca(self):
        with plantilla_con_release("4.24.0", "# original\n") as plantilla:
            (plantilla / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (plantilla / ".agent" / "workflows" / "manifest.json").write_text(
                json.dumps({"framework_version": "4.25.0"}), encoding="utf-8"
            )
            subprocess.run(["git", "-C", str(plantilla), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(plantilla), "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "chore: sube manifest sin marca de release"], check=True,
            )
            original = self.mod.ROOT
            try:
                self.mod.ROOT = plantilla
                with tempfile.TemporaryDirectory() as tmp_vacio:
                    import io
                    buf = io.StringIO()
                    with patch("sys.argv", ["propagate_framework.py", "--search-root", tmp_vacio]):
                        with contextlib.redirect_stdout(buf):
                            self.mod.main()
                    salida = buf.getvalue()
                    self.assertIn("release: 4.25.0", salida)
                    self.assertIn("BLOQUEADO", salida)
            finally:
                self.mod.ROOT = original

    def test_main_no_imprime_el_aviso_cuando_existe_la_marca(self):
        with plantilla_con_release("4.25.0", "# original\n") as plantilla:
            original = self.mod.ROOT
            try:
                self.mod.ROOT = plantilla
                with tempfile.TemporaryDirectory() as tmp_vacio:
                    import io
                    buf = io.StringIO()
                    with patch("sys.argv", ["propagate_framework.py", "--search-root", tmp_vacio]):
                        with contextlib.redirect_stdout(buf):
                            self.mod.main()
                    salida = buf.getvalue()
                    self.assertNotIn("BLOQUEADO", salida)
            finally:
                self.mod.ROOT = original


if __name__ == "__main__":
    unittest.main()


class StateManagerComparisonTests(unittest.TestCase):
    """FW-171: el mismo idioma de comparación mal aplicado en tres sitios.

    `.mcp.json` declara `state_manager` con `command` y `args` correctos MÁS una
    clave de documentación `$purpose` que el propio framework distribuye. Los
    tres sitios comparaban por igualdad estricta de dict y la rechazaban, lo que
    bloqueaba el upgrade de todo proyecto Codex adoptante. Deben comparar por
    `command` + `args` y tolerar claves extra."""

    SITES = (
        ".agent/scripts/framework_doctor.py",
        ".agent/scripts/ensure_codex_integration.py",
        ".agent/scripts/codex_runtime_smoke.py",
    )

    def test_no_site_compares_state_manager_by_strict_dict_equality(self):
        offenders = []
        for rel in self.SITES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "state_manager" in stripped and re.search(
                    r'state_manager\s*[!=]=\s*\{', stripped
                ):
                    offenders.append(f"{rel}: {stripped}")

        self.assertEqual([], offenders, "comparación estricta reintroducida")

    def test_every_site_tolerates_documentation_keys(self):
        """Las tres deben aceptar un state_manager con `$purpose`."""
        for rel in self.SITES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(
                'get("command")', text,
                f"{rel} no compara por command; ¿volvió la igualdad estricta?",
            )
            self.assertIn('get("args")', text, f"{rel} no compara por args")


class ApplyUpgradeTests(unittest.TestCase):
    """Red automática de `--apply` (FW-177).

    Este código escribe en repos de producto. Hasta ahora solo estaba verificado
    a mano con ensayos sobre copias desechables — que encontraron dos fallos
    reales (sembrado condicional de más y rollback con residuo), pero no dejaban
    nada que impidiera reintroducirlos.
    """

    def setUp(self):
        self.mod = load_module()

    def _project_con_doctor(self, root: Path, veredicto: int) -> Path:
        """Proyecto mínimo cuyo `framework_doctor.py` sale con el código dado."""
        project = make_project(root, "alpha", "4.24.0", git=True)
        doctor = project / ".agent" / "scripts" / "framework_doctor.py"
        doctor.write_text(f"import sys\nsys.exit({veredicto})\n", encoding="utf-8")
        (project / "AGENTS.md").write_text("# agentes del producto\n", encoding="utf-8")
        (project / "_project" / "15_environment_profile.md").write_text(
            "# perfil\n", encoding="utf-8"
        )
        commit_all(project)
        return project

    def test_un_doctor_en_fallo_restaura_y_no_deja_residuo(self):
        """El contrato del paso 5: no dejar el proyecto a medias. Un fallo debe
        devolverlo a estado limpio, sin `.agent_backup/` ni ficheros huérfanos —
        si los dejara, el propio guard de árbol limpio bloquearía el reintento."""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project_con_doctor(Path(tmp), veredicto=1)
            antes = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                capture_output=True, text=True,
            ).stdout

            ok = self.mod.apply_upgrade(self.mod.inspect(project, "4.25.0"))

            self.assertFalse(ok, "un doctor en fallo no puede reportar éxito")
            self.assertFalse(
                (project / ".agent_backup").exists(),
                "el backup nunca debe quedar dentro del árbol",
            )
            sucio = subprocess.run(
                ["git", "-C", str(project), "status", "--porcelain"],
                capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual("", sucio, f"residuo tras el rollback: {sucio}")
            despues = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                capture_output=True, text=True,
            ).stdout
            self.assertEqual(antes, despues)

    def test_un_rollback_no_deja_residuo_en_los_tests_sincronizados(self):
        """FW-197: el rollback decía "sin residuo" y no restauraba `tests/`.
        `.agent/` se respalda entero, pero los tests del framework viven fuera:
        un upgrade fallido dejaba contenido de la versión nueva sobre un
        proyecto revertido. En `example-downloader` sobrevivieron 99 líneas y
        su árbol sucio bloqueó el siguiente upgrade."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project_con_doctor(root, veredicto=1)
            (project / "tests").mkdir(exist_ok=True)
            objetivo = project / "tests" / "test_framework_doctor.py"
            original = "# contenido del proyecto, anterior al upgrade\n"
            objetivo.write_text(original, encoding="utf-8")
            commit_all(project)

            report = self.mod.inspect(project, "9.9.9", frozenset())
            self.mod.apply_upgrade(report)

            self.assertEqual(original, objetivo.read_text(encoding="utf-8"))
            sucio = subprocess.run(
                ["git", "-C", str(project), "status", "--short"],
                capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual("", sucio, f"el rollback dejó residuo:\n{sucio}")

    def test_un_rollback_restaurates_superficies_codex_y_hook_git_existentes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project_con_doctor(Path(tmp), veredicto=1)
            profile = project / "_project" / "15_environment_profile.md"
            profile.write_text("- Herramienta: Codex\n", encoding="utf-8")
            codex = project / ".codex"
            codex.mkdir()
            (codex / "config.toml").write_text("custom = true\n", encoding="utf-8")
            (codex / "hooks.json").write_text('{"hooks": {"Custom": []}}\n', encoding="utf-8")
            claude = project / ".claude"
            claude.mkdir()
            (claude / "settings.json").write_text('{"custom": true}\n', encoding="utf-8")
            hook = project / ".git" / "hooks" / "pre-commit"
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
            hook.chmod(0o755)
            commit_all(project)

            paths = [
                project / "_project" / "15_environment_profile.md",
                codex / "config.toml",
                codex / "hooks.json",
                claude / "settings.json",
                hook,
            ]
            before = {path: (path.read_bytes(), path.stat().st_mode) for path in paths}

            self.assertFalse(self.mod.apply_upgrade(self.mod.inspect(project, "9.9.9")))

            for path, (raw, mode) in before.items():
                self.assertEqual(raw, path.read_bytes(), path)
                self.assertEqual(mode, path.stat().st_mode, path)
            self.assertEqual(
                "",
                subprocess.run(
                    ["git", "-C", str(project), "status", "--short"],
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            )

    def test_apply_upgrade_commitea_por_defecto(self):
        """FW-196: aplicar sin commitear dejaba el árbol sucio, y eso bloquea al
        SIGUIENTE upgrade por el gate tier_2 — un fallo transitorio se volvía
        bloqueo persistente. El opt-out existe, pero el defecto es cerrar."""
        firma = inspect.signature(self.mod.apply_upgrade)
        self.assertIs(True, firma.parameters["commit"].default)

    def test_commit_upgrade_cierra_el_arbol_y_nombra_la_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            (project / ".agent" / "scripts" / "nuevo.py").write_text("x\n", encoding="utf-8")
            antes = int(subprocess.run(
                ["git", "-C", str(project), "rev-list", "--count", "HEAD"],
                capture_output=True, text=True).stdout.strip())

            cerrado, motivo = self.mod.commit_upgrade(
                project, {"declared": "4.24.0", "target": "9.9.9", "project": "alpha"})

            self.assertTrue(cerrado, motivo)
            self.assertEqual("", subprocess.run(
                ["git", "-C", str(project), "status", "--short"],
                capture_output=True, text=True).stdout.strip())
            despues = int(subprocess.run(
                ["git", "-C", str(project), "rev-list", "--count", "HEAD"],
                capture_output=True, text=True).stdout.strip())
            self.assertEqual(antes + 1, despues)
            asunto = subprocess.run(
                ["git", "-C", str(project), "log", "-1", "--format=%s"],
                capture_output=True, text=True).stdout.strip()
            self.assertIn("4.24.0 -> 9.9.9", asunto)

    def test_un_commit_fallido_se_reporta_y_no_se_finge_cerrado(self):
        """Lo que suele fallar aquí es el pre-commit del propio producto. El
        doctor ya pasó, así que revertir seria peor que dejarlo aplicado — pero
        hay que DECIRLO: afirmar un cierre que no ocurrió es la clase FW-194."""
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            (project / ".agent" / "scripts" / "nuevo.py").write_text("x\n", encoding="utf-8")
            hook = project / ".git" / "hooks" / "pre-commit"
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)

            cerrado, motivo = self.mod.commit_upgrade(
                project, {"declared": "4.24.0", "target": "9.9.9", "project": "alpha"})

            self.assertFalse(cerrado)
            self.assertTrue(motivo.strip(), "un cierre fallido debe explicar por qué")
            # FW-200: reportar la ÚLTIMA línea daba el consejo de FW-099 del
            # pre-commit en vez de la causa. El motivo debe señalar el fallo.
            self.assertNotIn("registrala AHORA", motivo)
            self.assertNotEqual("", subprocess.run(
                ["git", "-C", str(project), "status", "--short"],
                capture_output=True, text=True).stdout.strip())

    def test_el_motivo_del_commit_fallido_senala_la_causa_y_no_el_consejo(self):
        """FW-200: un pre-commit del framework termina con su consejo de FW-099
        ("registrala AHORA como FW-NNN..."). Reportar la última línea dejaba un
        motivo inútil justo cuando hace falta saber qué falló."""
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            (project / ".agent" / "scripts" / "nuevo.py").write_text("x\n", encoding="utf-8")
            hook = project / ".git" / "hooks" / "pre-commit"
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text(
                "#!/bin/sh\n"
                "echo 'FAIL: test_algo (suite del producto)'\n"
                "echo '-> Si este bloqueo es friccion del framework: registrala AHORA como FW-NNN'\n"
                "exit 1\n",
                encoding="utf-8",
            )
            hook.chmod(0o755)

            cerrado, motivo = self.mod.commit_upgrade(
                project, {"declared": "4.24.0", "target": "9.9.9", "project": "alpha"})

            self.assertFalse(cerrado)
            self.assertIn("FAIL: test_algo", motivo)
            self.assertNotIn("registrala AHORA", motivo)

    def test_la_version_no_avanza_si_el_doctor_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project_con_doctor(Path(tmp), veredicto=1)

            self.mod.apply_upgrade(self.mod.inspect(project, "4.25.0"))

            estado = json.loads(
                (project / "_project" / "03_project_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual("4.24.0", estado["framework_version"])

    def test_el_ancla_de_AGENTS_se_siembra_sea_o_no_proyecto_codex(self):
        """FW-171: `codex_integration` no tiene guards en `doctor_wiring.json`
        — exige esas anclas a TODO proyecto. Sembrarlas solo en proyectos Codex
        dejó 4 de 5 upgrades en fallo en la propagación real."""
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            (project / "AGENTS.md").write_text("# sin nada de Codex\n", encoding="utf-8")

            sembrado = self.mod.seed_agents_contract(project)

            self.assertEqual(["AGENTS.md"], sembrado)
            self.assertIn(
                self.mod.AGENTS_CODEX_NEEDLE,
                (project / "AGENTS.md").read_text(encoding="utf-8"),
            )

    def test_el_sembrado_de_AGENTS_es_idempotente(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            (project / "AGENTS.md").write_text("# vacío\n", encoding="utf-8")

            self.mod.seed_agents_contract(project)
            contenido = (project / "AGENTS.md").read_text(encoding="utf-8")
            segunda = self.mod.seed_agents_contract(project)

            self.assertEqual([], segunda)
            self.assertEqual(contenido, (project / "AGENTS.md").read_text(encoding="utf-8"))

    def test_el_sembrado_preserva_el_contenido_del_producto(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            propio = "# AGENTS del producto\n\nReglas que no son del framework.\n"
            (project / "AGENTS.md").write_text(propio, encoding="utf-8")

            self.mod.seed_agents_contract(project)

            self.assertIn(propio.strip(), (project / "AGENTS.md").read_text(encoding="utf-8"))


class CodexProfileMigrationTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.values = {
            "Soporte estructural Codex": "unavailable",
            "Smoke runtime Codex": "unavailable",
            "Superficies Codex requeridas": "cli",
            "Smoke runtime Codex CLI": "unavailable",
            "Smoke runtime Codex Desktop/app-server": "unavailable",
            "Versión de Codex": "no disponible",
            "Versión de Codex CLI": "no disponible",
            "Versión de Codex Desktop/app-server": "no disponible",
            "Modelo Codex smoke": "[no ejecutado]",
            "Esfuerzo Codex smoke": "[no ejecutado]",
            "Hooks Codex": "no disponibles",
            "MCP local `state_manager`": "no disponible",
            "Diferencias Codex conocidas": "ninguna",
        }

    def _legacy_profile(self) -> str:
        return (
            "# Perfil de Entorno\n"
            "- Herramienta: Codex\n"
            "- Soporte estructural Codex: pass\n"
            "- Smoke runtime Codex: pass\n"
            "- Versión de Codex: codex-cli legacy\n"
            "- Hooks Codex: gestionados\n"
            "- MCP local `state_manager`: conectado\n"
            "- Diferencias Codex conocidas: ninguna\n"
        )

    def test_legacy_aggregate_pass_is_preserved_as_antecedent_and_not_certified_as_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            profile = project / "_project" / "15_environment_profile.md"
            profile.write_text(self._legacy_profile(), encoding="utf-8")

            with patch.object(self.mod, "_probe_codex_values", return_value=self.values):
                seeded = self.mod.seed_codex_contract(project)

            text = profile.read_text(encoding="utf-8")
            evidence = [line for line in text.splitlines() if line.startswith("- Evidencia Codex anterior:")]
            self.assertEqual(len(evidence), 1, text)
            self.assertIn('"schema_version":1', evidence[0])
            self.assertIn('"runtime_smoke":"pass"', evidence[0])
            self.assertIn('"version":"codex-cli legacy"', evidence[0])
            self.assertIn("- Smoke runtime Codex: unavailable", text)
            self.assertIn("- Smoke runtime Codex CLI: unavailable", text)
            self.assertIn("perfil", "+".join(seeded))

    def test_legacy_migration_is_idempotent_and_keeps_first_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            profile = project / "_project" / "15_environment_profile.md"
            profile.write_text(self._legacy_profile(), encoding="utf-8")
            with patch.object(self.mod, "_probe_codex_values", return_value=self.values):
                self.mod.seed_codex_contract(project)
                first = profile.read_bytes()
                self.mod.seed_codex_contract(project)
                second = profile.read_bytes()
            self.assertEqual(first, second)
            self.assertEqual(1, sum(line.startswith(b"- Evidencia Codex anterior:") for line in second.splitlines()))

    def test_duplicate_or_invalid_canonical_profile_labels_fail_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            profile = project / "_project" / "15_environment_profile.md"
            profile.write_text(
                self._legacy_profile() + "- Smoke runtime Codex: maybe\n"
                "- Smoke runtime Codex: pass\n",
                encoding="utf-8",
            )
            before = profile.read_bytes()
            with self.assertRaises(self.mod.ProfileMigrationError):
                with patch.object(self.mod, "_probe_codex_values", return_value=self.values):
                    self.mod.seed_codex_contract(project)
            self.assertEqual(before, profile.read_bytes())

    def test_explicit_cli_evidence_is_not_downgraded_by_informational_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            profile = project / "_project" / "15_environment_profile.md"
            profile.write_text(
                self._legacy_profile()
                + "- Superficies Codex requeridas: cli\n"
                + "- Smoke runtime Codex CLI: pass\n",
                encoding="utf-8",
            )
            values = {**self.values, "Smoke runtime Codex": "unavailable"}
            with patch.object(self.mod, "_probe_codex_values", return_value=values):
                self.mod.seed_codex_contract(project)
            text = profile.read_text(encoding="utf-8")
            self.assertIn("- Smoke runtime Codex CLI: pass", text)

    def test_invalid_probe_value_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "alpha", "4.24.0")
            profile = project / "_project" / "15_environment_profile.md"
            profile.write_text(self._legacy_profile(), encoding="utf-8")
            before = profile.read_bytes()
            values = {**self.values, "Smoke runtime Codex CLI": "maybe"}
            with self.assertRaises(self.mod.ProfileMigrationError):
                with patch.object(self.mod, "_probe_codex_values", return_value=values):
                    self.mod.seed_codex_contract(project)
            self.assertEqual(before, profile.read_bytes())


class UpgradeFailureBoundaryTests(unittest.TestCase):
    """FW-232 — un fallo en cualquier punto del upgrade restaura o lo dice.

    Auditoría 2026-09-14: la fase de copiado quedaba fuera del rollback (un
    `OSError` o un Ctrl+C dejaban `.agent` a medias, el árbol sucio y el backup
    huérfano en /tmp) y, si fallaba el rollback Codex, el llamador anunciaba
    "restaurado, sin residuo" sin haber restaurado nada.
    """

    MARCA = ".agent/scripts/marca_version_vieja.py"

    def setUp(self):
        self.mod = load_module()
        self.real_run = subprocess.run
        self.real_copy2 = self.mod.shutil.copy2
        self.staging_antes = self._staging()

    def tearDown(self):
        for path in self._staging() - self.staging_antes:
            self.mod.shutil.rmtree(Path(tempfile.gettempdir()) / path, ignore_errors=True)

    def _staging(self) -> set[str]:
        return {p.name for p in Path(tempfile.gettempdir()).glob("fw-upgrade-alpha-*")}

    def _project(self, root: Path) -> Path:
        project = make_project(root, "alpha", "4.24.0", git=True)
        (project / self.MARCA).write_text("# version vieja\n", encoding="utf-8")
        (project / "AGENTS.md").write_text("# agentes\n", encoding="utf-8")
        (project / "_project" / "15_environment_profile.md").write_text("# perfil\n", encoding="utf-8")
        commit_all(project)
        return project

    def _status(self, project: Path) -> str:
        return self.real_run(
            ["git", "-C", str(project), "status", "--short"], capture_output=True, text=True,
        ).stdout.strip()

    def _copy2_que_falla(self, exc: BaseException):
        def copy2(src, dst, *args, **kwargs):
            if "/.agent/skills/" in str(dst):
                raise exc
            return self.real_copy2(src, dst, *args, **kwargs)
        return copy2

    def test_oserror_en_el_copiado_restaura_sin_lanzar(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            out = io.StringIO()
            with patch.object(self.mod.shutil, "copy2", self._copy2_que_falla(OSError(28, "disco lleno"))), \
                    contextlib.redirect_stdout(out):
                ok = self.mod.apply_upgrade(self.mod.inspect(project, "9.9.9"))
            self.assertFalse(ok)
            self.assertTrue((project / self.MARCA).exists(), "el .agent anterior debe volver")
            self.assertEqual("", self._status(project))
            self.assertIn("restaurado, sin residuo", out.getvalue())
            self.assertIn("disco lleno", out.getvalue())
            self.assertEqual(set(), self._staging() - self.staging_antes, "backup restaurado: no debe quedar staging")

    def test_interrupcion_en_el_copiado_restaura_y_relanza(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            with patch.object(self.mod.shutil, "copy2", self._copy2_que_falla(KeyboardInterrupt())), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    self.mod.apply_upgrade(self.mod.inspect(project, "9.9.9"))
            self.assertTrue((project / self.MARCA).exists())
            self.assertEqual("", self._status(project))

    def test_fallo_del_sync_se_avisa(self):
        def fake_run(args, *a, **k):
            joined = " ".join(map(str, args)) if isinstance(args, (list, tuple)) else str(args)
            if "sync_claude_commands.py" in joined:
                return subprocess.CompletedProcess(args, 1, "", "Bloque GENERADO no encontrado")
            return self.real_run(args, *a, **k)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            out = io.StringIO()
            with patch.object(self.mod.subprocess, "run", fake_run), contextlib.redirect_stdout(out):
                self.mod.apply_upgrade(self.mod.inspect(project, "9.9.9"))
            self.assertIn("Bloque GENERADO no encontrado", out.getvalue())

    def test_main_sigue_con_el_siguiente_proyecto_si_uno_revienta(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "alpha", Path(tmp) / "beta"]
            for path in paths:
                path.mkdir()

            def report(project, target, upstreamed=frozenset()):
                return {
                    "project": project.name, "path": str(project), "declared": "4.24.0",
                    "target": target, "git": "limpio", "behind": True, "blockers": [],
                    "patches_upstreamed": False, "local_patches": [],
                }

            llamadas = []

            # 5.1 L6.1: el mock acepta los kwargs de H5 (allow_open_session,
            # claude_projects_dir) en vez de que main() inspeccione la firma.
            def apply_upgrade(r, commit=True, **_kwargs):
                llamadas.append(r["project"])
                if r["project"] == "alpha":
                    raise RuntimeError("regresión inesperada")
                return True

            out = io.StringIO()
            with patch.object(self.mod, "discover", return_value=paths), \
                    patch.object(self.mod, "inspect", side_effect=report), \
                    patch.object(self.mod, "template_version", return_value="9.9.9"), \
                    patch.object(self.mod, "missing_release_marker", return_value=False), \
                    patch.object(self.mod, "apply_upgrade", side_effect=apply_upgrade), \
                    patch.object(self.mod.sys, "argv", ["propagate_framework.py", "--apply", "--yes"]), \
                    contextlib.redirect_stdout(out):
                rc = self.mod.main()
            self.assertEqual(["alpha", "beta"], llamadas)
            self.assertEqual(1, rc)
            self.assertIn("regresión inesperada", out.getvalue())
            self.assertIn("1/2 actualizados", out.getvalue())


class TestOpenClaudeSessionFW253(unittest.TestCase):
    """FW-253: check_open_claude_session deja de usar mtime y lee el timestamp

    del último mensaje user/assistant en la cola de los *.jsonl.
    """

    def setUp(self):
        self.mod = load_module()

    def test_transcript_with_old_messages_and_recent_metadata_does_not_block(self):
        """Mensajes antiguos (>60 min) con metadatos escritos ahora no bloquean."""
        import datetime, time, os
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "claude_projects"
            project_path = Path(tmp) / "project_x"
            project_path.mkdir()
            slug = self.mod.claude_project_slug(project_path)
            slug_dir = base / slug
            slug_dir.mkdir(parents=True)

            transcript = slug_dir / "session.jsonl"
            # Mensajes de hace 2 horas (7200s)
            t_old = datetime.datetime.fromtimestamp(
                time.time() - 7200, tz=datetime.timezone.utc
            ).isoformat()
            t_recent = datetime.datetime.fromtimestamp(
                time.time() - 30, tz=datetime.timezone.utc
            ).isoformat()
            lines = [
                json.dumps({"type": "user", "timestamp": t_old, "message": {"content": "hola"}}),
                json.dumps({"type": "assistant", "timestamp": t_old, "message": {"content": "mundo"}}),
                json.dumps({"type": "cost-state", "timestamp": t_recent, "tokens": 500}),
            ]
            transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
            # mtime reciente (hace 10 segundos)
            now = time.time()
            os.utime(transcript, (now - 10, now - 10))

            has_open, reason = self.mod.check_open_claude_session(
                project_path, claude_projects_dir=base
            )
            self.assertFalse(has_open, f"No debe bloquear si los mensajes son antiguos; got: {reason}")
            self.assertEqual(reason, "")

    def test_transcript_with_recent_message_blocks(self):
        """Mensaje user o assistant reciente (<60 min) bloquea."""
        import datetime, time, os
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "claude_projects"
            project_path = Path(tmp) / "project_x"
            project_path.mkdir()
            slug = self.mod.claude_project_slug(project_path)
            slug_dir = base / slug
            slug_dir.mkdir(parents=True)

            transcript = slug_dir / "active.jsonl"
            t_recent = datetime.datetime.fromtimestamp(
                time.time() - 300, tz=datetime.timezone.utc
            ).isoformat()
            lines = [
                json.dumps({"type": "user", "timestamp": t_recent, "message": {"content": "trabajando"}}),
            ]
            transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
            now = time.time()
            os.utime(transcript, (now, now))

            has_open, reason = self.mod.check_open_claude_session(
                project_path, claude_projects_dir=base
            )
            self.assertTrue(has_open)
            self.assertIn("sesión de Claude Code activa", reason)
            self.assertIn("active.jsonl", reason)

    def test_unreadable_or_corrupt_transcript_falls_back_to_mtime_conservative(self):
        """Transcripción ilegible o sin mensajes usa comportamiento conservador (mtime)."""
        import time, os
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "claude_projects"
            project_path = Path(tmp) / "project_x"
            project_path.mkdir()
            slug = self.mod.claude_project_slug(project_path)
            slug_dir = base / slug
            slug_dir.mkdir(parents=True)

            # Archivo con JSON sin mensajes user/assistant y mtime reciente
            transcript = slug_dir / "corrupt.jsonl"
            transcript.write_text("{not valid json\n", encoding="utf-8")
            now = time.time()
            os.utime(transcript, (now - 60, now - 60))

            has_open, reason = self.mod.check_open_claude_session(
                project_path, claude_projects_dir=base
            )
            self.assertTrue(has_open, "Transcripción ilegible con mtime reciente debe bloquear por precaución")
            self.assertIn("sesión de Claude Code activa", reason)


class TestProductTestsEngineImportsWarningFW254(unittest.TestCase):
    """FW-254: AVISO tras upgrade si algún tests/**/*.py del producto importa

    state_core, workflow_receipts, state_queries o replay_state_log.
    """

    def setUp(self):
        self.mod = load_module()

    def _project_con_doctor(self, root: Path) -> Path:
        project = make_project(root, "alpha", "4.24.0", git=True)
        (project / "AGENTS.md").write_text("# agentes del producto\n", encoding="utf-8")
        (project / "_project" / "15_environment_profile.md").write_text(
            "# perfil\n", encoding="utf-8"
        )
        commit_all(project)
        return project

    def test_product_test_importing_engine_emits_warning(self):
        """Test del producto que importa workflow_receipts emite AVISO y no bloquea."""
        real_run = subprocess.run
        def fake_run(args, *a, **k):
            joined = " ".join(str(x) for x in args) if isinstance(args, (list, tuple)) else str(args)
            if "framework_doctor.py" in joined:
                return subprocess.CompletedProcess(args, 0, stdout="Framework doctor: OK\n", stderr="")
            if "state_manager.py" in joined:
                return subprocess.CompletedProcess(args, 0, stdout="OK\n", stderr="")
            if "sync_claude_commands.py" in joined:
                return subprocess.CompletedProcess(args, 0, stdout="Sync check: OK\n", stderr="")
            if "pre_commit.sh" in joined:
                return subprocess.CompletedProcess(args, 0, stdout="OK\n", stderr="")
            return real_run(args, *a, **k)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project_con_doctor(Path(tmp))
            tests_dir = project / "tests"
            tests_dir.mkdir(exist_ok=True)
            custom_test = tests_dir / "test_custom_feature.py"
            custom_test.write_text(
                "import unittest\nimport workflow_receipts\n\nclass T(unittest.TestCase): pass\n",
                encoding="utf-8",
            )
            commit_all(project)

            out = io.StringIO()
            with patch.object(self.mod.subprocess, "run", fake_run), contextlib.redirect_stdout(out):
                ok = self.mod.apply_upgrade(
                    self.mod.inspect(project, "9.9.9"),
                    commit=False,
                )

            self.assertTrue(ok, "El upgrade debe completarse con éxito (el aviso no bloquea)")
            salida = out.getvalue()
            self.assertIn("AVISO:", salida)
            self.assertIn("test_custom_feature.py", salida)
            self.assertIn("estas copias no viajan con el upgrade", salida)

    def test_framework_test_in_root_tests_does_not_emit_warning(self):
        """Test sincronizado desde la plantilla (en ROOT/tests) no emite AVISO."""
        real_run = subprocess.run
        def fake_run(args, *a, **k):
            joined = " ".join(str(x) for x in args) if isinstance(args, (list, tuple)) else str(args)
            if "framework_doctor.py" in joined:
                return subprocess.CompletedProcess(args, 0, stdout="Framework doctor: OK\n", stderr="")
            if "state_manager.py" in joined:
                return subprocess.CompletedProcess(args, 0, stdout="OK\n", stderr="")
            if "sync_claude_commands.py" in joined:
                return subprocess.CompletedProcess(args, 0, stdout="Sync check: OK\n", stderr="")
            if "pre_commit.sh" in joined:
                return subprocess.CompletedProcess(args, 0, stdout="OK\n", stderr="")
            return real_run(args, *a, **k)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project_con_doctor(Path(tmp))
            tests_dir = project / "tests"
            tests_dir.mkdir(exist_ok=True)
            # test_workflow_receipts.py existe en la plantilla (ROOT/tests/)
            fw_test = tests_dir / "test_workflow_receipts.py"
            fw_test.write_text(
                "import unittest\nimport workflow_receipts\n\nclass T(unittest.TestCase): pass\n",
                encoding="utf-8",
            )
            commit_all(project)

            out = io.StringIO()
            with patch.object(self.mod.subprocess, "run", fake_run), contextlib.redirect_stdout(out):
                ok = self.mod.apply_upgrade(
                    self.mod.inspect(project, "9.9.9"),
                    commit=False,
                )

            self.assertTrue(ok)
            salida = out.getvalue()
            self.assertNotIn("estas copias no viajan con el upgrade", salida)


class TestBranchDetectionO1(unittest.TestCase):
    """O1: la tabla muestra la rama actual de cada adoptante. Si no es la rama por

    defecto, añade «(rama X; <default> en <versión>)». Si la rama por defecto va
    por detrás, la fila NO dice «al día».
    """

    def setUp(self):
        self.mod = load_module()

    def test_inspect_and_table_when_diverged_and_behind(self):
        """Adoptante en rama feature con main atrasada muestra rama y NO dice 'al día'."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "tallo_simulado"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-b", "main", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
            (repo / "_project").mkdir()
            (repo / ".agent" / "workflows").mkdir(parents=True)
            (repo / ".agent" / "scripts").mkdir(parents=True)
            (repo / "_project" / "03_project_state.json").write_text(
                json.dumps({"framework_version": "4.37.1"}), encoding="utf-8"
            )
            (repo / ".agent" / "workflows" / "manifest.json").write_text(
                json.dumps({"framework_version": "4.37.1"}), encoding="utf-8"
            )
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init en main"], check=True)

            # Crear y pasar a feature/modulo-analitico
            subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature/modulo-analitico", "-q"], check=True)
            (repo / "_project" / "03_project_state.json").write_text(
                json.dumps({"framework_version": "5.1.0"}), encoding="utf-8"
            )
            (repo / ".agent" / "workflows" / "manifest.json").write_text(
                json.dumps({"framework_version": "5.1.0"}), encoding="utf-8"
            )
            subprocess.run(["git", "-C", str(repo), "commit", "-am", "upgrade a 5.1.0 en feature"], check=True)

            report = self.mod.inspect(repo, "5.1.0")
            self.assertEqual("feature/modulo-analitico", report.get("branch"))
            self.assertEqual("main", report.get("default_branch"))
            self.assertEqual("4.37.1", report.get("default_version"))
            self.assertTrue(report.get("default_behind"))

            out = io.StringIO()
            with patch.object(self.mod, "discover", return_value=[repo]), \
                    patch.object(self.mod, "template_version", return_value="5.1.0"), \
                    patch.object(self.mod, "missing_release_marker", return_value=False), \
                    patch.object(self.mod.sys, "argv", ["propagate_framework.py"]), \
                    contextlib.redirect_stdout(out):
                self.mod.main()

            salida = out.getvalue()
            # La fila NO dice "al día"
            lines = [l for l in salida.splitlines() if "tallo_simulado" in l]
            self.assertTrue(lines, f"tallo_simulado debe estar en la salida:\n{salida}")
            fila = lines[0]
            self.assertNotIn("al día", fila)
            self.assertIn("(rama feature/modulo-analitico; main en 4.37.1)", fila)

    def test_inspect_and_table_when_both_branches_at_target(self):
        """Adoptante en rama feature con main también al día añade sufijo y dice 'al día'."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "tallo_simulado"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-b", "main", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
            (repo / "_project").mkdir()
            (repo / ".agent" / "workflows").mkdir(parents=True)
            (repo / ".agent" / "scripts").mkdir(parents=True)
            (repo / "_project" / "03_project_state.json").write_text(
                json.dumps({"framework_version": "5.1.0"}), encoding="utf-8"
            )
            (repo / ".agent" / "workflows" / "manifest.json").write_text(
                json.dumps({"framework_version": "5.1.0"}), encoding="utf-8"
            )
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init en main"], check=True)

            subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature/modulo-analitico", "-q"], check=True)

            out = io.StringIO()
            with patch.object(self.mod, "discover", return_value=[repo]), \
                    patch.object(self.mod, "template_version", return_value="5.1.0"), \
                    patch.object(self.mod, "missing_release_marker", return_value=False), \
                    patch.object(self.mod.sys, "argv", ["propagate_framework.py"]), \
                    contextlib.redirect_stdout(out):
                self.mod.main()

            salida = out.getvalue()
            lines = [l for l in salida.splitlines() if "tallo_simulado" in l]
            self.assertTrue(lines)
            fila = lines[0]
            self.assertIn("al día", fila)
            self.assertIn("(rama feature/modulo-analitico; main en 5.1.0)", fila)

    def test_preserve_product_workflows_restores_custom_workflow_and_manifest_entry(self):
        """Un workflow específico del producto declarado en manifest.json sobrevive al upgrade."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            backup_agent = base / "backup_agent"
            project = base / "project"
            (backup_agent / "workflows").mkdir(parents=True)
            (project / ".agent" / "workflows").mkdir(parents=True)
            custom_entry = {
                "name": "cuadrar-cuenta",
                "description": "Workflow de dominio del producto",
                "source": ".agent/workflows/cuadrar-cuenta.md",
                "claude_command": True,
                "builder_facing": False,
            }
            (backup_agent / "workflows" / "cuadrar-cuenta.md").write_text("# Cuadrar cuenta\n", encoding="utf-8")
            (backup_agent / "workflows" / "manifest.json").write_text(
                json.dumps({"framework_version": "5.2.3", "commands": [{"name": "ciclo"}, custom_entry]}),
                encoding="utf-8",
            )
            (project / ".agent" / "workflows" / "manifest.json").write_text(
                json.dumps({"framework_version": "5.3.0", "commands": [{"name": "ciclo"}]}),
                encoding="utf-8",
            )
            preserved = self.mod._preserve_product_workflows(project, backup_agent)
            self.assertEqual(preserved, ["cuadrar-cuenta.md"])
            self.assertTrue((project / ".agent" / "workflows" / "cuadrar-cuenta.md").is_file())
            merged = json.loads((project / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8"))
            names = [c["name"] for c in merged["commands"]]
            self.assertIn("cuadrar-cuenta", names)

    def test_apply_upgrade_installs_pre_commit_when_dot_git_is_worktree_file(self):
        """En un worktree secundario `.git` es un fichero, y `pre_commit.sh install` debe invocarse igual."""
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "wt_proj", "4.24.0")
            self.mod.shutil.rmtree(project / ".git")
            (project / ".git").write_text("gitdir: /tmp/fake-common-git/worktrees/wt_proj\n", encoding="utf-8")
            seen_install = []

            def fake_run(cmd, *args, **kwargs):
                joined = " ".join(str(x) for x in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
                if cmd[:3] == ["bash", ".agent/scripts/pre_commit.sh", "install"]:
                    seen_install.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="OK\n", stderr="")

            out = io.StringIO()
            with patch.object(self.mod.subprocess, "run", fake_run), contextlib.redirect_stdout(out):
                ok = self.mod.apply_upgrade(
                    self.mod.inspect(project, "9.9.9"),
                    commit=False,
                )
            self.assertTrue(ok)
            self.assertEqual(len(seen_install), 1)



class ManagedBlockResyncFW394Tests(unittest.TestCase):
    """FW-394: sustituir el bloque GESTIONADO trae los GENERADO de la plantilla.

    En Research App (workflow propio `/cuadrar-cuenta`) el sync regeneraba
    los GENERADO con el manifest del proyecto y después el bloque los pisaba: el
    doctor fallaba y el upgrade se restauraba. El sync tiene que repetirse tras
    la sustitución, y solo entonces."""

    def _syncs_alrededor_del_bloque(self, sustituidos: list[str]) -> list[str]:
        mod = load_module()
        eventos: list[str] = []
        args_sync: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            if any("sync_claude_commands.py" in str(x) for x in cmd):
                eventos.append("sync")
                args_sync.append(list(cmd))
            elif any("framework_doctor.py" in str(x) for x in cmd):
                eventos.append("doctor")
            return subprocess.CompletedProcess(cmd, 0, stdout="OK\n", stderr="")

        def fake_block(project):
            eventos.append("bloque")
            return list(sustituidos), []

        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), "pa_simulado", "4.24.0")
            sin_sesiones = Path(tmp) / "claude_projects"
            sin_sesiones.mkdir()
            with patch.object(mod.subprocess, "run", fake_run), \
                    patch.object(mod, "sync_managed_root_docs", fake_block), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(mod.apply_upgrade(
                    mod.inspect(project, "9.9.9"), commit=False, claude_projects_dir=sin_sesiones,
                ))
        self.assertTrue(all(a == args_sync[0] for a in args_sync), f"el sync se repite con otros argumentos: {args_sync}")
        return eventos

    def test_el_sync_se_repite_tras_sustituir_el_bloque_y_antes_del_doctor(self):
        self.assertEqual(
            ["sync", "bloque", "sync", "doctor"], self._syncs_alrededor_del_bloque(["AGENTS.md(bloque)"]),
        )

    def test_sin_bloque_sustituido_el_sync_corre_una_vez(self):
        self.assertEqual(["sync", "bloque", "doctor"], self._syncs_alrededor_del_bloque([]))
