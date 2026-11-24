#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transacción multiarchivo del motor de estado (FW-174, retroportado de Boton).

Una mutación publica estado, proyecciones y bitácora. No existe un rename
atómico de varios nombres independientes, así que un crash a mitad dejaba una
vista incoherente. El manifiesto convierte esa limitación física en una
transacción crash-consistent: el siguiente lector restaura los bytes exactos
previos antes de servir nada.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core


class MutationManifestTests(unittest.TestCase):
    def test_la_allowlist_cubre_todo_lo_que_publica_una_mutacion(self):
        """El manifiesto debe abarcar exactamente los ficheros que una mutación
        toca; si publicara uno fuera de la allowlist, la recuperación lo
        dejaría atrás."""
        paths = {p.name for p in state_core.StateManager._mutation_paths()}

        self.assertEqual(
            {
                "03_project_state.json", "state_log.jsonl", "core.md",
                "06_bug_fixes.md", "07_pending_clarifications.md",
                "03_arch_decisions.md", "00_session_brief.json",
                # FW-225: registro de cambios del modo mantenimiento.
                "24_change_log.md",
            },
            paths,
        )

    def test_el_manifiesto_esta_excluido_del_hash_de_diff(self):
        """Sin esta exclusión, abrir una transacción cambia el hash del
        workspace e invalida todo execution receipt vigente."""
        import workflow_receipts

        self.assertIn(
            "_project/.state_transaction.json", workflow_receipts.RECEIPT_EXCLUDED_PATHS
        )

    def test_la_allowlist_y_la_exclusion_son_el_mismo_conjunto(self):
        """Coherencia: lo que la transacción restaura es exactamente lo que el
        hash de diff ignora. Si divergen, o se corrompe una vista o se
        invalidan recibos sin motivo."""
        import workflow_receipts

        allowlist = {f"_project/{p.name}" for p in state_core.StateManager._mutation_paths()}
        excluded = set(workflow_receipts.RECEIPT_EXCLUDED_PATHS) - {
            "_project/.state_transaction.json"
        }

        self.assertEqual(allowlist, excluded)

    def test_un_manifiesto_con_rutas_fuera_de_la_allowlist_se_rechaza(self):
        """Vector de escritura arbitraria: un manifiesto manipulado no puede
        hacer que la recuperación escriba fuera de `_project/`."""
        raw = {"version": 1, "files": {"/etc/passwd": None}}
        manifest = state_core.StateManager._mutation_manifest_path()
        original = manifest.read_bytes() if manifest.exists() else None
        try:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                state_core.StateManager._recover_pending_mutation()

            self.assertIn("no recuperable", str(ctx.exception))
        finally:
            if original is None:
                manifest.unlink(missing_ok=True)
            else:
                manifest.write_bytes(original)

    def test_recuperacion_restaura_los_bytes_previos(self):
        """El caso real: el proceso muere con el manifiesto puesto y un fichero
        ya sobrescrito. El siguiente lector debe devolverlo a su contenido
        exacto anterior."""
        import base64

        core = state_core.CORE_FILE_PATH
        manifest = state_core.StateManager._mutation_manifest_path()
        original = core.read_bytes() if core.exists() else None
        manifest_original = manifest.read_bytes() if manifest.exists() else None
        try:
            previo = b"# contenido previo a la mutacion\n"
            core.parent.mkdir(parents=True, exist_ok=True)
            core.write_bytes(previo)
            state_core.StateManager._write_mutation_manifest({core: previo})
            # el "crash": la proyección queda a medio escribir
            core.write_bytes(b"# vista a medio escribir")

            state_core.StateManager._recover_pending_mutation()

            self.assertEqual(previo, core.read_bytes())
            self.assertFalse(manifest.exists(), "el manifiesto debe quedar limpio")
        finally:
            if original is None:
                core.unlink(missing_ok=True)
            else:
                core.write_bytes(original)
            if manifest_original is None:
                manifest.unlink(missing_ok=True)
            else:
                manifest.write_bytes(manifest_original)


