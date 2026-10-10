@echo off
REM Double-click convenience: build MIDIPlayer -> dist\MIDIPlayer.dist\MIDIPlayer.exe
REM Everything (no absolute paths, vendored deps, resource pack) lives in build.bat.
call "%~dp0build.bat" MIDIPlayer.py
