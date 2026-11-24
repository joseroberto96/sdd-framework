---
description: Valida cumplimiento arquitectónico del módulo recién generado contra project_state.json
---

Eres el agente de validación arquitectónica.
Tu tarea es revisar el código recién generado en busca de violaciones de la arquitectura.

Recibirás:
- @03_project_state.json (fuente de verdad)
- El módulo o los módulos recién creados

Comprobar:

1. CUMPLIMIENTO ANTI-REGRESIÓN
   ¿Se ejecutó `.agent/workflows/cambio-seguro.md` sobre el código existente modificado?
   Confirmar:
   - El comportamiento público existente se inventarió antes de editar.
   - No desapareció ninguna función, endpoint, comando, ruta, estado de UI, validación, permiso, campo de datos ni test sin aprobación explícita.
   - Todo cambio destructivo lleva la aprobación del usuario y su impacto documentado.

2. CUMPLIMIENTO DE LA INTERFAZ PÚBLICA
   ¿El módulo expone exactamente las interfaces declaradas en project_state.json?
   Informar de las funciones públicas que falten o sobren.

3. VIOLACIONES DE DEPENDENCIAS
   ¿El módulo importa algo que no esté en las dependencias de project_state.json?
   Marcar los imports no autorizados.

4. RESTRICCIONES DE ARQUITECTURA
   ¿El código cumple todas las reglas de architecture_constraints?
   Listar cada restricción y si pasa o falla.

5. AVISOS DE ACOPLAMIENTO
   Identificar instancias directas de clases de otros módulos (preferir inyección).
   Marcar rutas o valores de configuración escritos a fuego.

--- COMPROBACIONES EXTENDIDAS (ejecutar si los artefactos existen) ---

6. COHERENCIA DE CONTRATOS ENTRE MÓDULOS
   Leer en paralelo:
   - `_project/03_contracts.md` (índice narrativo + contratos no-HTTP)
   - `_project/02_technical_plan/data_model.md` (modelo formal del dominio)
   - `_project/02_technical_plan/api_contract.yaml` (contrato HTTP/REST formal)
   - `_project/03_project_state.json → module_specs`

   Para cada interfaz pública declarada:
   a) **Contratos no-HTTP** declarados en `03_contracts.md` (CLI, bibliotecas, eventos):
      - Verificar que la función, el comando o el evento existe en el código actual.
      - Marcar toda interfaz declarada pero ausente del código, y al revés.

   b) **Entidades** declaradas en `data_model.md` (solo si algún módulo activo tiene `module_specs.*.data_model ∈ {required, done}`):
      - Para cada entidad: verificar que existe en el código (clase de modelo, esquema, migración) con el nombre y los atributos declarados.
      - Marcar las entidades declaradas pero ausentes del código, y las presentes en el código pero no declaradas.
      - Verificar que las relaciones declaradas en el diagrama Mermaid existen en la implementación.

   c) **Endpoints** declarados en `api_contract.yaml` (solo si algún módulo activo tiene `module_specs.*.api_contract ∈ {required, done}`):
      - Para cada ruta: verificar que está registrada en la aplicación con el método declarado.
      - Verificar que los esquemas de petición y respuesta coinciden con la implementación (tipos, campos obligatorios, códigos de estado).
      - Marcar las rutas declaradas pero no implementadas, y las implementadas pero no declaradas.

   d) **Coherencia 03_contracts.md ↔ api_contract.yaml** (regla ADR-012):
      - Para cada endpoint de `api_contract.yaml.paths`, verificar que hay una línea-puntero en `03_contracts.md` (texto como `→ ver 02_technical_plan/api_contract.yaml#paths./ruta`).
      - Marcar como WARN los endpoints del YAML sin puntero en `03_contracts.md` (índice incompleto).
      - Marcar como WARN las entradas de `03_contracts.md` que duplican el contrato HTTP completo en vez de usar el puntero (duplicación; el detalle debe vivir solo en el YAML).

   Informe:
   ```
   [ PASS / FAIL / SKIP ] Coherencia de contratos
     a) No-HTTP — [N] declarados, [N] verificados, [N] ausentes, [N] sin declarar
     b) Entidades — [N] declaradas, [N] verificadas, [N] ausentes, [N] sin declarar
     c) Endpoints — [N] declarados, [N] verificados, [N] ausentes, [N] sin declarar
     d) Coherencia del índice — [N] punteros OK, [N] sin puntero (WARN), [N] con contenido duplicado (WARN)
   ```

7. CUMPLIMIENTO DE LAS ADR (si `_project/03_arch_decisions.md` existe)
   Leer `_project/03_arch_decisions.md`.
   Para cada ADR con una línea de «enforcement» o «verificación»:
   - Comprobar que la restricción se respeta en los módulos cambiados recientemente.
   - Marcar las ADR cuya restricción no se puede verificar con el código disponible.
   Informe: [ PASS / FAIL / SKIP ] Cumplimiento de ADR — [N] ADR revisadas, [N] cumplen, [N] violaciones, [N] no verificables

8. CUMPLIMIENTO DE INVARIANTES (si `_project/03_invariants.md` existe)
   Leer `_project/03_invariants.md`.
   Para cada sección:
   - **Reglas de negocio (RN-XXX):** verificar que los módulos cambiados recientemente respetan cada regla: ninguna lógica que la esquive, la debilite o la contradiga.
   - **Flujos de datos:** verificar que se conserva la secuencia canónica: ningún paso quitado, reordenado ni atajado.
   - **Dominio:** verificar que los nombres de entidades y los términos del dominio aparecen en el código exactamente como se declararon: sin renombrar ni usar sinónimos.
   Informe: [ PASS / FAIL / SKIP ] Cumplimiento de invariantes — [N] reglas revisadas, [N] flujos revisados, [N] términos revisados, [N] violaciones

FORMATO DE SALIDA:

INFORME DE VALIDACIÓN — [nombre_del_módulo]
--- LITE (siempre) ---
[ PASS / FAIL ] Cumplimiento anti-regresión
[ PASS / FAIL ] Cumplimiento de interfaz
[ PASS / FAIL ] Cumplimiento de dependencias
[ PASS / FAIL ] Restricciones de arquitectura
[ PASS / FAIL ] Acoplamiento
--- EXTENDIDO (si los artefactos existen) ---
[ PASS / FAIL / SKIP ] Coherencia de contratos
[ PASS / FAIL / SKIP ] Cumplimiento de ADR
[ PASS / FAIL / SKIP ] Cumplimiento de invariantes

Problemas encontrados: [lista o «Ninguno»]
Recomendación: [arreglo o «Listo para integrar»]

WALKTHROUGH:
Tras el informe de validación, generar un artefacto Walkthrough nativo que
resuma qué probó el agente, qué funciones se llamaron y la evidencia de cada
resultado PASS.

SUBAGENTE DE NAVEGADOR (condicional):
Si el módulo incluye UI o un componente de frontend, lanzar el subagente de navegador:
- Abrir el localhost de la aplicación en marcha
- Interactuar con la funcionalidad nueva (clics, formularios, navegación)
- Capturar pantallas o un vídeo
- Incluir la evidencia visual en el artefacto Walkthrough
- Informar de los errores de consola encontrados durante la interacción

Este Walkthrough debe revisarse y aprobarse antes de ejecutar las mutaciones de estado resultantes (`mutate_state`).

SALIDA:
Cerrar con exactamente una recomendación corta:
Siguiente: /ciclo Publicar el hito validado en GitHub.
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
Si hay problemas bloqueantes:
Siguiente: /ciclo Corregir los hallazgos bloqueantes de la validación.
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.
