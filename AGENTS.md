## Learned User Preferences

- Prefers the medical chart cleaner to be easy to run from the Mac (Automator app / Dock-friendly flow, not only terminal).
- Wants to keep real clinical content while stripping supplementary noise, boilerplate, and EMR chrome.
- When asking to implement or fix something, expects concrete changes rather than extended discussion.

## Learned Workspace Facts

- Core cleaner lives in `medical_cleaner.py` beside `config.json`; rules (regex lists, headers, `nlp_allow_list`, duplicate-note settings) stay in JSON, not hardcoded in Python.
- Clipboard workflow uses a `clean-chart` helper script beside the app; `Clean_Medical_Chart.applescript` resolves the parent folder of the app/script and runs that `clean-chart`, then shows a notification (re-run `osacompile` after editing the applescript).
- Stack includes Presidio plus spaCy `en_core_web_sm`; downloading or verifying spaCy models requires `pip` or `uv` available in the environment (spaCy’s installer errors if neither is found).
- Pipeline targets Epic-style exports: strip line-level metadata and boilerplate, optional structuring for LLMs, and configurable near-duplicate note folding per `config.json`.
- **Windows:** Run `install.bat` once (CMD only, **no admin** — venv + packages live entirely under this folder). If PowerShell is allowed, `install.ps1` or `powershell -ExecutionPolicy Bypass -File .\install.ps1` is equivalent. Then `clean-chart.cmd` (CLI) or `Clean_Medical_Chart.cmd` (double-click clipboard, no PowerShell). Need **Python 3.10+** without elevation: use [python.org](https://www.python.org/downloads/) installer with **“Install for all users” unchecked** and **“Add python.exe to PATH”** checked (installs under your profile). Copy the whole project folder to Desktop/USB if the machine allows that.
- **Mac without admin:** If `python3` is already 3.10+ (IT image, Homebrew in your home, etc.), run `./install.sh` from a folder you own; no sudo. If Python isn’t available, IT must install it or you use a machine where you’ve already run install, then copy the folder (same Mac + same Python major.minor as the venv’s `pyvenv.cfg` home, or re-run `install.sh` after copy).
