# Medical chart cleaner

Clean **Epic-style EMR exports** for safer sharing with LLMs or documentation: strip line-level metadata and boilerplate, redact common PHI patterns, run **Microsoft Presidio** NLP redaction (names, phones, emails), normalize literals and headings, and optionally remove near-duplicate note blocks.

**Default output** wraps the chart in `<patient_chart>…</patient_chart>` unless you pass `--no-wrap`.

---

## What you need

| Requirement | Notes |
|-------------|--------|
| **Python 3.10+** | **3.11–3.12** is the most reliable on Windows for Presidio/spaCy wheels. |
| **Internet (first install only)** | `pip` downloads packages and the spaCy model (`en-core-web-sm`). |
| **Write access** | To the project folder (virtual environment lives in `.venv/`). |

**Clipboard mode** uses [`pyperclip`](https://pypi.org/project/pyperclip/) (macOS, Windows, Linux with a clipboard backend).

---

## Quick start by platform

### macOS

1. Open **Terminal** and go to this folder:

   ```bash
   cd /path/to/scripts
   ```

2. Run setup once:

   ```bash
   chmod +x install.sh clean-chart
   ./install.sh
   ```

3. **Clipboard:** copy chart text, then:

   ```bash
   ./clean-chart
   ```

   Cleaned text replaces the clipboard. Use `./clean-chart -h` for files and folders.

### Windows

1. Install Python from [python.org](https://www.python.org/downloads/) if needed:
   - Enable **“Add python.exe to PATH”**.
   - For PCs **without admin**: leave **“Install for all users”** unchecked (per-user install).

2. Open **Command Prompt** or **PowerShell**, `cd` to this folder.

3. Run setup **once** (pick one):

   | Method | Command |
   |--------|---------|
   | **CMD only** (good for locked-down PCs, no PowerShell policy issues) | `install.bat` |
   | **PowerShell** | `.\install.ps1` or `powershell -ExecutionPolicy Bypass -File .\install.ps1` |

4. **Clipboard:** copy chart text, then run `clean-chart.cmd` from that folder, or double-click **`Clean_Medical_Chart.cmd`**.

---

## How to run it

### Clipboard (no arguments)

1. Copy the raw chart from Epic (or any source) to the clipboard.
2. Run the launcher with **no** `-f` / `-d`:

   | Platform | Command |
   |----------|---------|
   | macOS | `./clean-chart` |
   | Windows | `clean-chart.cmd` or double-click `Clean_Medical_Chart.cmd` |

3. Paste wherever you need; the clipboard now holds the cleaned text.

If the clipboard is empty, the tool exits with an error message.

### Single file

```bash
# macOS
./clean-chart -f /path/to/chart.txt

# Windows
clean-chart.cmd -f C:\path\to\chart.txt
```

Output: `cleaned_charts/<name>_cleaned.txt` (see `-o` below).

### Folder batch (`.txt` only)

```bash
# macOS
./clean-chart -d /path/to/folder

# Windows
clean-chart.cmd -d C:\path\to\folder
```

Every `*.txt` in that directory is processed. Outputs go under `cleaned_charts/` by default.

### CLI options

| Option | Description |
|--------|-------------|
| `-f`, `--file` | Path to one text file. |
| `-d`, `--dir` | Path to a directory of `.txt` files. |
| `-o`, `--out` | Output directory for file/dir modes (default: `cleaned_charts`). |
| `--no-wrap` | Omit the `<patient_chart>` wrapper; plain cleaned text only. |
| `-h`, `--help` | Show help. |

Direct Python (after venv exists):

```bash
# macOS
.venv/bin/python medical_cleaner.py [args...]

# Windows
.venv\Scripts\python.exe medical_cleaner.py [args...]
```

---

## macOS Dock / double-click app

`Clean_Medical_Chart.applescript` runs the **`clean-chart`** script in the **same folder as the app** (not a hardcoded home path). Place:

- `Clean Medical Chart.app`
- `clean-chart`
- `medical_cleaner.py`, `config.json`, `.venv`, etc.

in one directory (e.g. `~/scripts`).

**Rebuild the app** after editing the AppleScript:

```bash
osacompile -o "Clean Medical Chart.app" Clean_Medical_Chart.applescript
```

On success you get a notification; on failure, a dialog with the error.

### Windows optional dialog

`Clean_Medical_Chart.ps1` runs clipboard mode and shows a **success** message box. If script execution is blocked, use `Clean_Medical_Chart.cmd` instead or create a shortcut:

`powershell.exe -ExecutionPolicy Bypass -File "C:\path\to\Clean_Medical_Chart.ps1"`

---

## Locked-down or no-admin computers

- **Everything installs under this folder** (`.venv` + packages). You do **not** need Administrator if Python is installed **for your user only** and you can write to the project directory (Desktop, Documents, USB if allowed).
- Prefer **`install.bat`** on Windows if PowerShell execution policy is restricted.
- **Corporate networks:** if `pip install` fails, you may need a proxy exception or an internal PyPI mirror—ask IT.
- **Copying a pre-built `.venv` from another machine** is fragile (paths, Python version, OS). Prefer running `install.sh` / `install.bat` on each machine when possible.

---

## Configuration (`config.json`)

Place **`config.json` next to `medical_cleaner.py`**. The file must include these **required** top-level keys (the shipped file is a working Epic-oriented example):

| Key | Purpose |
|-----|---------|
| `emr_line_metadata` | Array of regex strings: whole lines to remove (author lines, “Filed:”, Epic chrome, etc.). |
| `boilerplate` | Array of regex strings: multi-line or large blocks to strip (disclaimers, empty SmartSections, revision history, etc.). |
| `epic_phi_patterns` | Array of `[pattern, replacement]` pairs for structured PHI (MRN, DOB lines, contact lines, etc.). |
| `literal_replacements` | Array of `[pattern, replacement]` for abbreviations and small text fixes. |
| `clinical_headers` | Header names (without regex); matching lines become Markdown-style `## Header`. |

**Optional:**

| Key | Purpose |
|-----|---------|
| `duplicate_note_detection` | `enabled` (default true), `split_pattern`, `min_body_chars`, `similarity_threshold` — fold near-duplicate Epic note blocks (uses fuzzy match on note bodies). |
| `nlp_allow_list` | Lowercase tokens Presidio should **not** redact as person names (e.g. drug names that look like people). |

Invalid JSON or missing required keys causes a clear error and exit.

---

## Processing pipeline (order)

1. Remove lines matching `emr_line_metadata`.
2. Remove regions matching `boilerplate`.
3. Apply `epic_phi_patterns` substitutions.
4. **Presidio** analysis for `PERSON`, `PHONE_NUMBER`, `EMAIL_ADDRESS` (with `nlp_allow_list`).
5. Apply `literal_replacements`.
6. Trim trailing spaces; collapse excessive blank lines.
7. **Duplicate note** folding (if enabled).
8. **Fuzzy paragraph** deduplication (similar paragraphs collapsed).
9. Promote `clinical_headers` to `## …`; normalize simple bullet prefixes.
10. Optionally wrap in `<patient_chart>…</patient_chart>`.

---

## Troubleshooting

| Problem | What to try |
|---------|--------------|
| `No virtual environment found` | Run `./install.sh` (Mac) or `install.bat` / `install.ps1` (Windows) from this folder. |
| `Clipboard is empty` | Copy text again; on Linux ensure a clipboard tool is available for `pyperclip`. |
| `Clipboard error: …` | See stderr; sometimes another app locks the clipboard—retry. |
| spaCy / model errors | Use the pinned stack in `requirements.txt`; do not rely on `python -m spacy download` alone if pip is not visible to spaCy. |
| Regex errors on startup | `config.json` contains an invalid pattern; the error names the key and index. |
| Windows: “cannot run scripts” | Use `install.bat` and `Clean_Medical_Chart.cmd`, or `powershell -ExecutionPolicy Bypass -File …`. |

---

## Privacy and compliance

This tool **reduces** obvious PHI and EMR noise; it is **not** a guarantee of de-identification under HIPAA or other rules. **You** are responsible for what you paste, store, or send to third parties. Review output before sharing. Adjust `config.json` and Presidio settings for your institution’s policy.

---

## Project layout (reference)

```
medical_cleaner.py      # CLI entrypoint
config.json             # Rules and optional NLP allow-list
requirements.txt        # Python dependencies

install.sh              # macOS / Linux venv setup
clean-chart             # macOS/Linux launcher

install.bat             # Windows venv setup (CMD)
install.ps1             # Windows venv setup (PowerShell)
clean-chart.cmd         # Windows launcher
Clean_Medical_Chart.cmd # Windows clipboard-only (double-click)
Clean_Medical_Chart.ps1 # Windows clipboard + success dialog

Clean_Medical_Chart.applescript   # Source for macOS app
Clean Medical Chart.app           # Optional; rebuild with osacompile
```

---

## License

Use and modify for your own workflow. If you share forks publicly, keep compliance and privacy warnings prominent.
