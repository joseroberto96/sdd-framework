"""Helper compartido para cargar un módulo .py por ruta en tests (patrón usado
en varios archivos de tests/ para importar scripts de .agent/scripts/ sin
depender de que estén en sys.path)."""
import importlib.util
from pathlib import Path
from types import ModuleType


def load_module_from_path(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
