@echo off
rem Tab-bar status: print the line the Navigator daemon keeps fresh (no Python start).
rem The daemon holds a lock on byte 0 of daemon.lock for as long as it lives, so a write there
rem fails exactly while it runs. When the write succeeds the daemon is gone and status.txt is a
rem leftover: compute the line the old way instead, which also starts a new daemon.
if not exist "%~1" goto fallback
(>>"%~dp1daemon.lock" echo.) 2>nul && goto fallback
type "%~1"
exit /b 0
:fallback
call "%~dp0run.cmd" status
