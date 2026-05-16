@echo off
chcp 65001 >nul
echo ============================================
echo  Instalador - Tramear Isometrico (Inkscape)
echo ============================================
echo.

:: Carpeta de extensiones de usuario de Inkscape en Windows
set "DEST=%APPDATA%\inkscape\extensions"

if not exist "%DEST%" (
    echo Creando carpeta de extensiones...
    mkdir "%DEST%"
)

:: Copiar archivos del addon
echo Instalando archivos...
copy /Y "inkscape_addon\tramear_isometrico.inx" "%DEST%\" >nul
copy /Y "inkscape_addon\tramear_isometrico.py"  "%DEST%\" >nul

if not exist "%DEST%\tramear_core" mkdir "%DEST%\tramear_core"
copy /Y "inkscape_addon\tramear_core\*.py" "%DEST%\tramear_core\" >nul

echo.
echo OK - Addon instalado en:
echo    %DEST%
echo.
echo IMPORTANTE: Reinicia Inkscape.
echo Luego ve a:  Extensiones ^> Tramear isometrico ^> Tramear isometrico
echo.
pause
