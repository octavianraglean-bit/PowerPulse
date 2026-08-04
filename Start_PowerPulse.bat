@echo off
title PowerPulse - PC & Monitor Power Tracker
echo Starting PowerPulse Server...
start "" "http://127.0.0.1:5000"
python "%~dp0backend.py"
pause
