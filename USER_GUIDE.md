# How to translate and upload your work

[Español](USER_GUIDE.es.md)

## Start the server

Open a terminal in the project folder and run:

```bash
.venv/bin/python run.py
```

This setup supports Linux and Python 3.12 or later. Windows and macOS have not been validated. For the first run, set up the environment and start the server with:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/python run.py
```

Leave that terminal open and visit **http://127.0.0.1:8765** in your browser. To stop the server, press **Ctrl+C** in the terminal.

## Prepare your language glossary

The glossary records agreed terminology for your language: WCA roles, event names, formats, equipment and other recurring terms. The application uses it to guide generated proposals and flag terminology for review. Human review is still needed for meaning, grammar and context.

Once you have connected Weblate and selected your target language, review the glossary **before clicking Sync & Translate**:

1. Open **Configure glossary**. The window shows the default or personal catalog for the selected language. Reviewed defaults are currently included for **es-419**, **fr** and **fr-CA**. Other languages can start with a small catalog; a complete glossary is not required to begin using the tool. Any additional terminology downloaded from Weblate is saved in the session snapshot rather than listed in this window.
2. Search for important terms and check their reference translations against wording already used by your language team. Start with around 15–25 key terms covering WCA roles, event names, formats and equipment. Examples include Trainee Delegate, competition, event, attempt, solve, puzzle, scramble, time limit, cutoff, average, mean and scorecard. Each language needs its own reviewed translations.
3. Use **Add term** for missing entries. Enter the English term, the preferred translation and any useful usage note. Put variants on separate lines. Mark a translation as forbidden only when your team has explicitly agreed to avoid it; an alternative is not automatically wrong. Click **Save term** for each entry.
4. Save the glossary before creating a new session with **Sync & Translate**. If you edit it after a session has been created, synchronize again to use the updated glossary. Resuming an existing session keeps that session's original glossary.

If several people translate your language, agree on one shared reference with your language team. You can enter terms manually, or review a candidate prepared from your existing English/translated corpus using the [README's authoring prompt](README.md#language-glossaries). AI-generated candidates need a fluent speaker's review before adoption.

UI edits are personal to your local data folder. To contribute your team's reviewed glossary, click **Export glossary** and share the exported file with the tool maintainer for inclusion as a repository default. Another translator can load an agreed file using **Import glossary**, which replaces their complete configured catalog for that language; export a backup first. Changes to the local glossary are shared with Weblate through a separate manual process.

Personal catalogs take precedence over repository defaults, so review updates with your team when adopting a newer shared version. The [README](README.md#language-glossaries) documents the file format, validation and direct import.

## Translate from another language (for example fr → fr-CA)

If your language is adapted from another one rather than from English, choose it in **More options → Reference language** before syncing. The choice is remembered per target language, and the default **None** keeps the usual English workflow (fr-CA starts with French selected). Upload the reference language's translations to Weblate first: the sync downloads them read-only. Each entry then shows the reference text, with the words your glossary wants changed highlighted (for example *Petit-déjeuner → Déjeuner*). **Use … as proposals** copies the reference into the proposals so you only edit what differs; the diff is shown against the reference. AI and chat proposals also receive the reference; if one is not to your taste, **Use …** (shortcut U) puts the reference in the editor instead. Nothing is approved automatically.

## In the application

1. Choose **🇺🇸 EN** for English or **🇦🇷 ES** for Spanish. This changes the interface only.
2. Open **Connections**.
3. Sign in to [WCA Weblate](https://translate.worldcubeassociation.org/), click your user icon at the top right and open **Settings → API access** (**Developer settings** in newer versions). Copy your API token. Return to the application, paste it into **Weblate token** and click **Save**.
4. Click **Connect ChatGPT** and finish signing in. Return to the application and close **Connections** with **×**.
   Using Claude instead? Install [Claude Code](https://code.claude.com/docs), run `claude auth login` in a terminal with your Claude subscription, then choose **Claude Code · local** under **AI** in the application. Skip the ChatGPT button. If `ANTHROPIC_API_KEY` is set on your computer, usage is billed to that API key instead of your subscription.
   Want a free option? Run `pipx install libretranslate` once, then choose **Argos · LibreTranslate** under **AI**. The app starts LibreTranslate itself and stops it when you quit. The first use of each language downloads its model (150 to 400 MB); later uses work offline. Only if you run your own LibreTranslate server (for example with Docker), paste its address (such as `http://127.0.0.1:5000`) in **Connections** under **Argos · LibreTranslate** and click **Save**; a saved address is used instead of the local install until the app is restarted (set `LIBRETRANSLATE_URL` in `.env` to keep it). It has no glossary or context awareness and is weaker on short interface labels. There is no English model for Croatian, Kazakh, Mongolian, Welsh, Icelandic or Serbian. Regional variants such as `es_419` and `fr_CA` use the generic model, and each translated proposal says so. If the engine breaks an entry's placeholders, that entry keeps the English text with a note.
5. Choose the **Component** you want to work on: **Locales**, **Payload CMS** or **Regulations**.
6. Under **Target language**, choose the language you want to translate into. Wait for loading to finish. Before generating, [review your language glossary](#prepare-your-language-glossary).
7. Click **Sync & Translate**. If **Continue translation** appears, click it until the proposals are ready. If there is no pending work, you are done with this component.
8. Read the English source and each proposal. Click **Accept** if it is correct. To change it, click **Edit**, make your changes and click **Approve my edit**. Keep variables such as `%{name}` and `%s` intact. Review every plural form shown. **Reject** keeps the original translation; **Leave pending** skips the entry for now.
9. Click **Download translation**. If a check needs attention, click **Correct here**, fix the text and click **Save correction**. If a conflict appears in the review, choose which version to keep before trying again. Confirm any remaining warnings and that pending entries will be left out.
10. Click **Download file**. Remember where your browser saves it. Its name identifies the language and component: `<language>_<component>_reviewed.yaml`, `.json` or `.po`.

The completion reminder appears when you finish or explicitly reopen a review. It hides when you change component or language, start another sync, or dismiss it. After uploading in Weblate, click **I uploaded it** to close the reminder; this does not change your review decisions. Sync again to check the current remote state. A finished review restored when you open the application does not show the reminder automatically.

## In Weblate

11. Sign in to [WCA Weblate](https://translate.worldcubeassociation.org/). Open the **WCA project**, then the **same component and language** you chose in the application.
12. Click **Files → Upload translation** (sometimes **Upload**) and select the file you just downloaded.
13. Choose **Add as translation**. For Locales, you may choose **Add as suggestion** if you want someone to review it there and that option is available. **Do not choose Replace existing translation file.** If Weblate asks about overwriting existing translations, check the remote changes before overwriting them.
14. Confirm the upload. Read the result and check that your changed strings appear correctly. If the upload option is missing, ask your project administrator for permission.

**Never upload a file whose name starts with `DEMO_` or `SIMULATION_`: those are test files.**
