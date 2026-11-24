# Mapa de Dependencias por Módulo

**Propósito:** Registrar qué archivos lee y escribe cada módulo, y qué comportamiento existente debe preservar. El skill `prompt_lean` consume este mapa para producir el contexto mínimo necesario antes de implementar, sin leer el repositorio completo.

**Quién escribe este archivo:** El agente, al ejecutar `/enriquecer-ticket` (paso 5.2) o `/lean-context --register`.
**Cuándo actualizarlo:** Al declarar una interfaz nueva o al detectar que un módulo accede a archivos no declarados.

---

## Gate de implementación

- Si un módulo tiene interfaz pública declarada, debe tener entrada aquí antes de implementar.
- Si la entrada existe pero `lee`, `escribe`, `interfaces_requeridas`, `specs_formales_requeridas` o `preserva` contienen `# pendiente`, el agente debe detenerse y pedir confirmación antes de modificar código.
- Durante implementación, no leer directorios completos si este mapa provee archivos concretos.
- Si durante el trabajo aparece un archivo no declarado, pausar y actualizar esta entrada antes de continuar.
- Si durante el trabajo aparece una función, ruta, comando, flujo UI, validación o permiso existente que puede verse afectado, añadirlo a `preserva` antes de editar.

---

## Formato de entrada

```
### [id_modulo] — [Nombre del Módulo]
**Estado:** not_started | in_progress | completed
**Fase:** phase_XX

lee:
  - ruta/al/archivo.py          # propósito breve
  - _project/10_arch_constraints.md

escribe:
  - ruta/al/archivo_nuevo.py
  - _project/03_project_state.json  # solo si el módulo actualiza estado

interfaces_requeridas:
  - NombreInterfazNoHTTP         # CLI, biblioteca o evento; debe existir en _project/03_contracts.md

specs_formales_requeridas:
  - data_model: NombreEntidad    # si module_specs.data_model es required/done, debe existir en data_model.md
  - api_contract: METHOD /ruta   # si module_specs.api_contract es required/done, debe existir en api_contract.yaml

preserva:
  - ruta/al/archivo.py::funcion_existente  # comportamiento que no debe cambiar
  - GET /api/recurso                    # endpoint/ruta pública existente
  - Flujo UI: crear recurso             # estado o interacción visible existente
```

Cada entrada real debe incluir `preserva:` y al menos un comportamiento
concreto con `— evidencia:` seguido de una referencia verificable a un archivo,
test, AC o contrato. `preserva: n/a — esta incursión no modifica comportamiento
existente: justificación` solo es válido con esa declaración expresa; el
ejemplo de esta plantilla, comentarios HTML y `# pendiente` no son evidencia.

---

## Módulos

<!-- Las entradas se añaden aquí al ejecutar /enriquecer-ticket (paso 5.2) o /lean-context --register -->
<!-- Ejemplo:

### auth — Autenticación JWT
**Estado:** not_started
**Fase:** phase_01

lee:
  - src/models/user.py           # modelo User para validación
  - _project/10_arch_constraints.md

escribe:
  - src/routes/auth.py
  - src/middleware/jwt.py

interfaces_requeridas:
  - UserModel

specs_formales_requeridas:
  - data_model: User
  - api_contract: POST /auth/login

preserva:
  - POST /auth/login                    # contrato de login existente
  - src/models/user.py::User            # campos usados por autenticación

-->
