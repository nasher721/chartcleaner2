# Highlighting, abbreviations, and sharing

On **Clean**, check **Remove mode** or **Replace mode**, then highlight text in the chart editor.

- Remove deletes only the selected occurrence now and remembers the text for future full cleans.
- Replace opens a dialog; **Replace & remember** edits the selection and saves the replacement.
- **Whole words only** prevents partial-word matches. **Match case** controls future matching.
- **Undo last highlight** restores the last edit and removes the newly saved rule. Undo refuses to overwrite subsequent text or rule changes.
- Auto-clean pauses while a highlight mode is checked. Modes start unchecked on each page visit.
- Saved removal/replacement rules run in **Full clean**. **Abbreviations only** intentionally applies only the abbreviation dictionary.

Open **My text rules** to search, add, edit, or delete saved text rules. Existing regex rules remain editable as regex; newly highlighted rules use literal text.

The **Abbreviations** tab lists the bundled dictionary and custom entries. Search by full term or abbreviation; edit a bundled entry to create an override, disable a term, or add a custom abbreviation. Removing an override restores its bundled entry. Disabled phrases are preserved as a unit, including words inside them. Custom changes apply in both cleaning modes and batch processing.

Under **Share & import**:

- **Export all rules JSON** includes saved text rules and abbreviation settings.
- **Export text rules CSV** includes only text rules, in their execution order.
- Exports are saved locally in the exports folder and also offered as downloads. **Copy JSON** supports clipboard sharing.
- Import previews the rule content and counts. Merge keeps existing conflicting rules; Replace replaces the saved text rules and replaces abbreviation settings only when the file contains them. Both modes skip and report later conflicting entries within the same file.
- CSV imports preserve abbreviation settings. Invalid files are rejected before saving. Settings saves retain a backup of the prior config.
- Use **Settings → Export all settings (.zip)** for a complete configuration backup, including pipeline settings, presets, and scripts. Portable rule files exclude charts and history, but saved rule text should still be reviewed before sharing.

Implementation: `app.py` contains the checked modes and page integration; `highlight_rules.py`, `learned_editor.py`, `abbreviation_editor.py`, and `rule_sharing.py` contain the reusable rule/editor logic. Matching and validation changes live in `abbreviations.py`, `stages.py`, and `config_validator.py`; `store.py` exposes portable import/export operations. No dependencies were added.

Validation: run `.venv/bin/python -m pytest -q` from the repository. Browser verification uses an isolated configuration with sample text. Source changes do not rebuild the packaged macOS or Windows installers.
