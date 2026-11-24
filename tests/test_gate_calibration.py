#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arnés de calibración de los 5 gates de enforcement (3.13.0).

Corre `gate_calibration.run_harness()` sobre el registro canónico y
asegura que la lista de fallas queda vacía. Más los meta-tests que
prueban que el arnés NO es ceremonial: inyectan un gate mockeado con
good que BLOQUEA o bad que ALLOW, y verifican que `run_harness` los
detecta. Sin este meta-test, un arnés que siempre pasa no se
distinguiría de uno que efectivamente ejecuta los gates — un patrón
de falsa confianza que ya mordió al framework en pre-3.5.0
(ADR-010, Frozen Core)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import gate_calibration  # noqa: E402


class TestCalibrationHarness(unittest.TestCase):
    """El arnés corre los 5 gates contra su known-good y known-bad."""

    def test_all_gates_calibrated(self):
        failures = gate_calibration.run_harness()
        self.assertEqual(
            failures, [],
            "calibración rota — un gate volvió a gritar lobo o dejó de bloquear:\n  - "
            + "\n  - ".join(failures),
        )

    def test_canonical_set_has_8_gates(self):
        """El conjunto canónico son los 5 gates del plan 3.13.0 + el gate de
        checkpoint (3.19.0, FW-074) + el Production Command Guard (FW-082,
        ADR-051) + el STUB GATE (4.21.0, FW-145). Si se añaden o quitan, hay que
        actualizar el doctor y este test en el mismo commit."""
        self.assertEqual(
            len(gate_calibration.GATES), 8,
            f"el canónico fija 8 gates; got {len(gate_calibration.GATES)}",
        )
        names = sorted(g["name"] for g in gate_calibration.GATES)
        self.assertEqual(
            names,
            ["guard_destructive_command", "pre_commit", "secret_scan", "validate_acceptance", "validate_checkpoint", "validate_closure", "validate_no_stubs", "validate_transition"],
        )


class TestHarnessIsNotCeremonial(unittest.TestCase):
    """Meta-tests: el arnés debe detectar gates que rompen su contrato.
    Si cualquiera de estos falla, el arnés está mintiendo y la
    calibración no se está ejerciendo de verdad."""

    def test_harness_catches_gate_whose_good_blocks(self):
        """Un gate cuyo 'good' ahora BLOQUEA debe hacer fallar el arnés."""
        fake = {
            "name": "fake-good-broken",
            "fw": "test",
            "good": lambda: (False, "the good fixture now blocks"),
            "bad": lambda: (True, "the bad fixture still blocks"),
        }
        failures = gate_calibration.run_harness([fake])
        self.assertTrue(
            any("fake-good-broken.good" in f and "debe ALLOW" in f for f in failures),
            f"el arnés debe detectar un good que BLOQUEA, got: {failures}",
        )

    def test_harness_catches_gate_whose_bad_allows(self):
        """Un gate cuyo 'bad' ahora ALLOW debe hacer fallar el arnés
        (el escenario opuesto: el lobo ya no ladra)."""
        fake = {
            "name": "fake-bad-broken",
            "fw": "test",
            "good": lambda: (True, "the good fixture still allows"),
            "bad": lambda: (False, "the bad fixture now allows"),
        }
        failures = gate_calibration.run_harness([fake])
        self.assertTrue(
            any("fake-bad-broken.bad" in f and "debe BLOCK" in f for f in failures),
            f"el arnés debe detectar un bad que ALLOW, got: {failures}",
        )

    def test_harness_catches_good_that_raises(self):
        """Un good que tira excepción (p. ej. signature rota) debe
        contarse como falla, no como éxito silencioso."""
        fake = {
            "name": "fake-raises",
            "fw": "test",
            "good": lambda: (_ for _ in ()).throw(RuntimeError("signature rota")),
            "bad": lambda: (True, "ok"),
        }
        failures = gate_calibration.run_harness([fake])
        self.assertTrue(
            any("fake-raises.good" in f and "raised" in f for f in failures),
            f"el arnés debe marcar un good que rompe como falla, got: {failures}",
        )

    def test_harness_catches_bad_that_raises(self):
        fake = {
            "name": "fake-bad-raises",
            "fw": "test",
            "good": lambda: (True, "ok"),
            "bad": lambda: (_ for _ in ()).throw(RuntimeError("signature rota")),
        }
        failures = gate_calibration.run_harness([fake])
        self.assertTrue(
            any("fake-bad-raises.bad" in f and "raised" in f for f in failures),
            f"el arnés debe marcar un bad que rompe como falla, got: {failures}",
        )

    def test_harness_passes_on_synthetic_passing_gates(self):
        """Sanity: un gate perfectamente calibrado (good=True, bad=True)
        no produce fallas. Cierra el meta-test — confirma que el arnés
        pasa cuando DEBE pasar, no solo falla cuando debe."""
        good = {
            "name": "fake-perfect",
            "fw": "test",
            "good": lambda: (True, "ok"),
            "bad": lambda: (True, "ok"),
        }
        failures = gate_calibration.run_harness([good])
        self.assertEqual(failures, [], f"gate perfecto no debe fallar: {failures}")

    def test_run_harness_defaults_to_canonical_GATES(self):
        """Sin argumentos, run_harness() corre sobre el registro canónico.
        Si los 5 están calibrados, devuelve []. Es el mismo test que
        test_all_gates_calibrated, pero ejercita la API por defecto
        (no paramétrica) — protege contra una refactorización que rompa
        el default."""
        # C4 (triaje 2026-07-09): correr el harness completo dos veces era el
        # mismo test (~0.5s duplicados). Verificar el forwarding del default
        # con un stub barato: run_harness() sin argumentos debe consumir
        # exactamente el registro canónico GATES.
        consumido = []

        def _good():
            consumido.append("good")
            return (True, "permite el caso bueno")

        def _bad():
            consumido.append("bad")
            return (True, "bloquea el caso malo")

        originales = gate_calibration.GATES
        gate_calibration.GATES = [{"name": "stub", "fw": "test", "good": _good, "bad": _bad}]
        try:
            failures = gate_calibration.run_harness()
        finally:
            gate_calibration.GATES = originales
        self.assertEqual(failures, [], failures)
        self.assertEqual(consumido, ["good", "bad"], "run_harness() sin args debe usar GATES canónico")


if __name__ == "__main__":
    unittest.main()
