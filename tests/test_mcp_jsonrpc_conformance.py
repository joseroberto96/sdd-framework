#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-234: el servidor MCP responde conforme a JSON-RPC 2.0 y a MCP.

Antes, un lote o un JSON que no era objeto se tragaban sin respuesta (el cliente
esperaba hasta su timeout), una petición sin `method` devolvía `-32603`, `ping`
no existía y un rechazo de validación de `mutate_state` llegaba al agente como
"Internal error". El bucle real (`run_mcp_server`) se ejecuta en proceso con un
gestor falso: aquí se prueba el protocolo, no el motor de estado.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_manager  # noqa: E402


class _FakeManager:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def mutate(self, action: str, payload: dict) -> str:
        self.calls.append((action, payload))
        if self.error is not None:
            raise self.error
        return "ok"


def _serve(lines: list[str], manager: _FakeManager | None = None) -> list:
    manager = manager or _FakeManager()
    stdin = io.StringIO("".join(line + "\n" for line in lines))
    stdout = io.StringIO()
    with mock.patch.object(state_manager, "install_fresh_hash_patch"), \
            mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout), \
            mock.patch.object(state_manager, "log_err"):
        state_manager.run_mcp_server(manager)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def _request(req_id, method, params=None) -> dict:
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _mutate(req_id, action="update_last_step", payload=None) -> dict:
    return _request(req_id, "tools/call", {
        "name": "mutate_state",
        "arguments": {"action": action, "payload": payload if payload is not None else {"last_completed_step": "x"}},
    })


class McpJsonRpcConformanceTests(unittest.TestCase):
    def assertInvalidRequest(self, response: dict, req_id) -> None:
        self.assertEqual(response.get("id"), req_id)
        self.assertEqual(response.get("error", {}).get("code"), -32600, response)

    def test_json_que_no_es_objeto_recibe_invalid_request(self):
        for raw in ('"hola"', "42", "null", "true"):
            with self.subTest(raw=raw):
                responses = _serve([raw])
                self.assertEqual(len(responses), 1, "sin respuesta: el cliente esperaría hasta su timeout")
                self.assertInvalidRequest(responses[0], None)

    def test_lote_vacio_recibe_invalid_request(self):
        responses = _serve(["[]"])
        self.assertEqual(len(responses), 1)
        self.assertInvalidRequest(responses[0], None)

    def test_lote_se_responde_con_un_array_por_peticion(self):
        """MCP 2025-03-26, que el servidor negocia, exige aceptar lotes."""
        batch = [_request(1, "ping"), {"jsonrpc": "2.0", "method": "notifications/initialized"}, _request(2, "tools/list"), 7]
        responses = _serve([json.dumps(batch)])
        self.assertEqual(len(responses), 1)
        self.assertIsInstance(responses[0], list)
        by_id = {item.get("id"): item for item in responses[0]}
        self.assertEqual(by_id[1]["result"], {})
        self.assertIn("tools", by_id[2]["result"])
        self.assertIn("lookup_state", [t["name"] for t in by_id[2]["result"]["tools"]])
        self.assertInvalidRequest(by_id[None], None)
        self.assertEqual(len(responses[0]), 3, "la notificación del lote no se responde")

    def test_lote_solo_de_notificaciones_no_emite_nada(self):
        batch = [{"jsonrpc": "2.0", "method": "notifications/initialized"}]
        self.assertEqual(_serve([json.dumps(batch)]), [])

    def test_peticion_sin_method_o_con_method_no_texto(self):
        responses = _serve([json.dumps({"jsonrpc": "2.0", "id": 2}), json.dumps({"jsonrpc": "2.0", "id": 3, "method": 7})])
        self.assertEqual(len(responses), 2)
        self.assertInvalidRequest(responses[0], 2)
        self.assertInvalidRequest(responses[1], 3)

    def test_ping_devuelve_resultado_vacio(self):
        self.assertEqual(_serve([json.dumps(_request(4, "ping"))]), [{"jsonrpc": "2.0", "id": 4, "result": {}}])

    def test_peticion_con_id_a_metodo_de_notificacion_se_responde(self):
        responses = _serve([json.dumps(_request(5, "notifications/initialized"))])
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["id"], 5)
        self.assertEqual(responses[0]["error"]["code"], -32601)

    def test_notificacion_tools_call_ni_se_ejecuta_ni_se_responde(self):
        manager = _FakeManager()
        notification = _mutate(None)
        del notification["id"]
        self.assertEqual(_serve([json.dumps(notification)], manager), [])
        self.assertEqual(manager.calls, [])

    def test_rechazo_de_mutate_state_es_error_de_herramienta(self):
        """El agente debe leer el rechazo, no un "Internal error" de protocolo."""
        for error in (ValueError("Transición ilegal para 'x'"), RuntimeError("No se pudo adquirir el lock")):
            with self.subTest(error=type(error).__name__):
                responses = _serve([json.dumps(_mutate(7))], _FakeManager(error))
                self.assertEqual(len(responses), 1)
                result = responses[0].get("result", {})
                self.assertTrue(result.get("isError"), responses[0])
                self.assertIn(str(error), result["content"][0]["text"])

    def test_fallo_interno_real_sigue_siendo_32603(self):
        responses = _serve([json.dumps(_mutate(8))], _FakeManager(KeyError("inesperado")))
        self.assertEqual(responses[0]["error"]["code"], -32603)

    def test_preservar_parse_error_y_tools_list(self):
        responses = _serve(["{no json", json.dumps(_request(9, "tools/list"))])
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 9)
        self.assertIn("tools", responses[1]["result"])
        self.assertIn("lookup_state", [t["name"] for t in responses[1]["result"]["tools"]])


if __name__ == "__main__":
    unittest.main()
