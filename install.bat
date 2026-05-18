@echo off
REM Instalador del addon Tramear Isometrico para Inkscape (Windows).

setlocal enabledelayedexpansion

set AQUI=%~dp0
set ORIGEN=%AQUI%inkscape_addon
set DESTINO=%APPDATA%\inkscape\extensions

if not exist "%ORIGEN%" (
    echo No se encuentra la carpeta inkscape_addon junto a este script.
    exit /b 1
)

if not exist "%DESTINO%" mkdir "%DESTINO%"
echo Copiando addon a: %DESTINO%
xcopy /e /i /y "%ORIGEN%\*" "%DESTINO%\" >nul

echo.
echo Instalacion de archivos completada.
echo.
echo Dependencias Python opcionales (XLSX y deteccion raster):
echo   - openpyxl                       -^> welding book .xlsx
echo   - opencv-python, Pillow, numpy   -^> deteccion raster
echo.
set /p RESP=Instalar dependencias con pip ahora? [y/N]
if /I "%RESP%"=="Y" (
    where python >nul 2>nul
    if errorlevel 1 (
        echo No se encuentra python en el PATH; instalalas manualmente.
    ) else (
        python -m pip install --user -r "%AQUI%requirements.txt"
    )
)

echo.
echo Listo. Abre Inkscape y busca: Extensiones -^> Tramear isometrico.
endlocal
