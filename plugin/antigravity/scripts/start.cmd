@echo off
rem Antigravity Marketplace launcher: runs the NexusAndroid engine installed on this PC as an stdio relay to its
rem shared service (NexusAndroid.exe --bridge). A Marketplace plugin can only name its own folder, so the install
rem records its engine in %USERPROFILE%\.nexus\nexus-android\engine.txt (setup, run by get.ps1 and install.ps1):
rem a path relative to that folder, or an absolute one.
setlocal
set "STATE=%USERPROFILE%\.nexus\nexus-android"
set "ENGINE="
if exist "%STATE%\engine.txt" set /p ENGINE=<"%STATE%\engine.txt"
if not defined ENGINE goto missing
if not "%ENGINE:~1,1%"==":" if not "%ENGINE:~0,2%"=="\\" set "ENGINE=%STATE%\%ENGINE%"
if exist "%ENGINE%" goto run
:missing
>&2 echo NexusAndroid is not installed on this PC, or its version predates the Antigravity Marketplace launcher. Ask Antigravity to install or update it ^(see the nexus-android skill^), or follow https://github.com/pironjulien/NexusAndroid#readme, then open a new conversation.
exit /b 1
:run
"%ENGINE%" --bridge %*
exit /b %ERRORLEVEL%