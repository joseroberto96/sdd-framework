#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-233: `state_write_lock` entre procesos reales.

El lock serializa el servidor MCP y el CLI. Con el lockfile `O_EXCL` y la rotura
por edad, dos procesos que encontraban el mismo lock viejo lo rompían a la vez y
ambos entraban: medido sobre una copia de un proyecto real, se perdía una
actualización sin que la bitácora ni el doctor lo notaran. Y un titular muerto
por señal dejaba el lock 60 s bloqueando mutaciones y lecturas.

Los procesos son subprocesos de verdad (el lock es entre procesos): la sección
crítica lee un contador, espera y escribe +1, que es la forma mínima de una
pérdida de actualización.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"

_PRELUDE = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    sys.path.insert(0, {scripts!r})
    import state_core
    project = Path({project!r})
    state_core.STATE_FILE_PATH = project / "03_project_state.json"
    """
)

# Dos escritores que llegan a la vez a un lock viejo. La espera dentro de la
# decisión de romper (la comprobación del PID, antes del borrado) fuerza el
# intercalado peor: ambos deciden antes de que ninguno cree el suyo. Si el diseño
# impide que los dos estén ahí a la vez, la espera caduca y el proceso sigue.
_WRITER = _PRELUDE + textwrap.dedent(
    """
    role = sys.argv[1]
    original = state_core._is_process_alive

    def is_process_alive(pid):
        (project / f"{{role}}.decidido").touch()
        otro = "B" if role == "A" else "A"
        limite = time.monotonic() + 2
        while not (project / f"{{otro}}.decidido").exists() and time.monotonic() < limite:
            time.sleep(0.01)
        if role == "B":
            time.sleep(0.3)
        return original(pid)

    state_core._is_process_alive = is_process_alive
    counter = project / "contador.txt"
    with state_core.state_write_lock(timeout=15, stale=60):
        valor = int(counter.read_text())
        time.sleep(1.0)
        counter.write_text(str(valor + 1))
    """
)

_HOLDER = _PRELUDE + textwrap.dedent(
    """
    with state_core.state_write_lock(timeout=5, stale=60):
        print("dentro", flush=True)
        time.sleep(60)
    """
)


class StateLockConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "_project"
        self.project.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _script(self, template: str) -> str:
        return template.format(scripts=str(SCRIPTS), project=str(self.project))

    def _dead_pid(self) -> int:
        proc = subprocess.Popen(["true"])
        proc.wait()
        return proc.pid

    def _start_holder(self) -> subprocess.Popen:
        holder = subprocess.Popen(
            [sys.executable, "-c", self._script(_HOLDER)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        def _stop():
            if holder.poll() is None:
                holder.kill()
            holder.communicate()

        self.addCleanup(_stop)
        self.assertEqual(holder.stdout.readline().strip(), "dentro", holder.stderr.read() if holder.poll() is not None else "")
        return holder

    def _acquire(self, timeout: float) -> float:
        sys.path.insert(0, str(SCRIPTS))
        import state_core

        original = state_core.STATE_FILE_PATH
        state_core.STATE_FILE_PATH = self.project / "03_project_state.json"
        try:
            inicio = time.monotonic()
            with state_core.state_write_lock(timeout=timeout, stale=60):
                return time.monotonic() - inicio
        finally:
            state_core.STATE_FILE_PATH = original

    def test_dos_escritores_ante_un_lock_viejo_no_pierden_actualizaciones(self):
        (self.project / "contador.txt").write_text("0")
        lock = self.project / ".state.lock"
        lock.write_text(f"{self._dead_pid()} 2026-01-01T00:00:00+00:00\n")
        viejo = time.time() - 600
        os.utime(lock, (viejo, viejo))

        procs = [
            subprocess.Popen([sys.executable, "-c", self._script(_WRITER), role],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for role in ("A", "B")
        ]
        for proc in procs:
            _, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
        self.assertEqual((self.project / "contador.txt").read_text(), "2",
                         "dos titulares a la vez: se perdió una actualización")
        self.assertFalse(lock.exists())

    def test_lock_de_un_titular_muerto_por_senal_no_hace_esperar(self):
        holder = self._start_holder()
        holder.send_signal(signal.SIGKILL)
        holder.wait()
        self.assertTrue((self.project / ".state.lock").exists(), "precondición: lock huérfano")
        transcurrido = self._acquire(timeout=3.0)
        self.assertLess(transcurrido, 1.0)

    def test_marca_de_otro_nucleo_no_se_rompe_al_instante(self):
        """Revisión del diseño, hallazgo 1: en un contenedor, una VM o una carpeta
        de red el flock es de otro dominio y la marca no prueba nada. Se aplica
        la regla antigua: fresco, no se rompe."""
        lock = self.project / ".state.lock"
        lock.write_text(f"{self._dead_pid()} 2026-09-14T00:00:00+00:00 flock:otro-nucleo-0000\n")
        with self.assertRaises(RuntimeError):
            self._acquire(timeout=0.5)
        self.assertIn("otro-nucleo", lock.read_text())

    def test_lockfile_vacio_bajo_flock_se_rompe_tras_la_gracia(self):
        """Revisión, hallazgo 4: un proceso muerto entre crear y escribir."""
        lock = self.project / ".state.lock"
        lock.write_text("")
        viejo = time.time() - 5
        os.utime(lock, (viejo, viejo))
        self.assertLess(self._acquire(timeout=3.0), 1.0)

    def test_flock_que_falla_usa_el_modo_anterior_sin_marca(self):
        """Revisión, hallazgo 2: NFS o permisos. El lock funciona como antes y no
        escribe una marca que otro proceso podría creer."""
        import errno
        from unittest import mock

        sys.path.insert(0, str(SCRIPTS))
        import state_core

        original = state_core.STATE_FILE_PATH
        state_core.STATE_FILE_PATH = self.project / "03_project_state.json"
        self.addCleanup(setattr, state_core, "STATE_FILE_PATH", original)
        fallo = OSError(errno.EOPNOTSUPP, "flock no soportado")
        with mock.patch.object(state_core.fcntl, "flock", side_effect=fallo), \
                mock.patch.object(state_core, "log_err"):
            with state_core.state_write_lock(timeout=1, stale=60):
                contenido = (self.project / ".state.lock").read_text()
        self.assertNotIn("flock:", contenido)
        self.assertFalse((self.project / ".state.lock").exists())

    def test_directorio_recreado_entre_open_y_flock_no_da_el_lock(self):
        """Revisión, hallazgo 3: el candado debe proteger el directorio que hay
        ahora en la ruta, no uno renombrado."""
        sys.path.insert(0, str(SCRIPTS))
        import state_core

        viejo = self.project.parent / "_project.viejo"
        real_open = os.open
        llamadas = []

        def open_con_carrera(path, flags, *args):
            if Path(path) == self.project and not llamadas:
                llamadas.append(path)
                fd = real_open(path, flags, *args)
                os.rename(self.project, viejo)
                self.project.mkdir()
                return fd
            return real_open(path, flags, *args)

        from unittest import mock

        with mock.patch.object(state_core.os, "open", side_effect=open_con_carrera):
            status, fd = state_core._try_flock_directory(self.project)
        self.assertEqual((status, fd), ("busy", None))
        status, fd = state_core._try_flock_directory(self.project)
        self.addCleanup(state_core._release_directory_lock, fd, os.getpid())
        self.assertEqual(status, "held")

    def test_hijo_de_fork_que_sale_del_bloque_no_suelta_el_lock_del_padre(self):
        """Revisión, hallazgo 5: el LOCK_UN de un hijo liberaba el flock del padre."""
        sys.path.insert(0, str(SCRIPTS))
        import state_core

        original = state_core.STATE_FILE_PATH
        state_core.STATE_FILE_PATH = self.project / "03_project_state.json"
        self.addCleanup(setattr, state_core, "STATE_FILE_PATH", original)

        class _SalidaDelHijo(Exception):
            pass

        try:
            with state_core.state_write_lock(timeout=2, stale=60):
                pid = os.fork()
                if pid == 0:
                    raise _SalidaDelHijo
                os.waitpid(pid, 0)
                self.assertTrue((self.project / ".state.lock").exists(), "el hijo borró el lockfile del padre")
                otro = subprocess.run(
                    [sys.executable, "-c", self._script(_PRELUDE) + textwrap.dedent(
                        """
                        try:
                            with state_core.state_write_lock(timeout=0.5, stale=60):
                                print("ADQUIRIDO")
                        except RuntimeError:
                            print("EXCLUIDO")
                        """
                    )],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(otro.stdout.strip(), "EXCLUIDO", otro.stderr)
        except _SalidaDelHijo:
            os._exit(0)

    def _patched_state_core(self):
        sys.path.insert(0, str(SCRIPTS))
        import state_core

        original = state_core.STATE_FILE_PATH
        state_core.STATE_FILE_PATH = self.project / "03_project_state.json"
        self.addCleanup(setattr, state_core, "STATE_FILE_PATH", original)
        return state_core

    def test_excepcion_al_decidir_la_rotura_no_deja_el_flock_tomado(self):
        """Revisión de la implementación (2026-09-15), hallazgo 1: si `log_err`
        lanza (stderr del servidor MCP cerrado) con el flock en la mano, el
        servidor bloqueaba toda mutación mientras viviera."""
        from unittest import mock

        state_core = self._patched_state_core()
        lock = self.project / ".state.lock"
        lock.write_text("")
        viejo = time.time() - 5
        os.utime(lock, (viejo, viejo))
        fds_antes = len(os.listdir("/dev/fd"))
        with mock.patch.object(state_core, "log_err", side_effect=BrokenPipeError("stderr cerrado")):
            try:
                with state_core.state_write_lock(timeout=1, stale=60):
                    pass
            except BrokenPipeError:
                pass
        status, fd = state_core._try_flock_directory(self.project)
        self.addCleanup(state_core._release_directory_lock, fd, os.getpid()) if fd is not None else None
        self.assertEqual(status, "held", "el flock del directorio quedó retenido tras la excepción")
        state_core._release_directory_lock(fd, os.getpid())
        self.assertLessEqual(len(os.listdir("/dev/fd")), fds_antes, "fuga de descriptores")

    def test_marca_de_otro_montaje_del_mismo_nucleo_no_se_rompe_al_instante(self):
        """Revisión de la implementación, hallazgo 2: WSL2 o Docker Desktop
        comparten núcleo pero montan el directorio con otro dispositivo; el flock
        no excluye entre montajes, así que la marca debe fijar también el
        directorio."""
        state_core = self._patched_state_core()
        identity = state_core._kernel_identity()
        if identity is None:
            self.skipTest("sin identidad de núcleo en este entorno")
        lock = self.project / ".state.lock"
        # Titular vivo en otro montaje: mismo núcleo, otro dispositivo. Sin el
        # directorio en la marca, su marca era idéntica a la nuestra.
        for mark in (f"flock:{identity}", f"flock:{identity}:999999:1"):
            with self.subTest(mark=mark):
                lock.write_text(f"{self._dead_pid()} 2026-09-15T00:00:00+00:00 {mark}\n")
                with self.assertRaises(RuntimeError):
                    with state_core.state_write_lock(timeout=0.5, stale=60):
                        pass
                self.assertIn(mark, lock.read_text())

    def test_titular_vivo_sigue_excluyendo(self):
        """Preservar: con el titular vivo, el siguiente agota el timeout con el
        mensaje de siempre."""
        self._start_holder()
        with self.assertRaises(RuntimeError) as ctx:
            self._acquire(timeout=0.5)
        self.assertIn("otra mutación en curso", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
