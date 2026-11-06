@echo off
rem StatusCast ESP32 build wrapper for cmd.exe.
rem Clears MSYSTEM first: ESP-IDF refuses MSYS/Mingw environments, and the
rem installer's python environment is a native Windows venv.
setlocal
set MSYSTEM=
set IDF_TOOLS_PATH=E:\Espressif
set IDF_PYTHON_ENV_PATH=E:\Espressif\python_env\idf5.5_py3.11_env
set IDF_PATH=E:\Espressif\frameworks\esp-idf-v5.5.6
call "%IDF_PATH%\export.bat" >nul
cd /d "%~dp0.."
idf.py %*
