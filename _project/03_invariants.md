# Invariantes del Sistema — [NOMBRE_PROYECTO]

> ⚠️ DOCUMENTO CANÓNICO E INMUTABLE.
> El AI **no puede modificar este archivo directamente**.
> Solo se actualiza mediante `/actualizar-invariante` con aprobación explícita del usuario.
> Última modificación aprobada: — / —

---

## 1. Dominio

### Entidades centrales
| Entidad | Descripción | Invariante |
|---------|-------------|------------|
| [Entidad] | [qué es y qué representa en el negocio] | [qué debe ser siempre verdad sobre ella] |

### Lenguaje ubicuo — términos protegidos
Los siguientes términos tienen significado exacto en este dominio.
**No renombrar, no reinterpretar, no reemplazar por sinónimos en código.**

- **[Término]**: [definición exacta tal como la usa el negocio]

---

## 2. Reglas de negocio

### RN-000: Fail-closed ante dependencias externas (invariante del framework, FW-146)
**Descripción:** cuando una dependencia externa (modelo, renderer, red, disco,
servicio de terceros) no responde o falla, el sistema lo dice.
**Invariante:** el fallo se propaga como error visible al llamante y, si procede,
al usuario final.
**Nunca:** devolver estructura vacía, valor por defecto o artefacto degradado
continuando el flujo como si hubiera funcionado; ni dejar datos de ejemplo,
identificadores de relleno o constantes cableadas en la ruta de producción.
**Excepción:** solo con ADR aprobado por el builder que nombre la dependencia, el
modo degradado y cómo se comunica.
**Afecta a:** todo módulo con una integración externa.


### RN-001: [nombre corto descriptivo]
**Descripción:** [qué regla es — en lenguaje de negocio, no de código]
**Invariante:** [condición que debe cumplirse SIEMPRE, sin excepción]
**Nunca:** [qué NO debe hacerse bajo ninguna circunstancia]
**Afecta a:** [módulos o componentes donde aplica]

---

## 3. Flujos de datos

### Flujo: [nombre del proceso]
**Descripción:** [qué proceso describe este flujo]
**Secuencia canónica:**
1. [Entrada: qué dato llega, de dónde]
2. [Transformación: cómo se procesa — sin detalles de implementación]
3. [Salida: qué dato sale, hacia dónde]

**Invariante del flujo:** [qué debe ser siempre verdad en este flujo]
**Nunca:** [qué NO debe cambiar en la secuencia ni en los datos que pasan por ella]

---

## Registro de cambios aprobados
| Fecha | Sección | Cambio | Razón | Aprobado por |
|-------|---------|--------|-------|--------------|
| — | — | Documento creado | Inicialización del proyecto | — |
