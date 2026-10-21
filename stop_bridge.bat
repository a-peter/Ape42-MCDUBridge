@echo off
rem Beendet eine laufende MCDU-Bridge sauber (schliesst auch die MobiFlight-Verbindung).
title MCDU Bridge beenden
cd /d "%~dp0"
python mcdu_screen_bridge.py stop
rem "if errorlevel N" bedeutet ">= N", deshalb den hoechsten Code zuerst pruefen
if errorlevel 6 goto fehler
if errorlevel 5 goto haengt
if errorlevel 4 goto laeuft_nicht
if errorlevel 1 goto fehler
rem Erfolg: Meldung kurz stehen lassen
timeout /t 3 /nobreak >nul
goto :eof

:laeuft_nicht
timeout /t 3 /nobreak >nul
goto :eof

:haengt
echo.
echo Die Bridge reagiert nicht. Bitte ihr Fenster schliessen.
pause
goto :eof

:fehler
echo.
echo Fehler beim Beenden. Ist Python 3 installiert und im PATH?
pause