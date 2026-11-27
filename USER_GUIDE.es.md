# Cómo traducir y subir tu trabajo

[English](USER_GUIDE.md)

## Iniciar el servidor

Abrí una terminal en la carpeta del proyecto y ejecutá:

```bash
.venv/bin/python run.py
```

Este setup admite Linux y Python 3.12 o posterior. Windows y macOS no están validados. Si es la primera vez, prepará el entorno e iniciá el servidor con:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/python run.py
```

Dejá esa terminal abierta y abrí **http://127.0.0.1:8765** en el navegador. Para detener el servidor, pulsá **Ctrl+C** en la terminal.

## Preparar el glosario de tu idioma

El glosario registra la terminología acordada para tu idioma: cargos WCA, nombres de eventos, formatos, equipamiento y otros términos recurrentes. La aplicación lo usa para orientar las propuestas y señalar terminología para revisar. El significado, la gramática y el contexto siguen requiriendo revisión humana.

Después de conectar Weblate y seleccionar el idioma de destino, revisá el glosario **antes de pulsar Sincronizar y traducir**:

1. Abrí **Configurar glosario**. La ventana muestra el catálogo predeterminado o personal del idioma seleccionado. Actualmente se incluyen predeterminados revisados para **es-419**, **fr** y **fr-CA**. Los demás idiomas pueden empezar con un catálogo pequeño; no es necesario tener un glosario completo para usar la herramienta. Los términos adicionales descargados de Weblate se guardan en la copia de la sesión, en vez de mostrarse en esta ventana.
2. Buscá los términos importantes y compará sus traducciones de referencia con las que ya utiliza tu equipo. Empezá con unos 15–25 términos sobre cargos WCA, nombres de eventos, formatos y equipamiento. Algunos ejemplos son Trainee Delegate, competition, event, attempt, solve, puzzle, scramble, time limit, cutoff, average, mean y scorecard. Cada idioma necesita sus propias traducciones revisadas.
3. Usá **Agregar término** para las entradas que falten. Escribí el término en inglés, su traducción preferida y una nota de uso si ayuda a aclarar el contexto. Poné una variante por línea. Marcá una traducción como prohibida solo cuando el equipo haya acordado evitarla expresamente; una alternativa no es incorrecta automáticamente. Pulsá **Guardar término** en cada entrada.
4. Guardá el glosario antes de crear una sesión con **Sincronizar y traducir**. Si lo modificás después de crearla, volvé a sincronizar para usar la versión actualizada. Retomar una sesión existente conserva el glosario original de esa sesión.

Si varias personas traducen tu idioma, acuerden una referencia compartida con el equipo. Podés cargar términos a mano o revisar un candidato preparado desde el corpus inglés/traducido existente usando el [prompt del README](README.es.md#glosarios-por-idioma). Un candidato generado con IA necesita la revisión de alguien que domine el idioma antes de adoptarlo.

Los cambios de la UI son personales y se guardan en tu carpeta local de datos. Para aportar el glosario revisado del equipo, pulsá **Exportar glosario** y compartí el archivo con quien mantiene la herramienta para incluirlo como predeterminado del repositorio. Otro traductor puede cargar un archivo acordado con **Importar glosario**, que reemplaza todo su catálogo configurado de ese idioma; exportá una copia de respaldo primero. Compartir los cambios del glosario local con Weblate es un proceso manual separado.

Los catálogos personales tienen prioridad sobre los predeterminados del repositorio. Revisá las actualizaciones con el equipo al adoptar una versión compartida más reciente. El [README](README.es.md#glosarios-por-idioma) documenta el formato, la validación y la importación directa.

## Traducir desde otro idioma (por ejemplo fr → fr-CA)

Si tu idioma se adapta de otro en lugar del inglés, elegilo en **Más opciones → Idioma de referencia** antes de sincronizar. La elección se recuerda por idioma de destino y la opción predeterminada **Ninguno** mantiene el flujo habitual desde el inglés (fr-CA empieza con el francés seleccionado). Primero subí a Weblate las traducciones del idioma de referencia: la sincronización las descarga en modo de solo lectura. Cada entrada muestra el texto de referencia con resaltadas las palabras que tu glosario pide cambiar (por ejemplo *Petit-déjeuner → Déjeuner*). **Usar … como propuestas** copia la referencia en las propuestas para que solo edites lo que cambia; la diferencia se muestra contra la referencia. Las propuestas de IA y del chat también reciben la referencia; si una no te convence, **Usar …** (atajo U) pone la referencia en el editor. Nada se aprueba automáticamente.

## En la aplicación

1. Elegí **🇺🇸 EN** para inglés o **🇦🇷 ES** para español. Esto cambia solo la interfaz.
2. Abrí **Conexiones**.
3. Iniciá sesión en [Weblate de WCA](https://translate.worldcubeassociation.org/), pulsá tu icono de usuario arriba a la derecha y entrá en **Settings → API access** (en versiones nuevas, **Developer settings**). Copiá tu token de API. Volvé a la aplicación, pegalo en **Token de Weblate** y pulsá **Guardar**.
4. Pulsá **Conectar ChatGPT** y completá el inicio de sesión. Volvé a la aplicación y cerrá **Conexiones** con **×**.
   ¿Usás Claude? Instalá [Claude Code](https://code.claude.com/docs), ejecutá `claude auth login` en una terminal con tu suscripción de Claude y elegí **Claude Code · local** en **IA** dentro de la aplicación. No hace falta el botón de ChatGPT. Si tenés `ANTHROPIC_API_KEY` definida en tu computadora, el uso se factura a esa API key en lugar de tu suscripción.
   ¿Querés una opción gratuita? Ejecutá `pipx install libretranslate` una vez y después elegí **Argos · LibreTranslate** en **IA**. La aplicación inicia LibreTranslate sola y lo detiene cuando la cerrás. El primer uso de cada idioma descarga su modelo (de 150 a 400 MB); los usos siguientes funcionan sin conexión. Solo si administrás tu propio servidor LibreTranslate (por ejemplo con Docker), pegá su dirección (como `http://127.0.0.1:5000`) en **Conexiones**, en **Argos · LibreTranslate**, y pulsá **Guardar**; una dirección guardada se usa en lugar de la instalación local hasta que reiniciés la aplicación (definí `LIBRETRANSLATE_URL` en `.env` para conservarla). No tiene glosario ni conocimiento del contexto y rinde peor en etiquetas cortas de la interfaz. No hay modelo de inglés para croata, kazajo, mongol, galés, islandés ni serbio. Las variantes regionales como `es_419` y `fr_CA` usan el modelo genérico, y cada propuesta traducida lo indica. Si el motor rompe los marcadores de una entrada, esa entrada conserva el texto en inglés con una nota.
