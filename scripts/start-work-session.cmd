@echo off
setlocal
cd /d "%~dp0.." || exit /b 1

for /f "delims=" %%A in ('git status --porcelain') do goto :dirty

git switch main || goto :failed
git pull --ff-only origin main || goto :failed

echo.
echo Main is clean and synchronized with GitHub.
echo Create a work branch with: git switch -c work/your-branch-name
exit /b 0

:dirty
echo ERROR: The repository has uncommitted or untracked files.
echo Review them with: git status --short
exit /b 1

:failed
echo ERROR: The synchronization command failed. No automatic merge was attempted.
exit /b 1
