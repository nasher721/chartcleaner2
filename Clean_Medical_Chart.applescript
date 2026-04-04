-- Resolves clean-chart next to this app (or next to the script when run from Script Editor).
-- Rebuild the app after editing: osacompile -o "Clean Medical Chart.app" Clean_Medical_Chart.applescript

on run
	set myApp to path to me as alias
	tell application "Finder"
		set parentFolder to container of myApp as alias
	end tell
	set base to POSIX path of parentFolder
	if base does not end with "/" then set base to base & "/"
	set cleaner to base & "clean-chart"
	try
		do shell script quoted form of cleaner
		display notification "Cleaned text is on the clipboard." with title "Medical chart cleaner"
	on error errMsg number errNum
		display dialog errMsg buttons {"OK"} default button 1 with title "Medical chart cleaner" with icon stop
	end try
end run
