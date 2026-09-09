@echo off
cd /d "%~dp0"
title ポーカー アクションアドバイザー

echo ==========================================
echo   テキサスホールデム アクションアドバイザー
echo ==========================================
echo.
echo アプリを起動します...
echo (このウィンドウを閉じるとアプリも終了します)
echo.

python app.py
if errorlevel 1 goto failed
goto done

:failed
echo.
echo [エラー] アプリが異常終了しました。上のメッセージを確認してください。
echo         Python が入っていない場合は python.org から入れてください。
pause

:done
