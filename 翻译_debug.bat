@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
set "cpath=%~dp0"
set "cpath=%cpath:~0,-1%"

:: ========================================
:: 从 config.json 读取转录配置
:: ========================================
echo [配置] 正在读取 config.json 中的转录配置...

:: Python 内部直接用 UTF-8 写文件，绕过 Shell 重定向前编码损坏
set "cfg_tmp=%temp%\trans_cfg.txt"
python -c "import json;c=json.load(open(r'!cpath!\config.json','r',encoding='utf-8'))['transcription'];t=c;f=open(r'!cfg_tmp!','w',encoding='utf-8');f.write(t.get('infer_exe','')+'\n');f.write(t.get('model_dir','')+'\n');f.write(t.get('device','cuda')+'\n');f.write(t.get('compute_type','int8_float16')+'\n');f.write(t.get('audio_suffixes','mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv')+'\n');f.write(t.get('sub_formats','lrc')+'\n');f.close()"

< "!cfg_tmp!" (
    set /p "infer_exe="
    set /p "model_dir="
    set /p "device="
    set /p "compute_type="
    set /p "audio_suffixes="
    set /p "sub_formats="
)
del "!cfg_tmp!" 2>nul

echo   转录工具: !infer_exe!
echo   模型目录: !model_dir!
echo   设备: !device!
echo   计算类型: !compute_type!

:: 移除可能存在的首尾空格
for /f "tokens=*" %%a in ("!infer_exe!") do set "infer_exe=%%a"
for /f "tokens=*" %%a in ("!model_dir!") do set "model_dir=%%a"

:: 验证 infer.exe 是否存在
if not exist "!infer_exe!" (
    echo [警告] infer.exe 不存在: !infer_exe!
    echo         请检查 config.json 中 transcription.infer_exe 路径是否正确
    pause
    exit /b 1
)

:: ========================================
:: 检测50系显卡
:: ========================================
set "gpu_compute_type=!compute_type!"
for /f "tokens=*" %%a in ('nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul') do (
    set "gpu_name=%%a"
    echo !gpu_name! | findstr /I /R /C:"RTX 50[0-9][0-9]" >nul && (
        set "gpu_compute_type=float16"
        echo [检测到50系显卡: !gpu_name!]
        echo [自动切换compute_type为 float16]
        goto :gpu_detected
    )
)
:gpu_detected

:: ========================================
:: 获取拖入的文件夹路径
:: ========================================
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
if not exist "%src_dir%" (
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

:: ========================================
:: [0/2] 准备转录标记
:: ========================================
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

:: ========================================
:: 检测是否需要转录
:: ========================================
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

:: ========================================
:: [1/2] 执行日文转录
:: ========================================
echo [1/2] 正在执行日文转录...
echo     已存在同名 .lrc 的音频将自动跳过...
echo     转录工具: !infer_exe!
echo.

"!infer_exe!" ^
    --audio_suffixes="!audio_suffixes!" ^
    --sub_formats="!sub_formats!" ^
    --device="!device!" ^
    --task="transcribe" ^
    --compute_type="!gpu_compute_type!" ^
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

:: ========================================
:: [2/2] 翻译
:: ========================================
:translate
echo [2/2] 正在启动翻译...(自动处理留档和去重)
> "%cpath%\input_path.txt" echo(!src_dir!

cd /d "%cpath%"

:: 设置 MODEL_DIR 环境变量，让 model_config.py 从转录模型目录加载 PaddleOCR 模型
set "MODEL_DIR=!model_dir!"
echo [模型] MODEL_DIR=!MODEL_DIR!

:: 使用 Python 直接运行 main.py（调试模式，-u 禁用缓冲）
echo [DEBUG] Launching main.py...
python -u main.py

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