@echo off
setlocal

set "UPSTREAM_URL=https://github.com/HKUDS/OpenHarness"

git remote get-url upstream >nul 2>nul
if errorlevel 1 (
  echo Upstream remote not found. Adding upstream: %UPSTREAM_URL%
  git remote add upstream %UPSTREAM_URL%
  if errorlevel 1 (
    echo Failed to add upstream remote.
    exit /b 1
  )
)

echo [1/2] Fetching latest changes from upstream...
git fetch upstream
if errorlevel 1 (
  echo Failed to fetch from upstream.
  exit /b 1
)

echo [2/2] Merging upstream/main into current branch...
git merge upstream/main
if errorlevel 1 (
  echo Failed to merge upstream/main.
  exit /b 1
)

echo Upstream sync completed successfully.
exit /b 0
