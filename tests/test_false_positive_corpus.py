#!/usr/bin/env python3
"""Integridad del corpus de falsos positivos (5.3.0 L1).

Solo comprueba que el índice y los ficheros están completos. NO ejecuta el guard
destructivo ni el escáner de secretos sobre los casos: eso lo hará un lote posterior.
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / "fixtures" / "false_positives"
FAMILIES = {"guard": 6, "scanner": 4}
FIELDS = {"id", "familia", "origen", "descripcion", "fichero", "veredicto_esperado"}
_MARKER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def materializar(case: dict) -> str:
    """Texto del caso con los marcadores `{{NOMBRE}}` sustituidos por hex = sha256(semilla)[:longitud].

    Los casos del escáner no llevan el hex en el fichero porque un hex real versionado
    dispara el escáner del propio repo; el lote que los consuma llama a esta función."""
    text = (CORPUS / case["fichero"]).read_text(encoding="utf-8")
    for name, spec in (case.get("materializar") or {}).items():
        digest = hashlib.sha256(spec["semilla"].encode()).hexdigest()[: spec["longitud"]]
        text = text.replace("{{%s}}" % name, digest)
    return text


class FalsePositiveCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = json.loads((CORPUS / "index.json").read_text(encoding="utf-8"))
        cls.cases = cls.index["casos"]

    def test_index_has_ten_cases_with_all_fields(self) -> None:
        self.assertEqual(len(self.cases), 10)
        for case in self.cases:
            self.assertEqual(set(case) - {"materializar"}, FIELDS, case.get("id"))
            for key in FIELDS:
                self.assertTrue(str(case[key]).strip(), f"{case['id']}: {key} vacío")

    def test_ids_are_unique_and_verdict_is_permitir(self) -> None:
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({c["veredicto_esperado"] for c in self.cases}, {"permitir"})

    def test_families_add_up_to_six_and_four(self) -> None:
        counts = {}
        for case in self.cases:
            counts[case["familia"]] = counts.get(case["familia"], 0) + 1
        self.assertEqual(counts, FAMILIES)

    def test_every_case_file_exists_and_is_not_empty(self) -> None:
        for case in self.cases:
            path = CORPUS / case["fichero"]
            self.assertTrue(path.is_file(), case["fichero"])
            self.assertGreater(len(path.read_text(encoding="utf-8").strip()), 0, case["fichero"])

    def test_no_orphan_case_files(self) -> None:
        listed = {c["fichero"] for c in self.cases} | {"index.json"}
        present = {p.name for p in CORPUS.iterdir() if p.is_file()}
        self.assertEqual(present, listed)

    def test_scanner_cases_use_markers_and_materialize_to_hex(self) -> None:
        for case in self.cases:
            raw = (CORPUS / case["fichero"]).read_text(encoding="utf-8")
            if case["familia"] != "scanner":
                self.assertNotIn("materializar", case)
                self.assertIsNone(_MARKER.search(raw), case["id"])
                continue
            # El fichero versionado no contiene hex de 32/40/64: solo marcadores.
            self.assertIsNone(re.search(r"\b(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})\b", raw), case["id"])
            used = set(_MARKER.findall(raw))
            self.assertEqual(used, set(case["materializar"]), case["id"])
            text = materializar(case)
            self.assertIsNone(_MARKER.search(text), case["id"])
            self.assertTrue(re.search(r"\b[0-9a-f]{32,64}\b", text), case["id"])

    def test_origins_cover_the_three_adopters(self) -> None:
        origins = " ".join(c["origen"] for c in self.cases)
        for adopter in ("Example Lab", "Example Tool", "Research App"):
            self.assertIn(adopter, origins)


if __name__ == "__main__":
    unittest.main()
