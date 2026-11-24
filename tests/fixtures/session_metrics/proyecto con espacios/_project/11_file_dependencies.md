# Mapa de Dependencias por Módulo

## Gate de implementación

### [id_modulo] — [Nombre del Módulo]
**Fase:** phase_XX

lee:
  - ejemplo.py

### m1 — Escribe app/a.py
**Fase:** phase_01

lee:
  - `docs/guia.md`

escribe:
  - `app/a.py`
  - `_project/04_active_scope.md`

### m2 — Lee app/a.py
**Fase:** phase_01

lee:
  - app/a.py                      # comentario
  - app/otro.py

escribe:
  - app/b.py

### m3 — Escribe un glob
**Fase:** phase_01

escribe_previsto:
  - `app/c/*.py`

### m4 — Escribe dentro del glob
**Fase:** phase_01

escribe:
  - app/c/x.py

### m5 — Ruta ancha ignorada
**Fase:** phase_01

escribe:
  - `*.md`
  - `_project/05_test_history.md`

### m6 — Otra fase, misma ruta
**Fase:** phase_02

escribe:
  - app/a.py

### m8_sin_rutas — Entrada vacía
**Fase:** phase_02

lee:
escribe:

## Módulo: m9_formato_libre

**Lee:** `app/libre.py`, texto en prosa que no se interpreta.
