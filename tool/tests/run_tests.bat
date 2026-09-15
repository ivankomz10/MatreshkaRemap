@echo off
rem Run the checks against the source, using the app's own virtual environment.
rem Nothing extra is installed: the suite uses only what the app already needs.
rem
rem   run_tests.bat                everything, windows hidden
rem   run_tests.bat --launch       also start the built exe and close it again
rem   run_tests.bat --show         draw the windows while it runs
rem   run_tests.bat gizmo          only the checks whose name says gizmo
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment missing. Run:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

.venv\Scripts\python.exe "tests\run.py" %*
set CODE=%ERRORLEVEL%
echo.
if %CODE%==0 (echo All checks passed.) else (echo Something failed -- see above.)
pause
exit /b %CODE%
