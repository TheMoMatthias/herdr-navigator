@echo off
rem Tab-bar status: print the line the Navigator daemon keeps fresh (no Python start);
rem without a daemon, compute it the old way (which also starts the daemon).
if exist "%~1" (type "%~1") else (call "%~dp0run.cmd" status)
