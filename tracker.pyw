# Entry point for the background tracker (pythonw, no window). Launched by
# "Start Tracker.vbs" and the Windows Startup shortcut. Run by full path so the
# process command line names this project folder.
from wdt.tracker import main

main()
