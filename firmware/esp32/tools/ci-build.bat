@echo off
rem One-shot "configure + build" used by the lead and CI; same env as build.bat.
setlocal
set MSYSTEM=
set IDF_TOOLS_PATH=E:\Espressif
set IDF_PYTHON_ENV_PATH=E:\Espressif\python_env\idf5.5_py3.11_env
set IDF_PATH=E:\Espressif\frameworks\esp-idf-v5.5.6
call "%IDF_PATH%\export.bat" >nul
cd /d "%~dp0.."
idf.py set-target esp32s3
if errorlevel 1 exit /b 1
idf.py build
