"""5.3.3 — lo compartido de otro módulo caduca el recibo, como en main.

Las partes 2 y 3 de L7.3 (apartar en `data_model`, `api_contract`, `contracts`,
`prd`, `phase_plan` y `current_phase` lo que tiene otro dueño) quedan fuera de la
5.3.3 por los falsos negativos de cuatro revisiones adversariales. Regresión: en cada
una de esas seis dimensiones, un cambio en la sección de otro módulo caduca, y un
recibo nuevo no sella `spec_parts` ni `global_parts`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import workflow_receipts as wr
from test_l6b_contrato import SeccionesBase  # noqa: F401 (solo la base, sin sus tests)

DATA_MODEL = "# Modelo de datos\n\n### Pedido (M1)\n- total: decimal\n\n### Factura (M2)\n- importe: decimal\n"
CONTRACTS = "# Contratos\n\n### PedidoService — M1\n- crear(pedido) -> id\n\n### FacturaService — M2\n- emitir(pedido_id) -> factura\n"
API = (
    "openapi: 3.0.0\npaths:\n"
    "  /pedidos:\n    post:\n      x-module: M1\n      summary: Crea un pedido\n"
    "  /facturas:\n    post:\n      x-module: M2\n      summary: Emite una factura\n"
)
PRD = "# PRD\nVisión común\n\n## Pedidos (M1)\n- crear pedidos\n\n## Facturación (M2)\n- emitir factura\n"
PLAN = "# Plan técnico\nObjetivo común\n\n## M1 — pedidos\n- T1: crear pedidos\n\n## M2 — facturas\n- T1: emitir facturas\n"


class CompartidosCaducanComoEnMainTests(SeccionesBase):
    def setUp(self) -> None:
        super().setUp()
        plan = self.project / "02_technical_plan"
        plan.mkdir(parents=True, exist_ok=True)
        self.files = {
            "data_model": (plan / "data_model.md", DATA_MODEL, "- importe: decimal\n"),
            "contracts": (self.project / "03_contracts.md", CONTRACTS, "- emitir(pedido_id) -> factura\n"),
            "api_contract": (plan / "api_contract.yaml", API, "      summary: Emite una factura\n"),
            "prd": (self.project / "01_product_requirements.md", PRD, "- emitir factura\n"),
            "phase_plan": (plan / "phase_01.md", PLAN, "- T1: emitir facturas\n"),
        }
        for path, text, _ in self.files.values():
            path.write_text(text, encoding="utf-8")

    def test_un_cambio_en_lo_de_otro_modulo_caduca_en_cada_dimension(self) -> None:
        for dim, (path, text, line) in self.files.items():
            with self.subTest(dim=dim):
                self.approve()
                self.replace(path, line, line.rstrip("\n") + " (M2 lo reescribe)\n")
                self.assert_stale(dim)
                path.write_text(text, encoding="utf-8")

    def test_cambiar_de_fase_conservando_lo_propio_caduca(self) -> None:
        self.approve()
        (self.project / "02_technical_plan" / "phase_02.md").write_text(PLAN, encoding="utf-8")
        self.state["current_phase"] = "phase_02"
        self.assert_stale("current_phase")

    def test_el_recibo_no_sella_duenos_de_lo_compartido(self) -> None:
        self.approve()
        snapshot = wr.load_derived_snapshot(self.root, self.receipt())
        self.assertNotIn("spec_parts", snapshot)
        self.assertNotIn("global_parts", snapshot)


if __name__ == "__main__":
    unittest.main()
