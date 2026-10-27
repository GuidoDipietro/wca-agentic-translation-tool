# How to translate and upload your work

[Español](USER_GUIDE.es.md)

## Start the server

Open a terminal in the project folder and run:

```bash
.venv/bin/python run.py
```

This setup supports Linux and Python 3.10 or later. Windows and macOS have not been validated. For the first run, set up the environment and start the server with:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/python run.py
```

Leave that terminal open and visit **http://127.0.0.1:8765** in your browser. To stop the server, press **Ctrl+C** in the terminal.

## In the application

1. Choose **🇺🇸 EN** for English or **🇦🇷 ES** for Spanish. This changes the interface only.
2. Open **Connections**.
3. Sign in to [WCA Weblate](https://translate.worldcubeassociation.org/), click your user icon at the top right and open **Settings → API access** (**Developer settings** in newer versions). Copy your API token. Return to the application, paste it into **Weblate token** and click **Save**.
4. Click **Connect ChatGPT** and finish signing in. Return to the application and close **Connections** with **×**.
5. Choose the **Component** you want to work on: **Locales**, **Payload CMS** or **Regulations**.
6. Under **Target language**, choose the language you want to translate into. Wait for loading to finish.
7. Click **Sync & Translate**. If **Continue translation** appears, click it until the proposals are ready. If there is no pending work, you are done with this component.
8. Read the English source and each proposal. Click **Accept** if it is correct. To change it, click **Edit**, make your changes and click **Approve my edit**. Keep variables such as `%{name}` and `%s` intact. Review every plural form shown. **Reject** keeps the original translation; **Leave pending** skips the entry for now.
9. Click **Download translation**. If a check needs attention, click **Correct here**, fix the text and click **Save correction**. If a conflict appears in the review, choose which version to keep before trying again. Confirm any remaining warnings and that pending entries will be left out.
10. Click **Download file**. Remember where your browser saves it. Its name identifies the language and component: `<language>_<component>_reviewed.yaml`, `.json` or `.po`.

## In Weblate

11. Sign in to [WCA Weblate](https://translate.worldcubeassociation.org/). Open the **WCA project**, then the **same component and language** you chose in the application.
12. Click **Files → Upload translation** (sometimes **Upload**) and select the file you just downloaded.
13. Choose **Add as translation**. For Locales, you may choose **Add as suggestion** if you want someone to review it there and that option is available. **Do not choose Replace existing translation file.** If Weblate asks about overwriting existing translations, check the remote changes before overwriting them.
14. Confirm the upload. Read the result and check that your changed strings appear correctly. If the upload option is missing, ask your project administrator for permission.

**Never upload a file whose name starts with `DEMO_` or `SIMULATION_`: those are test files.**
