#!/usr/bin/env python3
"""Gate de estado respaldado por la bitácora (N2, plan 5.2 L7).

CLI fino sobre `replay_state_log.verify_state_delta`, invocado por
`pre_commit.sh` con el estado y el log de HEAD y del índice.

Salida: una línea por problema, prefijada `ERROR:` o `WARN:`. Código de salida:
1 si hay algún ERROR, 0 en otro caso (los WARN informan y no bloquean).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_state_log  # noqa: E402


def _lines(path: str | None) -> list[str] | None:
    if not path or not Path(path).is_file():
        return None
    return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--head-state", required=True)
    ap.add_argument("--head-log", default="")
    ap.add_argument("--staged-state", required=True)
    ap.add_argument("--staged-log", default="")
    args = ap.parse_args()

    try:
        head_state = json.loads(Path(args.head_state).read_text(encoding="utf-8"))
        staged_state = json.loads(Path(args.staged_state).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: estado ilegible: {e}")
        return 1

    head_log = _lines(args.head_log)
    staged_log = _lines(args.staged_log)
    if not head_log:
        # Sin bitácora en HEAD no hay línea base contra la que verificar.
        if staged_log:
            print("WARN: sin línea base: HEAD no tiene state_log.jsonl (o está vacío); "
                  "el estado no se puede contrastar contra la bitácora en este commit.")
        return 0

    problems = replay_state_log.verify_state_delta(
        head_state, head_log, staged_state, staged_log or [], workspace_root=Path.cwd()
    )
    for p in problems:
        print(p)
    return 1 if any(p.startswith("ERROR") for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