class LockSafetyAndLivenessTests(unittest.TestCase):
    def test_read_lock_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".state.lock"
            # Inexistente
            self.assertIsNone(state_core._read_lock_pid(lock))

            # Vacio
            lock.write_text("", encoding="utf-8")
            self.assertIsNone(state_core._read_lock_pid(lock))

            # No numerico
            lock.write_text("not_a_pid timestamp\n", encoding="utf-8")
            self.assertIsNone(state_core._read_lock_pid(lock))

            # PID <= 0
            lock.write_text("0 timestamp\n", encoding="utf-8")
            self.assertIsNone(state_core._read_lock_pid(lock))
            lock.write_text("-123 timestamp\n", encoding="utf-8")
            self.assertIsNone(state_core._read_lock_pid(lock))

            # Valido
            lock.write_text("12345 2026-09-12T10:00:00Z\n", encoding="utf-8")
            self.assertEqual(state_core._read_lock_pid(lock), 12345)

    def test_is_process_alive(self):
        from unittest import mock
        # Proceso actual siempre vivo
        self.assertTrue(state_core._is_process_alive(os.getpid()))

        # PID <= 0 siempre falso
        self.assertFalse(state_core._is_process_alive(0))
        self.assertFalse(state_core._is_process_alive(-1))

        # Proceso inexistente (ProcessLookupError)
        with mock.patch("os.kill", side_effect=ProcessLookupError):
            self.assertFalse(state_core._is_process_alive(99999))

        # Proceso vivo de otro usuario (PermissionError)
        with mock.patch("os.kill", side_effect=PermissionError):
            self.assertTrue(state_core._is_process_alive(99999))

        # Fallback defensivo Windows/no-POSIX (ValueError)
        with mock.patch("os.kill", side_effect=ValueError("signal 0 not supported")):
            self.assertTrue(state_core._is_process_alive(99999))

        # Fallback defensivo OSError generico
        with mock.patch("os.kill", side_effect=OSError("unexpected")):
            self.assertTrue(state_core._is_process_alive(99999))

    def test_state_write_lock_does_not_break_stale_lock_if_process_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig_state_path = state_core.STATE_FILE_PATH
            try:
                state_core.STATE_FILE_PATH = Path(tmp) / "03_project_state.json"
                lock_path = Path(tmp) / ".state.lock"
                # Crear lockfile que aparenta ser de nuestro propio proceso pero muy viejo
                lock_path.write_text(f"{os.getpid()} 2026-01-01T00:00:00Z\n", encoding="utf-8")
                old_time = 1_000_000.0
                os.utime(lock_path, (old_time, old_time))

                # Intentar adquirir con timeout corto: como el proceso está vivo, NO debe romper el lock
                with self.assertRaises(RuntimeError) as ctx:
                    with state_core.state_write_lock(timeout=0.1, stale=10.0):
                        pass
                self.assertIn("otra mutación en curso", str(ctx.exception))
                self.assertIn(f"proceso {os.getpid()}", str(ctx.exception))
                # El lockfile debe seguir existiendo intacto
                self.assertTrue(lock_path.exists())
            finally:
                state_core.STATE_FILE_PATH = orig_state_path

    def test_state_write_lock_breaks_stale_lock_if_process_dead(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            orig_state_path = state_core.STATE_FILE_PATH
            try:
                state_core.STATE_FILE_PATH = Path(tmp) / "03_project_state.json"
                lock_path = Path(tmp) / ".state.lock"
                # PID ficticio garantizado como muerto vía mock
                with mock.patch("state_core._is_process_alive", return_value=False):
                    lock_path.write_text("88888 2026-01-01T00:00:00Z\n", encoding="utf-8")
                    old_time = 1_000_000.0
                    os.utime(lock_path, (old_time, old_time))

                    with state_core.state_write_lock(timeout=0.5, stale=10.0):
                        # Lock adquirido por nosotros
                        content = lock_path.read_text(encoding="utf-8")
                        self.assertIn(str(os.getpid()), content)
                    # Al salir de finally, se limpia
                    self.assertFalse(lock_path.exists())
            finally:
                state_core.STATE_FILE_PATH = orig_state_path

    def test_finally_does_not_unlink_foreign_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig_state_path = state_core.STATE_FILE_PATH
            try:
                state_core.STATE_FILE_PATH = Path(tmp) / "03_project_state.json"
                lock_path = Path(tmp) / ".state.lock"

                with state_core.state_write_lock(timeout=1.0, stale=60.0):
                    self.assertTrue(lock_path.exists())
                    # Simulamos que otro proceso se apropia del lockfile antes del finally
                    lock_path.write_text("77777 2026-09-12T10:00:00Z\n", encoding="utf-8")

                # Al salir de finally, NO debe borrar el lockfile ajeno (PID 77777)
                self.assertTrue(lock_path.exists())
                self.assertIn("77777", lock_path.read_text(encoding="utf-8"))
            finally:
                state_core.STATE_FILE_PATH = orig_state_path


if __name__ == "__main__":
    unittest.main()