5. Elegí el **Componente** en el que querés trabajar: **Locales**, **Payload CMS** o **Regulations**.
6. En **Idioma de destino**, elegí el idioma al que querés traducir. Esperá que termine de cargar. Antes de generar, [revisá el glosario de tu idioma](#preparar-el-glosario-de-tu-idioma).
7. Pulsá **Sincronizar y traducir**. Si aparece **Continuar traducción**, pulsalo hasta que las propuestas estén listas. Si no hay pendientes, terminaste con ese componente.
8. Leé el original en inglés y cada propuesta. Pulsá **Aceptar** si está bien. Para cambiarla, pulsá **Editar**, corregí el texto y pulsá **Aprobar mi edición**. Conservá las variables como `%{name}` y `%s`. Revisá cada forma plural que aparezca. **Rechazar** conserva la traducción original; **Dejar pendiente** saltea esa entrada por ahora.
9. Pulsá **Descargar traducción**. Si aparece un control que necesita atención, pulsá **Corregir aquí**, corregí el texto y pulsá **Guardar corrección**. Si aparece un conflicto en la revisión, elegí qué versión conservar antes de volver a intentar. Confirmá las advertencias restantes y que se omitirán las entradas pendientes.
10. Pulsá **Descargar archivo**. Fijate dónde lo guarda el navegador. El nombre identifica idioma y componente: `<idioma>_<componente>_reviewed.yaml`, `.json` o `.po`.

El recordatorio de finalización aparece al terminar o reabrir explícitamente una revisión. Se oculta al cambiar de componente o idioma, iniciar otra sincronización o cerrarlo. Después de subir el archivo en Weblate, pulsá **Ya lo subí** para cerrar el recordatorio; esto no cambia tus decisiones de revisión. Volvé a sincronizar para consultar el estado remoto actual. Una revisión terminada que se restaura al abrir la aplicación no muestra el recordatorio automáticamente.

## En Weblate

11. Iniciá sesión en [Weblate de WCA](https://translate.worldcubeassociation.org/). Abrí el **proyecto WCA** y después el **mismo componente e idioma** que elegiste en la aplicación.
12. Pulsá **Files → Upload translation** (a veces **Upload**) y elegí el archivo que acabás de descargar.
13. Elegí **Add as translation**. Para Locales, podés elegir **Add as suggestion** si querés que alguien lo revise allí y la opción está disponible. **No elijas Replace existing translation file.** Si Weblate pregunta si querés sobrescribir traducciones existentes, revisá los cambios remotos antes de hacerlo.
14. Confirmá la carga. Leé el resultado y comprobá que tus textos aparezcan correctamente. Si no aparece la opción para subir archivos, pedile permisos a quien administra el proyecto.

**Nunca subas archivos cuyo nombre empiece con `DEMO_` o `SIMULATION_`: son archivos de prueba.**
