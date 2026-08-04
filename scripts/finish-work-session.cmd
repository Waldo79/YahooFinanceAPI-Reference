@echo off
setlocal
cd /d "%~dp0.." || exit /b 1

for /f "delims=" %%A in ('git branch --show-current') do set "CURRENT_BRANCH=%%A"
if /i "%CURRENT_BRANCH%"=="main" goto :main_branch

git diff --check || goto :failed
py -m pytest -q --basetemp="%TEMP%\YahooFinanceAPI-Tests" || goto :failed

echo.
echo Validation passed on branch: %CURRENT_BRANCH%
git status --short
echo.
echo Review the files above, then commit and push this work branch.
exit /b 0

:main_branch
echo ERROR: Do not prepare new work directly on main.
echo Create or switch to a work branch first.
exit /b 1

:failed
echo ERROR: Validation failed. Nothing was committed or pushed.
exit /b 1
