@echo off
rem Startet die MCDU-Bridge mit farbiger Debug-Anzeige. Beenden mit Strg+C.
rem Die Bridge laeuft nur einmal: Eine zweite Instanz beendet sich mit Exit-Code 3.
title MCDU Bridge
cd /d "%~dp0"
python mcdu_screen_bridge.py run --debug
rem "if errorlevel N" bedeutet ">= N", deshalb den hoechsten Code zuerst pruefen
if errorlevel 4 goto fehler
if errorlevel 3 goto laeuft_bereits
if errorlevel 1 goto fehler
goto :eof

:laeuft_bereits
echo.
echo Die Bridge laeuft bereits in einem anderen Fenster.
pause
goto :eof

:fehler
echo.
echo Die Bridge wurde mit Fehler beendet. Ist Python 3 installiert und im PATH?
pause
