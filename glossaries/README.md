# Bundled glossaries

The reviewed [es-419 glossary](es_419.json) is the shared project default for Locales, Regulations and Payload, with 162 entries. New sessions load it automatically for es-419 or es_419 when no personal replacement exists.

It preserves the confirmed terminology, including Trainee Delegate → Delegado en prácticas, competition → competición and puzzle cover → tapacubos. Completed Solves keeps Soluciones completadas in the profile; other nominal solve/solves labels use resolución/resoluciones. Stackmat is forbidden when translating timer references. Notes document the contexts and review decisions.

Defaults are UTF-8 JSON files named with lowercase language codes and underscores, for example es_419.json or pt_br.json. Personal replacements live in <data-dir>/glossaries/<language>.json, outside Git, and take precedence over the complete default for that language. Existing sessions keep their original snapshots. Other languages without a bundled default start with an empty configured catalog and can use terminology downloaded from Weblate.

To update the shared reference, edit the JSON or review a file exported from Configure glossary → Export glossary. Validate it before committing:

```bash
.venv/bin/python app/glossary_store.py glossaries/es_419.json --language es-419
.venv/bin/python -m unittest discover -s tests -v
```

Validation does not install a personal replacement. Keep usage notes and contextual exceptions and review the diff before distributing a personal preference to everyone. UI edits remain local until a reviewed export is contributed to the repository. Do not commit local sessions or review packages.

See the [English README](../README.md#language-glossaries) or [Spanish README](../README.es.md#glosarios-por-idioma) for the schema, personal import commands and suggested corpus-based authoring prompt.
