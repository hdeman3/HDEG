@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
set "cpath=%~dp0"
set "cpath=%cpath:~0,-1%"

if "%~1"=="" (
    echo 请将音视频文件夹拖到此窗口，然后按回车:
    set "src_dir="
    set /p "src_dir="
    if not defined src_dir (
        echo 未提供输入文件夹。
        pause
        exit /b 1
    )
) else (
    set "src_dir=%~f1"
)

:: 检查路径存在性时禁用延迟变量扩展，避免文件夹名中的 ! 被解析
setlocal DisableDelayedExpansion
if not exist "%src_dir%\" (
    echo 错误: 指定的路径不是有效文件夹。
    pause
    exit /b 1
)
setlocal EnableDelayedExpansion

echo.
echo ========================================
echo 工作目录: !src_dir!
echo ========================================
echo.

echo [0/2] 准备转录标记...

set "has_ja_lrc=0"
for /f "delims=" %%f in ('dir /s /b "!src_dir!\*.ja.lrc" 2^>nul') do (
    set "has_ja_lrc=1"
    set "ja_full=%%~ff"
    set "ja_name=%%~nf"
    set "ja_dir=%%~dpf"
    set "base_name=!ja_name:~0,-3!"
    set "lrc_path=!ja_dir!!base_name!.lrc"

    if not exist "!lrc_path!" (
        copy "%%f" "!lrc_path!" >nul
        echo   恢复: !base_name!.lrc (从 .ja.lrc)
    )
)

echo [0/2] 准备完成。
echo.

set "need_transcribe=0"
set "audio_extensions=.mp3 .wav .flac .m4a .aac .ogg .wma .mp4 .mkv .avi .mov .webm .flv .wmv"

for %%e in (%audio_extensions%) do (
    for /f "delims=" %%a in ('dir /s /b "!src_dir!\*%%e" 2^>nul') do (
        set "audio_full=%%~fa"
        set "audio_name=%%~na"
        set "audio_dir=%%~dpa"
        set "ja_lrc_path=!audio_dir!!audio_name!.ja.lrc"
        if not exist "!ja_lrc_path!" (
            set "need_transcribe=1"
        )
    )
)

if "!need_transcribe!"=="0" (
    echo [1/2] 检测到所有音频已有 .ja.lrc 留档，跳过转录。
    echo.
    goto :translate
)

echo [1/2] 正在执行日文转录（GPU模式 - 输出到当前文件夹）...
echo     已存在同名 .lrc 的音频将自动跳过...
echo.

"%cpath%\infer.exe" ^
    --audio_suffixes="mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv" ^
    --sub_formats="lrc" ^
    --output_dir="输出" ^
    --device="cuda" ^
    --task="transcribe" ^
    "!src_dir!"

if errorlevel 1 (
    echo [错误] 转录过程出错。
    pause
    exit /b 1
)

echo [1/2] 转录阶段结束，正在留档日文歌词...

for %%e in (%audio_extensions%) do (
    for /f "delims=" %%a in ('dir /s /b "!src_dir!\*%%e" 2^>nul') do (
        set "audio_dir=%%~dpa"
        set "audio_name=%%~na"
        set "lrc_path=!audio_dir!!audio_name!.lrc"
        set "ja_lrc_path=!audio_dir!!audio_name!.ja.lrc"

        if exist "!lrc_path!" (
            if not exist "!ja_lrc_path!" (
                copy "!lrc_path!" "!ja_lrc_path!" >nul
                echo   留档: !audio_name!.ja.lrc
            )
        )
    )
)

echo.

:translate
echo [2/2] 正在启动翻译...（自动处理留档和去重）
> "%cpath%\input_path.txt" echo(!src_dir!

cd /d "%cpath%"
translate.exe

if errorlevel 1 (
    echo [错误] 翻译失败。
    del "%cpath%\input_path.txt" 2>nul
    pause
    exit /b 1
)

del "%cpath%\input_path.txt" 2>nul

echo.
echo ========================================
echo 全部完成！
echo.
echo 文件说明:
echo   .lrc     = 当前使用的中文歌词（播放器读取）
echo   .ja.lrc  = 日文原版留档
echo ========================================
pause