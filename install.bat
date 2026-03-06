@echo off
echo ============================================
echo   WoW Fishing Bot - Instalador
echo ============================================
echo.
echo Creando entorno virtual...
python -m venv venv
call venv\Scripts\activate
echo.
echo Instalando dependencias...
pip install -r requirements.txt
echo.
echo ============================================
echo   Instalacion completada!
echo   Ejecuta run.bat para iniciar el bot.
echo ============================================
pause
