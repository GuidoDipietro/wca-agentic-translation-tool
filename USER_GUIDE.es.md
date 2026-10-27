# Cómo traducir y subir tu trabajo

[English](USER_GUIDE.md)

## Iniciar el servidor

Abrí una terminal en la carpeta del proyecto y ejecutá:

```bash
.venv/bin/python run.py
```

Este setup admite Linux y Python 3.10 o posterior. Windows y macOS no están validados. Si es la primera vez, prepará el entorno e iniciá el servidor con:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/python run.py
```

Dejá esa terminal abierta y abrí **http://127.0.0.1:8765** en el navegador. Para detener el servidor, pulsá **Ctrl+C** en la terminal.

## En la aplicación

1. Elegí **🇺🇸 EN** para inglés o **🇦🇷 ES** para español. Esto cambia solo la interfaz.
2. Abrí **Conexiones**.
3. Iniciá sesión en [Weblate de WCA](https://translate.worldcubeassociation.org/), pulsá tu icono de usuario arriba a la derecha y entrá en **Settings → API access** (en versiones nuevas, **Developer settings**). Copiá tu token de API. Volvé a la aplicación, pegalo en **Token de Weblate** y pulsá **Guardar**.
4. Pulsá **Conectar ChatGPT** y completá el inicio de sesión. Volvé a la aplicación y cerrá **Conexiones** con **×**.
5. Elegí el **Componente** en el que querés trabajar: **Locales**, **Payload CMS** o **Regulations**.
6. En **Idioma de destino**, elegí el idioma al que querés traducir. Esperá que termine de cargar.
7. Pulsá **Sincronizar y traducir**. Si aparece **Continuar traducción**, pulsalo hasta que las propuestas estén listas. Si no hay pendientes, terminaste con ese componente.
8. Leé el original en inglés y cada propuesta. Pulsá **Aceptar** si está bien. Para cambiarla, pulsá **Editar**, corregí el texto y pulsá **Aprobar mi edición**. Conservá las variables como `%{name}` y `%s`. Revisá cada forma plural que aparezca. **Rechazar** conserva la traducción original; **Dejar pendiente** saltea esa entrada por ahora.
9. Pulsá **Descargar traducción**. Si aparece un control que necesita atención, pulsá **Corregir aquí**, corregí el texto y pulsá **Guardar corrección**. Si aparece un conflicto en la revisión, elegí qué versión conservar antes de volver a intentar. Confirmá las advertencias restantes y que se omitirán las entradas pendientes.
10. Pulsá **Descargar archivo**. Fijate dónde lo guarda el navegador. El nombre identifica idioma y componente: `<idioma>_<componente>_reviewed.yaml`, `.json` o `.po`.

## En Weblate

11. Iniciá sesión en [Weblate de WCA](https://translate.worldcubeassociation.org/). Abrí el **proyecto WCA** y después el **mismo componente e idioma** que elegiste en la aplicación.
12. Pulsá **Files → Upload translation** (a veces **Upload**) y elegí el archivo que acabás de descargar.
13. Elegí **Add as translation**. Para Locales, podés elegir **Add as suggestion** si querés que alguien lo revise allí y la opción está disponible. **No elijas Replace existing translation file.** Si Weblate pregunta si querés sobrescribir traducciones existentes, revisá los cambios remotos antes de hacerlo.
14. Confirmá la carga. Leé el resultado y comprobá que tus textos aparezcan correctamente. Si no aparece la opción para subir archivos, pedile permisos a quien administra el proyecto.

**Nunca subas archivos cuyo nombre empiece con `DEMO_` o `SIMULATION_`: son archivos de prueba.**
