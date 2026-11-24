#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Higiene del audit trail (FW-173, retroportado de Boton).

`reason` y compañía son texto libre que el motor persiste para siempre en
`state_log.jsonl`, encadenado por hash: un secreto pegado ahí no se puede
"desregistrar". El motor lo rechaza en la entrada, ruidosamente, en vez de
redactarlo en silencio.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core


class AuditTextHygieneTests(unittest.TestCase):
    def _validate(self, value):
        return state_core.StateManager._validate_audit_text("reason", value)

    def test_texto_normal_pasa_y_se_recorta(self):
        self.assertEqual("motivo legítimo", self._validate("  motivo legítimo  "))

    def test_rechaza_credenciales_en_texto_libre(self):
        for value in (
            "el fix usa password: hunter2 en el arranque",
            "rotamos el API_KEY=abc123 del proveedor",
            "cabecera con bearer: xyz para el endpoint",
        ):
            with self.assertRaises(ValueError) as ctx:
                self._validate(value)
            self.assertIn("credenciales", str(ctx.exception))

    def test_rechaza_jwt(self):
        # El JWT se construye en ejecución: un literal con esa forma dispara —
        # con razón — el detector de secretos del pre-commit sobre este mismo
        # fichero. El gate hace su trabajo; el fixture no debe pelearse con él.
        import base64

        def segmento(payload: bytes) -> str:
            return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

        jwt = ".".join((
            segmento(b'{"alg":"HS256","typ":"JWT"}'),
            segmento(b'{"sub":"1234567890","name":"prueba"}'),
            "c2lnbmF0dXJhZGVwcnVlYmE",
        ))

        with self.assertRaises(ValueError) as ctx:
            self._validate(f"el token filtrado fue {jwt}")

        self.assertIn("sensibles", str(ctx.exception))

    def test_rechaza_numero_de_tarjeta(self):
        with self.assertRaises(ValueError):
            self._validate("el ticket citaba la tarjeta 4111 1111 1111 1111 del cliente")

    def test_rechaza_texto_desmedido(self):
        with self.assertRaises(ValueError) as ctx:
            self._validate("x" * (state_core.MAX_AUDIT_TEXT_LENGTH + 1))
        self.assertIn("límite", str(ctx.exception))

    def test_no_hereda_los_patrones_calibrados_a_producto(self):
        """Boton filtraba además móviles ecuatorianos, email y cualquier número
        de 10 dígitos. En el framework rechazarían un `reason` legítimo que cite
        una incidencia o un contacto, así que se quedaron fuera a propósito."""
        for legitimo in (
            "el módulo lo reportó ana@example.com tras el cierre fallido",
            "la incidencia 0912345678 documenta la regresión completa",
        ):
            self.assertEqual(legitimo, self._validate(legitimo))


if __name__ == "__main__":
    unittest.main()
