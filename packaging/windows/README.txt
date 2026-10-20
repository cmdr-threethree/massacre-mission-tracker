Massacre Mission Tracker for Windows (portable)
================================================

This folder contains a private copy of Python and everything the tracker
needs. You do not need to install Python, pip, or any other software.

The application keeps all mission state in memory. It writes no database,
cache, log, clipboard, or configuration file, and it writes nothing into this
folder while running.


Quick start
-----------

1. Unblock the downloaded zip before extracting it (see "Downloaded from the
   internet" below).
2. Extract the whole zip to a folder of your choice. Do not run the launcher
   from inside the zip.
3. Double-click "massacre-tracker.cmd".

If no journal directory is found, the tracker shows an error screen with the
path it tried and offers Retry or Quit.

By default the launcher uses:

    %USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous

Pass --journal-dir to use a different folder:

    massacre-tracker.cmd --journal-dir "D:\Games\Elite Dangerous\logs"

An explicitly set ED_JOURNAL_DIR environment variable also takes precedence
over the built-in default.


Command-line options
--------------------

    --journal-dir PATH     Directory containing Journal.*.log files
    --days N               Days of journals to reconstruct (default: 8)
    --poll SECONDS         Directory polling interval (default: 2.0)
    --commander FID_OR_NAME
                           Initially select a commander
    --no-color             Disable colour
    --help                 Show all options

Run "massacre-tracker.cmd --help" for the full list.


Terminal
--------

Windows Terminal is recommended. The classic console host (conhost) also works
through Textual's Windows driver, with fewer colours and less reliable
clipboard integration.

Copy actions use the terminal clipboard where supported and always add the
text to the in-memory Clipboard tab (ctrl+y), which works everywhere.

If a startup error occurs when the launcher is double-clicked, the window stays
open so the message can be read. Press any key to close it.


Downloaded from the internet
----------------------------

Windows may mark a downloaded zip as blocked and SmartScreen may warn about an
unknown publisher. To unblock the archive before extracting it, right-click the
zip, choose Properties, tick "Unblock", and click OK. If you already extracted
it, unblock the files in the extracted folder the same way.

This bundle is not code-signed. SmartScreen warnings are expected for an
unsigned community tool. Verify that the zip came from the expected release
page before running it.


No installation required
------------------------

The "python" subfolder contains the official CPython embeddable distribution
and the exact runtime packages listed in the project repository. Deleting this
folder removes the tracker completely.


License
-------

The tracker is released into the public domain under the Unlicense. The bundled
Python distribution is covered by the Python Software Foundation License; see
python\LICENSE.txt. Bundled third-party packages retain their own licenses in
their .dist-info directories.
