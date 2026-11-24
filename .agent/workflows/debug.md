---
description: Protocolo de debugging sistemático — reproducir, minimizar, instrumentar, fix, regresión
---

Eres el agente de depuración sistemática.

Recibirás:
- @06_bug_fixes.md
- El código con el fallo o la salida del error

## Pasos

0. COMPROBAR EL REGISTRO DE BUGS
   Leer @06_bug_fixes.md. Si el bug ya tiene una entrada BUG-XXX, seguir con esa entrada.
   Si no, crear antes la entrada (equivale a /nuevo-bug).

1. REPRODUCIR
   Antes de proponer ningún arreglo, demostrar o documentar:
   - ¿Comportamiento observado exacto frente al esperado?
   - ¿El error es determinista o intermitente?
   - ¿En qué condiciones ocurre? ¿Cuándo NO ocurre?
   - ¿Qué cambió justo antes de que apareciera? (commit, dependencia, datos de entrada)
   - ¿El proceso o build que reproduce el bug corre con el código actual, o
     puede estar sirviendo una versión cacheada/no recargada? (ver regla de
     runtime obsoleto en `verificar-ejecutable.md` Paso 2) — descartar esto
     antes de dar el síntoma por confirmado.
   Marcar explícitamente las respuestas desconocidas: son lo primero que hay que verificar.

2. MINIMIZAR
   Aislar el bug en la superficie útil más pequeña:
   - Test focalizado, fixture mínima o reproducción manual mínima
   - Fichero, función, endpoint o componente sospechoso
   - Datos mínimos que disparan el bug
   No modificar todavía la lógica de negocio.

3. FORMULAR HIPÓTESIS
   Listar hasta 3 hipótesis ordenadas por probabilidad:
   ```
   Hipótesis 1 (más probable): [descripción — por qué es la más probable]
   Hipótesis 2: [descripción]
   Hipótesis 3: [descripción]
   ```

4. INSTRUMENTAR
   Para la hipótesis 1, definir la prueba más pequeña que la confirme o la descarte:
   ```
   Para verificar H1:
   - Acción: [qué hacer — añadir un log, cambiar un valor, aislar una función]
   - Si H1 es correcta: [resultado esperado]
   - Si H1 es incorrecta: [resultado esperado]
   ```
   Añadir logs, trazas, aserciones o sondas temporales si hace falta.
   NO implementar todavía el arreglo. Confirmar antes la hipótesis.

5. IMPLEMENTAR EL ARREGLO (solo tras confirmar la hipótesis)
   Producto en producción (FW-225): abrir el fix como cambio
   (`mantenimiento.md` pasos 2-7) — incluye su PRESERVAR acotado y la escalada
   si el fix toca superficies frágiles — en lugar del `cambio-seguro.md` completo.
   Antes de editar código existente, ejecutar `.agent/workflows/cambio-seguro.md` sobre el módulo o la función afectados.
   - Implementar el arreglo mínimo que resuelve la causa raíz
   - No añadir código defensivo para síntomas que no se entienden
   - No refactorizar alrededor del arreglo salvo que sea estrictamente necesario
   - No quitar comportamiento existente salvo que la entrada del bug o la aprobación del usuario lo exijan explícitamente

6. TEST DE REGRESIÓN
   Antes de cerrar:
   - Escribir un test de regresión si el proyecto tiene un runner aplicable
   - O ejecutar el test focalizado que cubre el bug
   - Verificar también el inventario `PRESERVAR` de `.agent/workflows/cambio-seguro.md`
   - O documentar por qué no es viable un test automático

7. REGISTRAR RESOLUCIÓN VÍA MCP
   Registrar la resolución vía `mutate_state` action `resolve_bug` con
   `{"id": "BUG-XXX", "fix": "[qué se cambió]", "affected_files": ["..."]}`.
   La proyección `06_bug_fixes.md` se regenera automáticamente — no editarla a mano.

   Si el bug no estaba registrado previamente, registrarlo primero con
   `mutate_state` action `register_bug` con
   `{"module_id": "[módulo]", "severity": "[critical|high|medium|low]", "description": "[síntoma]", "affects_ac": "[AC-XXX o n/a]"}`
   y usar el ID asignado en el paso `resolve_bug`.

## Regla de los 2 intentos
Si tras 2 ciclos completos (reproducir → minimizar → instrumentar → arreglar → regresión) el bug sigue sin resolver:
> Llevamos 2 intentos sin resolver. El contexto puede estar contaminado.
> Abre una ventana nueva con este contexto:
> "Bug en [descripción]. Síntoma: [X]. Intentos previos: [Y] y [Z]. Archivos sospechosos: [lista]. @06_bug_fixes.md"

## Salida

Cerrar con:
Siguiente: /ciclo Validar el fix y continuar el módulo hasta cierre.
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
Si el bug sigue abierto:
Siguiente: ninguno — abrir ventana nueva con contexto.

La interfaz, los logs y los textos para el usuario van en español.
