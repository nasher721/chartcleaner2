; Chart Cleaner hotkeys for Windows (AutoHotkey v2 — https://www.autohotkey.com, no admin needed)
;
;   Ctrl+Alt+C  clean the selected text
;   Ctrl+Alt+A  abbreviate the selected text
;   Ctrl+Alt+E  expand abbreviations in the selected text
;
; Keep this file in the integrations\windows folder of Chart Cleaner (it finds
; clean-chart.cmd two folders up). Double-click to run; to start it with
; Windows, put a shortcut to it in shell:startup.
#Requires AutoHotkey v2.0
#SingleInstance Force

CleanChart := A_ScriptDir "\..\..\clean-chart.cmd"

RunOnSelection(mode) {
    global CleanChart
    saved := ClipboardAll()
    A_Clipboard := ""
    Send "^c"
    if !ClipWait(1) {
        A_Clipboard := saved
        TrayTip "Select some text first.", "Chart Cleaner"
        return
    }
    ; clean-chart reads the clipboard and writes the result back to it.
    exitCode := RunWait('"' CleanChart '" --mode ' mode, , "Hide")
    if (exitCode != 0) {
        A_Clipboard := saved
        TrayTip "Chart Cleaner could not process the selection.", "Chart Cleaner"
        return
    }
    Send "^v"
    Sleep 300
    A_Clipboard := saved  ; put back what was on the clipboard before
}

^!c::RunOnSelection("clean --no-wrap")
^!a::RunOnSelection("abbreviations")
^!e::RunOnSelection("expand")
