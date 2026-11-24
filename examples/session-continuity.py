"""Run the real state CLI in a disposable example project."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


TEMPLATE = Path(__file__).resolve().parents[1]
NEXT_ACTION = "Añadir una prueba unitaria para la búsqueda de notas por título."


def main() -> None:
    with TemporaryDirectory(prefix="sdd-example-") as folder:
        project = Path(folder)
        for name in (".agent", "_project"):
            shutil.copytree(
                TEMPLATE / name,
                project / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        shutil.copy2(TEMPLATE / "llms.txt", project / "llms.txt")

        # Seed a new, synthetic project as the new-project workflow would.
        state_path = project / "_project" / "03_project_state.json"
        seed = json.loads(state_path.read_text(encoding="utf-8"))
        seed.update(
            framework_mode="initialized",
            project="Notas de ejemplo",
            execution_profile={"capability_tier": "standard"},
        )
        state_path.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")

        command = [sys.executable, "-B", ".agent/scripts/state_manager.py"]
        subprocess.run([*command, "bootstrap"], cwd=project, check=True)
        payload = json.dumps({"next_action": NEXT_ACTION, "next_command": "/ciclo"})
        subprocess.run(
            [*command, "mutate", "--action", "set_session_pointer", "--payload", payload],
            cwd=project,
            check=True,
        )

        result = subprocess.check_output(
            [*command, "query", "--view", "startup"], cwd=project, text=True,
        )
        startup = json.loads(result)
        summary = startup["data"]["summary"]
        if (
            startup["gate_status"] != "PASS"
            or summary["project"] != "Notas de ejemplo"
            or summary["next_action"] != NEXT_ACTION
            or summary["next_command"] != "/ciclo"
        ):
            raise SystemExit("La consulta no recuperó la continuidad registrada.")

        log = project / "_project" / "state_log.jsonl"
        last_event = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        if last_event["action"] != "set_session_pointer" or last_event["payload"]["next_action"] != NEXT_ACTION:
            raise SystemExit("La bitácora no contiene la mutación esperada.")

        print(f"Siguiente: {summary['next_command']} {summary['next_action']}")
        print("La siguiente acción se recuperó en un proceso nuevo y consta en la bitácora.")


if __name__ == "__main__":
    main()
