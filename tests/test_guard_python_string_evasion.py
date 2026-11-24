#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-236 (L8b, plan 5.2) revisado en la 5.3.1 (L1, S3).

En L8b la clase «código Python que borra» pedía confirmación aunque el nombre de
la función estuviera escondido en una cadena. S3 retira esa clase: el código en
línea es un script en fichero y solo cuenta la orden que lanza con argumentos
literales (mitigación de spawn, por AST). Resultado, caso por caso (D2):

- deny: las formas en que la llamada que lanza la orden tiene nombre propio
  (`system`, `popen`…), aunque llegue por `import_module`, `vars()` o
  `__import__`;
- pass: `getattr` con el nombre en una cadena, nombres partidos y borrados con
  `rmtree`/`unlink`, que no lanzan una orden. Son huecos aceptados en el dictamen
  `security` (argumentos dinámicos y APIs que no lanzan procesos).

`deny` sigue ganando siempre. Los comandos son sintéticos.
"""
from __future__ import annotations

import unittest

from test_guard_indirect_execution import hook

RM_HOME = "rm -rf ~"


class PythonStringEvasionS3Tests(unittest.TestCase):
    DENY = {
        "import_module": f"python3 -c \"import importlib; m=importlib.import_module('os'); m.system('{RM_HOME}')\"",
        "vars()['os'].system": f"python3 -c \"import os; vars()['os'].system('{RM_HOME}')\"",
        "__import__('os').system": f"python3 -c \"__import__('os').system('{RM_HOME}')\"",
    }
    PASS = {
        "getattr con cadena": "python3 -c \"import os,shutil; getattr(shutil,'rmtree')(os.path.expanduser('~'))\"",
        "nombre partido por adyacencia": "python3 -c \"import shutil,os; getattr(shutil,'rmt' 'ree')(os.path.expanduser('~'))\"",
        "nombre partido por suma": "python3 -c \"import shutil,os; getattr(shutil,'rmt'+'ree')(os.path.expanduser('~'))\"",
        "unlink en cadena": "python3 -c \"import os; getattr(os,'unlink')(os.path.expanduser('~/x'))\"",
        "stdin con getattr": "python3 - <<'PY'\nimport shutil,os\ngetattr(shutil,'rmtree')(os.path.expanduser('~'))\nPY",
        "stdin con __import__ y orden en el entorno": "python3 - <<'PY'\nimport os\n__import__('os').system(os.environ['C'])\nPY",
        "stdin con import_module": "python3 - <<'PY'\nimport importlib\nimportlib.import_module('shutil').rmtree('x')\nPY",
        "solo datos con cadena": "python3 -c \"print('remove the file')\"",
        "rmtree efímero literal": "python3 -c \"import shutil; shutil.rmtree('/tmp/x')\"",
    }

    def test_las_que_lanzan_una_orden_literal_se_deniegan(self):
        for name, cmd in self.DENY.items():
            with self.subTest(name):
                self.assertEqual(hook(cmd)[0], "deny", cmd)

    def test_huecos_aceptados_pasan(self):
        for name, cmd in self.PASS.items():
            with self.subTest(name):
                self.assertEqual(hook(cmd)[0], "pass", cmd)

    def test_deny_gana(self):
        verdict, _ = hook("python3 -c \"import shutil; getattr(shutil,'rmtree')('x')\"; rm -rf ~")
        self.assertEqual(verdict, "deny")


if __name__ == "__main__":
    unittest.main()
