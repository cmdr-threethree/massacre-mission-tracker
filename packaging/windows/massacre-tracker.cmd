@echo off
setlocal
title Massacre Mission Tracker

rem Default to the usual Windows journal location when ED_JOURNAL_DIR is unset.
rem --journal-dir and an explicit ED_JOURNAL_DIR still take precedence.
if not defined ED_JOURNAL_DIR set "ED_JOURNAL_DIR=%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous"

"%~dp0python\python.exe" -B -X utf8 -m massacre_tracker %*
if errorlevel 1 pause
