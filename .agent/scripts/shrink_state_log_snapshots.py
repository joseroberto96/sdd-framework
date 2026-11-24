#!/usr/bin/env python3
"""ADR-139/FW-201 — shrink one-shot: trunca a 16 hex las huellas de línea de
TODOS los `derived_snapshot` ya materializados (sidecars de
`_project/approval_snapshots/`, refs en `03_project_state.json`, y el
`derived_snapshot` inline que los eventos históricos `record_approval_receipt`/
`refresh_approval_receipt` de `state_log.jsonl` conservan desde antes de
ADR-130) y recalcula la hash-chain del log desde el primer evento tocado.

La corre CADA proyecto adoptante una vez tras subir a esta versión del
framework, igual que la migración de ADR-130/FW-185 (retirada en 5.3.1) — este
script es su continuación: aquel movió el snapshot a un store
content-addressed pero dejó los eventos históricos del log con el blob
completo inline (297 eventos, ~58 MB en Example Lab), y usaba huellas de
línea de 64 hex (SHA-256 completo) donde solo hace falta comparar igualdad —
ver `workflow_receipts._truncated_hash` para el porqué de 16 hex.

Fail-closed: el log recalculado se verifica (`state_core.verify_log_chain` +
`replay_state_log.replay(dry_run=True)`) en un fichero TEMPORAL antes de tocar
nada real. Si cualquiera de las dos verificaciones falla, no se escribe nada
— ni siquiera el backup — y el repo queda exactamente como estaba.

Idempotente: una segunda corrida detecta que todo está ya en formato v2
(`"fmt": 2` en cada snapshot) y no toca nada.

Uso:
  python3 .agent/scripts/shrink_state_log_snapshots.py            # dry-run (informe)
  python3 .agent/scripts/shrink_state_log_snapshots.py --write    # aplica
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import replay_state_log as _replay  # noqa: E402
import state_core as _sc  # noqa: E402
import workflow_receipts as _wr  # noqa: E402

_APPROVAL_ACTIONS = ("record_approval_receipt", "refresh_approval_receipt")
_SNAPSHOT_LIST_KEYS = (
    "active_scope", "acceptance", "data_model", "api_contract", "contracts", "decisions",
)


def _is_v2(snapshot: object) -> bool:
    return isinstance(snapshot, dict) and snapshot.get("fmt") == 2


def _truncate_snapshot(snapshot: dict) -> dict:
    """Trunca por slicing de string las huellas ya calculadas de un snapshot
    (NO re-deriva desde los documentos del proyecto — esos pudieron cambiar
    desde el receipt histórico; truncar el hash ya escrito es la operación
    correcta y determinista: `h[:16]` es exactamente lo que
    `_truncated_hash` habría producido desde cero para el mismo contenido)."""
    result: dict = {"fmt": 2}
    for key in _SNAPSHOT_LIST_KEYS:
        values = snapshot.get(key, [])
        result[key] = (
            [h[: _wr._SNAPSHOT_FINGERPRINT_LEN] if isinstance(h, str) else h for h in values]
            if isinstance(values, list) else values
        )
    module_spec = snapshot.get("module_spec", "")
    result["module_spec"] = (
        module_spec[: _wr._SNAPSHOT_FINGERPRINT_LEN] if isinstance(module_spec, str) else module_spec
    )
    return result


def _scan_sidecars(store: Path) -> dict[str, tuple[str, dict]]:
    """Solo lectura: `old_digest -> (new_digest, contenido_truncado)` para
    cada sidecar aún en v1. No escribe nada."""
    plan: dict[str, tuple[str, dict]] = {}
    if not store.exists():
        return plan
    for path in sorted(store.glob("*.json")):
        old_digest = path.stem
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # sidecar ilegible: no se toca, no se cuenta
        if not isinstance(snapshot, dict) or _is_v2(snapshot):
            continue
        truncated = _truncate_snapshot(snapshot)
        new_digest = _wr._stable_hash(truncated)
        plan[old_digest] = (new_digest, truncated)
    return plan


def _plan_state_refs(state: dict, rename_map: dict[str, str]) -> tuple[list[str], list[str]]:
    """Solo lectura: qué módulos de `workflow_receipts.approvals` tienen un
    `derived_snapshot_ref` a re-apuntar, o un `derived_snapshot` inline legacy
    a truncar. Devuelve (modules_a_re_apuntar, modules_con_inline_legacy)."""
    approvals = state.get("workflow_receipts", {}).get("approvals", {})
    refs_to_update: list[str] = []
    inline_legacy: list[str] = []
    for module, receipt in approvals.items():
        if not isinstance(receipt, dict):
            continue
        inline = receipt.get("derived_snapshot")
        if isinstance(inline, dict) and not _is_v2(inline):
            inline_legacy.append(module)
            continue
        ref = receipt.get("derived_snapshot_ref")
        if isinstance(ref, str) and ref in rename_map:
            refs_to_update.append(module)
    return refs_to_update, inline_legacy


def _apply_state_refs(state: dict, rename_map: dict[str, str]) -> None:
    approvals = state.get("workflow_receipts", {}).get("approvals", {})
    for receipt in approvals.values():
        if not isinstance(receipt, dict):
            continue
        inline = receipt.get("derived_snapshot")
        if isinstance(inline, dict) and not _is_v2(inline):
            receipt["derived_snapshot"] = _truncate_snapshot(inline)
            continue
        ref = receipt.get("derived_snapshot_ref")
        if isinstance(ref, str) and ref in rename_map:
            receipt["derived_snapshot_ref"] = rename_map[ref]


def _rewrite_log_events(
    events: list[dict], rename_map: dict[str, str]
) -> tuple[int | None, int, int]:
    """Trunca in-place el `derived_snapshot` inline y re-apunta
    `derived_snapshot_ref` de cada evento de aprobación tocado. Devuelve
    (índice del primer evento tocado o None, nº de eventos tocados, de esos
    cuántos carecían de campo `hash` propio — pre-cadena)."""
    first_touched: int | None = None
    touched = 0
    pre_chain_touched = 0
    for i, event in enumerate(events):
        if event.get("action") not in _APPROVAL_ACTIONS:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        receipt = payload.get("_receipt")
        if not isinstance(receipt, dict):
            continue
        changed = False
        inline = receipt.get("derived_snapshot")
        if isinstance(inline, dict) and not _is_v2(inline):
            receipt["derived_snapshot"] = _truncate_snapshot(inline)
            changed = True
        ref = receipt.get("derived_snapshot_ref")
        if isinstance(ref, str) and ref in rename_map:
            receipt["derived_snapshot_ref"] = rename_map[ref]
            changed = True
        if changed:
            touched += 1
            if "hash" not in event:
                pre_chain_touched += 1
            if first_touched is None:
                first_touched = i
    return first_touched, touched, pre_chain_touched


def _recompute_chain(events: list[dict], first_touched: int | None) -> None:
    """Recalcula `prev`/`hash` en cascada desde `first_touched` (in-place).

    Eventos anteriores a `first_touched` no se tocan. Un evento sin `hash`
    propio (pre-cadena) no gana el campo, pero SÍ participa en el arrastre de
    `prev_hash` hacia el primer evento encadenado posterior — si no se
    recalculara ese arrastre, el primer evento con `hash` real tras la
    frontera pre-cadena→cadena quedaría con un `prev` que ya no coincide con
    el contenido (posiblemente truncado) del evento anterior, y
    `verify_log_chain` fallaría justo ahí."""
    if first_touched is None:
        return
    prev_hash: str | None = None
    for event in events[:first_touched]:
        prev_hash = event["hash"] if "hash" in event else _sc._event_hash(event)
    for i in range(first_touched, len(events)):
        event = events[i]
        if "hash" in event:
            event["prev"] = prev_hash
            event["hash"] = _sc._event_hash(event)
            prev_hash = event["hash"]
        else:
            prev_hash = _sc._event_hash(event)


def _tip_hash(events: list[dict]) -> str | None:
    if not events:
        return None
    last = events[-1]
    return last["hash"] if "hash" in last else _sc._event_hash(last)


def _write_events(path: Path, events: list[dict]) -> None:
    """Mismo formato de línea que `state_core.StateManager.append_event`:
    `json.dumps(entry, ensure_ascii=False, sort_keys=True)` — SIN separadores
    compactos (esos solo se usan dentro de `_event_hash` para el cálculo
    canónico del hash, nunca en la persistencia de la línea)."""
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _atomic_write_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".shrink-", suffix=".json.tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup(path: Path, stamp: str) -> Path:
    bak = path.with_name(path.name + f".bak-{stamp}")
    bak.write_bytes(path.read_bytes())
    return bak


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.glob("*.json") if p.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="Aplica el shrink (por defecto: dry-run)")
    parser.add_argument("--state", type=Path, default=ROOT / "_project" / "03_project_state.json")
    parser.add_argument("--log", type=Path, default=ROOT / "_project" / "state_log.jsonl")
    args = parser.parse_args()

    state_path: Path = args.state
    log_path: Path = args.log
    if not state_path.exists():
        print(f"ERROR: estado no encontrado: {state_path}", file=sys.stderr)
        return 1
    if not log_path.exists():
        print(f"ERROR: log no encontrado: {log_path}", file=sys.stderr)
        return 1
    root = state_path.parent.parent
    store = root / "_project" / _wr.APPROVAL_SNAPSHOT_DIR

    # state_write_lock() deriva el path del lockfile de state_core.STATE_FILE_PATH
    # (global de módulo), no de `args.state` — sin fijarlo aquí, correr el script
    # con --state/--log apuntando a otro sitio (tests, o un layout no estándar)
    # tomaría el lock del repo REAL en vez del árbol que se está tocando. En
    # producción args.state == STATE_FILE_PATH por defecto, así que esto es un
    # no-op ahí.
    _sc.STATE_FILE_PATH = state_path

    with _sc.state_write_lock():
        # --- fase de solo lectura: qué hay que tocar --------------------
        sidecar_plan = _scan_sidecars(store)
        rename_map = {old: new for old, (new, _) in sidecar_plan.items()}

        state = json.loads(state_path.read_text(encoding="utf-8"))
        refs_to_update, inline_legacy = _plan_state_refs(state, rename_map)

        lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        events = [json.loads(l) for l in lines]
        tip_before = _tip_hash(events)
        # `_rewrite_log_events` muta los dicts que recibe (incluidos los
        # anidados payload/_receipt/derived_snapshot) para poder contar de
        # forma fiel qué cambiaría. Un `dict(e)` (copia superficial) NO basta
        # — payload/_receipt siguen siendo el MISMO objeto que en `events`, y
        # mutarlos aquí dejaría `events` ya truncado antes de la fase de
        # escritura real, dejando `first_touched` en None la segunda vez y sin
        # recalcular la cadena. `copy.deepcopy` aísla de verdad esta pasada de
        # solo-reporte de la pasada de escritura de más abajo.
        first_touched, touched, pre_chain_touched = _rewrite_log_events(
            copy.deepcopy(events), rename_map
        )

        sidecars_n = len(sidecar_plan)
        state_touched_n = len(refs_to_update) + len(inline_legacy)
        nothing_to_do = sidecars_n == 0 and state_touched_n == 0 and touched == 0

        state_before = state_path.stat().st_size
        log_before = log_path.stat().st_size
        store_before = _dir_size(store)

        print(f"Sidecars en formato v1 a truncar: {sidecars_n}")
        print(f"Recibos en 03_project_state.json a actualizar: {state_touched_n} "
              f"({len(refs_to_update)} ref, {len(inline_legacy)} inline legacy)")
        print(f"Eventos de state_log.jsonl a truncar: {touched} "
              f"({pre_chain_touched} pre-cadena, {touched - pre_chain_touched} encadenados)")
        print(f"state_log.jsonl: {log_before / 1024 / 1024:.1f} MB")
        print(f"approval_snapshots/: {store_before / 1024 / 1024:.1f} MB")

        if nothing_to_do:
            print("\nNada que hacer — todo ya está en formato v2.")
            return 0

        if not args.write:
            print("\n(dry-run) repetir con --write para aplicar")
            return 0

        # --- fase de escritura: re-hacer el rewrite sobre los eventos reales ---
        first_touched, touched, pre_chain_touched = _rewrite_log_events(events, rename_map)
        _recompute_chain(events, first_touched)
        tip_after = _tip_hash(events)

        tmp_log = log_path.with_suffix(log_path.suffix + ".new")
        _write_events(tmp_log, events)

        problems = _sc.verify_log_chain(tmp_log)
        if problems:
            tmp_log.unlink(missing_ok=True)
            print("ERROR: la cadena recalculada NO verifica. Nada escrito.", file=sys.stderr)
            for p in problems[:5]:
                print(f"  {p}", file=sys.stderr)
            return 2

        try:
            _replay.replay(tmp_log, tmp_log.parent / "_shrink_replay_unused.json", dry_run=True, strict=True)
        except _replay.ReplayError as e:
            tmp_log.unlink(missing_ok=True)
            print(f"ERROR: el log recalculado no reconstruye vía replay dry-run. Nada escrito.\n  {e}", file=sys.stderr)
            return 2

        # Verificado — a partir de aquí se publica.
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        log_bak = _backup(log_path, stamp)
        state_bak = _backup(state_path, stamp)

        for _old_digest, (_new_digest, content) in sidecar_plan.items():
            _wr.store_derived_snapshot(root, content)  # aditivo, idempotente

        os.replace(tmp_log, log_path)

        _apply_state_refs(state, rename_map)
        _atomic_write_json(state_path, state)

        removed_sidecars = 0
        for old_digest, (new_digest, _content) in sidecar_plan.items():
            if old_digest == new_digest:
                continue
            old_sidecar = store / f"{old_digest}.json"
            if old_sidecar.exists():
                old_sidecar.unlink()
                removed_sidecars += 1

        log_after = log_path.stat().st_size
        store_after = _dir_size(store)

        rebase_info = {
            "rebased_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "reason": "ADR-139/FW-201: truncado 64->16 hex de huellas de linea en approval_derived_snapshot",
            "tip_before": tip_before,
            "tip_after": tip_after,
            "events_rewritten": touched,
            "events_pre_chain_touched": pre_chain_touched,
            "sidecars_renamed": sidecars_n,
            "sidecars_removed": removed_sidecars,
            "refs_updated_in_state": len(refs_to_update),
            "inline_legacy_truncated_in_state": len(inline_legacy),
            "size_before_bytes": {"state_log": log_before, "state_json": state_before, "approval_snapshots_dir": store_before},
            "size_after_bytes": {"state_log": log_after, "state_json": state_path.stat().st_size, "approval_snapshots_dir": store_after},
            "backups": {"state_log": str(log_bak), "state_json": str(state_bak)},
        }
        _atomic_write_json(root / "_project" / "state_log_rebase.json", rebase_info)

        print(f"\nstate_log.jsonl: {log_before / 1024 / 1024:.1f} MB -> {log_after / 1024 / 1024:.1f} MB")
        print(f"approval_snapshots/: {store_before / 1024 / 1024:.1f} MB -> {store_after / 1024 / 1024:.1f} MB")
        print(f"Backups: {log_bak}, {state_bak}")
        print("Provenance: _project/state_log_rebase.json")
        print("Regenera proyecciones: python3 .agent/scripts/state_manager.py render")
        return 0


if __name__ == "__main__":
    sys.exit(main())
