#!/usr/bin/env python3
"""L3.4 de la 5.3.1: el marcador de trabajo sin terminar (PA-FW-006).

Dentro de un comentario se busca en cualquier caja (`# todo: …`); en el código y
en las cadenas, solo en mayúsculas (`TODO`, `FIXME`, `HACK`, `XXX`). Así «No se
pudo comparar todo: falta…» en una cadena deja de bloquear y `TODO(` en código
sigue bloqueando. `stub-ok: <motivo>` sigue siendo la salida.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import validate_no_stubs  # noqa: E402

LABEL = "marcador de trabajo sin terminar"

# (fichero, contenido, ¿bloquea?)
CASES = [
    ("portfolio.js", 'const aviso = "No se pudo comparar todo: falta parte de los datos de cartera";', False),
    ("Resumen.jsx", "  <p>No se pudo comparar todo: falta parte de los datos</p>", False),
    ("informe.py", 'mensaje = "Todo: listo para exportar"', False),
    ("vista.ts", "const etiqueta = `todo(s) los activos`;", False),
    ("informe.py", "# todo: arreglar el redondeo", True),
    ("api.js", "// FIXME: invalidar la caché", True),
    ("Main.kt", 'val x = TODO("implementar")', True),
    ("informe.py", "x = 1  # TODO(pepe): quitar", True),
    ("api.ts", "/* todo: revisar el contrato */", True),
    ("consulta.sql", "-- hack: índice temporal", True),
    ("informe.py", 'print("TODO: implementar")', True),
    ("deploy.sh", "echo listo  # xxx: quitar antes de producción", True),
    ("api.ts", "const x = 1; // todo: quitar", True),
    ("index.php", "$x = 1; # fixme: validar", True),
    ("api.go", "/*\n * todo: documentar el contrato\n */", True),
    ("Resumen.jsx", "{/* todo: estado vacío */}", True),
    ("informe.py", "url = 'https://ejemplo.local/todo:lista'", False),
]


def scan(name, content):
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        path = root / "src" / name
        path.parent.mkdir()
        path.write_text(content + "\n", encoding="utf-8")
        return validate_no_stubs.scan_file(path, root)


class UnfinishedMarkerTests(unittest.TestCase):
    def test_casos(self):
        for name, content, blocks in CASES:
            with self.subTest(name=name, content=content):
                found = scan(name, content)
                self.assertEqual(any(LABEL in f for f in found), blocks, found)

    def test_stub_ok_sigue_siendo_la_salida(self):
        found = scan("api.js", "// TODO: quitar  stub-ok: el cliente lo pide así hasta enero")
        self.assertFalse(any(LABEL in f for f in found), found)

    def test_docstring_de_python_sigue_fuera(self):
        found = scan("informe.py", '"""\nTODO: esto es documentación\n"""\nx = 1')
        self.assertFalse(any(LABEL in f for f in found), found)


if __name__ == "__main__":
    unittest.main()
