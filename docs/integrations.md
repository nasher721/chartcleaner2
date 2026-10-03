# Using Chart Cleaner from other apps

Everything here runs on your computer. Nothing sends chart text anywhere else.

## Text expanders — type `;sah`, get "subarachnoid hemorrhage"
My text rules → Abbreviations → **Export for text expander**, or:

```bash
./clean-chart --export-abbreviations plist        # macOS Text Replacements
./clean-chart --export-abbreviations espanso      # Espanso match file
./clean-chart --export-abbreviations textexpander # TextExpander CSV
clean-chart.cmd --export-abbreviations ahk        # AutoHotkey v2 (Windows)
```
- **macOS:** drag the `.plist` into System Settings → Keyboard → Text Replacements.
- **Espanso:** put `chartcleaner.yml` in the folder `espanso path` prints under "Config", in `match/`.
- Do-Not-Use abbreviations are never exported.

## Keyboard shortcuts on selected text
**macOS (Quick Actions).** Run once:
```bash
./install.sh --services          # or: python3 integrations/macos/make_quick_actions.py
```
This adds *Chart Cleaner – Clean / Abbreviate / Expand Selection* to every app's
Services menu. Then go to System Settings → Keyboard → Keyboard Shortcuts →
Services → Text and give each one a shortcut. Remove them with
`python3 integrations/macos/make_quick_actions.py --uninstall`.

**Windows (AutoHotkey v2, no admin).** Double-click
`integrations\windows\chart-cleaner-hotkeys.ahk`:
- `Ctrl+Alt+C` cleans the selected text
- `Ctrl+Alt+A` abbreviates it
- `Ctrl+Alt+E` expands abbreviations

To start it with Windows, put a shortcut to the file in `shell:startup`.

**Any launcher or script:**
```bash
pbpaste | ./clean-chart --stdin --stdout --mode abbreviations | pbcopy
./clean-chart --mode expand          # clipboard in → clipboard out
./clean-chart --prompt "Sign-out / handoff"
```

## Clipboard watcher
Settings → **Clipboard watcher** → *Clean Epic text as soon as it's copied*.
- While Chart Cleaner is open, any copied text that looks like an Epic chart is
  cleaned and the clipboard is replaced. On a Mac a notification confirms it.
- **Undo last clipboard clean** puts the original text back.
- Choose *Only notify me* to keep the original on the clipboard.
- From a terminal instead: `./clean-chart --watch-clipboard`.

## Claude Desktop and Claude Code (MCP)
Chart Cleaner includes an MCP server. It talks over stdio and opens no network port.

**Claude Code:**
```bash
claude mcp add chart-cleaner -- "/path/to/Chart Cleaner/clean-chart-mcp"
```
**Claude Desktop:** in Settings → Developer → Edit Config, add:
```json
{
  "mcpServers": {
    "chart-cleaner": { "command": "/path/to/Chart Cleaner/clean-chart-mcp" }
  }
}
```
On Windows, use `C:\\path\\to\\Chart Cleaner\\clean-chart-mcp.cmd`.

Tools:
- `clean_chart`, `abbreviate`, `expand_abbreviations`
- `render_prompt` (clean, then wrap in a prompt template)
- `list_presets`, `list_prompt_templates`
- `ask_chart` (on-device Ollama)

What Claude receives is cleaned according to your rules. Cleaning is not a
guarantee of de-identification, so treat it as clinical data.

## Local API (scripts, Raycast, Alfred, Keyboard Maestro)
Settings → **Local API** shows the token.
```bash
TOKEN=...   # Settings → Local API → Copy
curl -s http://127.0.0.1:8765/api/v1/clean -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"text": "Pt w/ HTN", "wrap": false}'
```
Routes:
- `/api/v1/health`
- `/api/v1/clean`, with optional `format`, `delta`, `trends` and `wrap` (the response also carries `fact_check`)
- `/api/v1/abbreviate`, `/api/v1/expand`
- `/api/v1/prompt`, with `template`

The API only answers this computer, rejects requests from web pages, needs the
token, and caps requests at 5 MB.

## Browser extension (Chrome / Edge) — for Epic web and Haiku
1. Open `chrome://extensions` (or `edge://extensions`) and turn on **Developer mode**.
2. Click **Load unpacked** and choose `integrations/browser-extension`.
3. Click the extension's icon and enter the app's port (from its address bar)
   and the token (Settings → Local API → Copy).
4. Select chart text, right-click, and choose **Clean / Abbreviate / Expand with
   Chart Cleaner**. The result is copied to your clipboard.

The extension can only reach `127.0.0.1`/`localhost`. It runs nothing on a page
until you use the menu. Hospital browser policies may block unpacked extensions.

## Prompts, exports and notes
- **Copy as prompt** (Clean page) wraps the cleaned chart in a template. Edit
  templates in Settings → Prompt templates. Placeholders: `{chart}`, `{delta}`,
  `{date}`.
- **Download** offers `.txt`, `.docx` and `.md` (with date and note type).
- **Copy for Epic** gives plain ASCII wrapped at 80 characters that pastes
  cleanly into Epic.
- Settings → **Notes folder** (for example an Obsidian vault) adds **Save to
  notes folder** to the Clean page.

## Manual checks (not covered by automated tests)
These can't run on the Linux machine the tests use. Try them once on your own computer:
- [ ] macOS Quick Actions appear under Services and replace the selected text.
- [ ] A keyboard shortcut assigned to a Quick Action works in TextEdit and in Epic.
- [ ] The Windows AutoHotkey hotkeys work in Notepad and in Epic.
- [ ] The browser extension loads unpacked, saves the token, and the right-click menu works on a test page.
- [ ] The clipboard watcher's macOS notification appears.
