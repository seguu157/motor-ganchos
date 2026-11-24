# Plan de mejoras

Referencia funcional: la auditoría de Espace (biblioteca, ficha, adaptación y flujo de producción). No se copian sus textos, sus categorías ni su catálogo. Lo que sigue es el plan de esta app, para Antonio, en local.

## P1 — Esta entrega

1. **Biblioteca.** El banco sigue siendo la biblioteca. Se añaden categoría (derivada del tipo de gancho, con nombres propios), formato (reel o carrusel), búsqueda con acentos normalizados y los filtros que ya había. Los filtros van en la URL.
2. **Ficha de siete bloques.** Gancho, receta, prompts, ajustes de edición, ideas derivadas, monetización/oferta y adaptación al nicho. El desglose con marcas de tiempo sale de los segmentos de la transcripción. Si no hay tiempos, se dice y no se inventan. En un carrusel el desglose es diapositiva a diapositiva.
3. **Perfil.** Marca, oferta, audiencia, problema, tono, idioma, pruebas, restricciones, CTA y nicho. Editable en `radar perfil` y en la web. Lo que no esté rellenado se muestra como `[X]`.
4. **Flujo guiado.** Oferta → Referencias → Hooks → Guion → Variantes → Revisión → Exportación, en `radar flujo` y en `/flujo`. Solo se puede elegir como gancho de trabajo un outlier con métricas guardadas. Los ejemplos se rechazan: no son métricas reales. Las variantes rellenan la plantilla del formato ya clasificado; no inventan otro.
5. **Navegador como fuente por defecto.** Playwright, sesión persistente, `radar login` una sola vez y a mano. Sin contraseñas en código, base ni logs. Pausas aleatorias y tope de perfiles y de posts. Parada en 401, 429, checkpoint o challenge, sin reintentos. Apify e Instaloader siguen disponibles con `--source`. `auto` conserva el comportamiento anterior cuando no hay sesión de navegador.
6. **Carruseles.** Texto de cada diapositiva y OCR local solo si falta el texto (`rapidocr-onnxruntime`). Las imágenes se borran al terminar, salvo `--conservar-imagenes`. Outlier propio: `me gusta + 4 × comentarios` contra la mediana de los últimos carruseles de la cuenta, umbral 2×. El 4 es una regla de esta app para que un comentario pese más que un me gusta; no es una métrica de Instagram. Sin me gusta o sin comentarios, esa pieza no entra en la mediana.

## P2 — Después

- Tablero por estados y teleprompter (ya quedaron fuera de la fase 1).
- Marcar fichas como leídas o favoritas.
- Ajustes de edición más finos cuando la fuente de verdad traiga el plano, no antes.
- Edición semanal de lo que ha entrado en el banco.

## P3 — Más adelante

- YouTube con TubeAlfred, mediana aparte y flag explícito. Sigue apagado.
- Herramientas de solo lectura para un asistente.
- Publicar el reel en otra herramienta. Esta app no publica.
- Sustituir el SQLite por un almacén remoto. El JSON de exportación sigue siendo el puente.

## Fuera de alcance

Pagos, cuentas de varios usuarios, comunidad y producción de vídeo. La interfaz no tiene login: no se publica a internet sin un proxy con contraseña.
