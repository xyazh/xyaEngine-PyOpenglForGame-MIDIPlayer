@echo off
REM Double-click convenience: smallest MIDIPlayer build.
REM XYA_SLIM=1 drops cv2 (cv2.pyd 68 MB + its ffmpeg dll 27 MB, ~95 MB of the
REM dist). The player still runs - only SETTINGS -> SAVE VIDEO reports
REM "missing cv2/numpy" (the import is already wrapped in try/except).
REM Everything else (no absolute paths, vendored deps, LZMA resource pack,
REM dead DLL pruning) lives in build.bat; use build.bat directly for the
REM switches (XYA_PACK_COMPRESS, XYA_LTO, XYA_FULL, ...).
set XYA_SLIM=1
call "%~dp0build.bat" MIDIPlayer.py
