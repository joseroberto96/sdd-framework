# Skills de Dominio — Expertos Especializados

> Esta carpeta contiene los "expertos" del proyecto: archivos de conocimiento especializado
> que el agente activa cuando trabaja en módulos relevantes para ese dominio.

## Cómo funciona

1. **Claude genera la base inicial** al ejecutar `/nuevo-proyecto` con su conocimiento del dominio
2. **El desarrollador la enriquece** añadiendo fuentes reales: docs, APIs del sector, papers, manuales
3. **El agente la usa** al declarar interfaces y cerrar módulos que tocan ese dominio

## Cuándo se activa cada experto

- Los expertos nacen **proactivamente** (4.1.0): `/diseñar` (paso 3.5) y `/enriquecer-ticket` (paso 0.7) detectan sub-dominios sin cubrir y proponen el experto con borrador listo sobre `_TEMPLATE.md`; el builder confirma/corrige.
- `/enriquecer-ticket` (paso 0.7) lee `09_domain_profile.md` y carga el experto relevante según el módulo; sus invariantes entran a la Definition of Done.
- Las decisiones citan a su experto: `register_decision` con `"expertise": "domain/[archivo]"` (trazabilidad en `03_arch_decisions.md`).
- El gate suave 3.9 de `/cierre-modulo` avisa (sin bloquear) si un módulo tocó un sub-dominio sin experto ni descarte justificado.
- `/cierre-modulo` verifica los riesgos críticos de cada experto activo antes del gate de seguridad
- El desarrollador puede invocar un experto manualmente mencionando `@.agent/skills/domain/nombre.md`

## Estructura de cada archivo de experto

```markdown
# Experto en [Área] — [NOMBRE_PROYECTO]

## Conocimiento base (generado por IA)
[reglas, patrones, anti-patrones conocidos del área]

## Fuentes del desarrollador
[links, docs, fragmentos de APIs reales — añadir aquí]

## Invariantes críticos
[lo que NUNCA debe ocurrir en este área del sistema]

## Preguntas a hacer antes de implementar
[checklist de dudas habituales en este dominio]
```

## Estado de madurez

| Ícono | Significado |
|-------|-------------|
| 🌱 | Base generada por IA — sin fuentes externas |
| 📚 | Enriquecido con fuentes del desarrollador |
| ✅ | Validado por experto humano del dominio |
