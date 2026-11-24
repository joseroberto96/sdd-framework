#!/usr/bin/env python3
"""FW-357 (5.3.1 L4.3): saca los mapas de identidades de test de los recibos de aprobación.

Los recibos nuevos citan su mapa (`test_identities_ref`, `test_identities_origin_ref`)
en el almacén `_project/approval_snapshots/`, que nombra cada fichero por su digest
y lo verifica al leerlo; los anteriores lo llevan entero en cada recibo. Los dos se
leen igual: la migración es opcional y no destructiva (toma el candado del estado,
comprueba que cada mapa se lee igual, deja la copia `.fw357-<fecha>.bak`, que el
`*.bak` del `.gitignore` ignora, y escribe de forma atómica). No reescribe
`state_log.jsonl`: tras un replay, vuelve a correrla.

Uso: python3 .agent/scripts/migrate_test_identities.py [--state RUTA] [--write]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow_receipts as _wr  # noqa: E402

KEYS = ("test_identities", "test_identities_origin")


def _inline_maps(state: dict) -> list[tuple[dict, str]]:
    approvals = state.get("workflow_receipts", {}).get("approvals", {})
    return [
        (receipt, key) for receipt in approvals.values() for key in KEYS
        if isinstance(receipt, dict) and isinstance(receipt.get(key), dict)
    ]


def migrate_state(state: dict, root: Path) -> int:
    """Sustituye cada mapa inline por su referencia; devuelve cuántos movió."""
    pending = _inline_maps(state)
    for receipt, key in pending:
        receipt[f"{key}_ref"] = _wr.store_derived_snapshot(root, receipt.pop(key))
    return len(pending)


def _write_atomic(path: Path, state: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".migrate-", suffix=".json.tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="aplica la migración (por defecto, solo informa)")
    parser.add_argument("--state", type=Path, default=ROOT / "_project" / "03_project_state.json")
    args = parser.parse_args(argv)
    path: Path = args.state
    if not path.is_file():
        print(f"ERROR: estado no encontrado: {path}", file=sys.stderr)
        return 1
    import state_core
    original, state_core.STATE_FILE_PATH = state_core.STATE_FILE_PATH, path  # candado del árbol migrado
    try:
        return _run(path, args.write, state_core)
    finally:
        state_core.STATE_FILE_PATH = original


def _run(path: Path, write: bool, state_core) -> int:
    root = path.parent.parent
    with state_core.state_write_lock():
        state_core.StateManager.ensure_transaction_clean(lock_held=True)
        raw = path.read_bytes()
        state = json.loads(raw)
        pending = _inline_maps(state)
        size = sum(len(json.dumps(receipt[key])) for receipt, key in pending)
        print(f"Mapas de identidades inline: {len(pending)} ({size / 1024 / 1024:.1f} MB en el estado)")
        if not pending or not write:
            print("Nada que migrar." if not pending else "(sin --write no se escribe nada)")
            return 0
        receipts = [r for r in state.get("workflow_receipts", {}).get("approvals", {}).values() if isinstance(r, dict)]
        before = [_wr._approval_identities(root, r) for r in receipts]
        migrate_state(state, root)
        try:
            if [_wr._approval_identities(root, r) for r in receipts] != before:
                raise ValueError("algún mapa no se lee igual desde el almacén")
            state_core.StateManager.__new__(state_core.StateManager).validate_state(state)
        except (ValueError, TypeError, KeyError) as exc:
            print(f"ERROR: la migración no es segura ({exc}); no se escribió nada.", file=sys.stderr)
            return 2
        backup = path.with_name(f"{path.name}.fw357-{time.strftime('%Y%m%d%H%M%S')}.bak")
        if backup.exists():
            print(f"ERROR: ya existe la copia {backup}; vuelve a intentarlo.", file=sys.stderr)
            return 2
        backup.write_bytes(raw)
        _write_atomic(path, state)
    print(f"Estado: {len(raw)} → {path.stat().st_size} bytes. Copia: {backup}")
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(backup)], capture_output=True)
    if ignored.returncode != 0:
        print("AVISO: git no ignora la copia: cuenta en la huella del workspace y caducaría los recibos "
              "de revisión y ejecución vivos. Añade *.bak al .gitignore o sácala del proyecto.")
    print("Commitea `_project/approval_snapshots/` junto al estado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
