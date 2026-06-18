@echo off
setlocal EnableDelayedExpansion
chcp 65001

:: 检测是否为50系显卡（RTX 5090/5080/5070等Blackwell架构）
set "gpu_compute_type=int8_float16"
for /f "tokens=*" %%a in ('nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul') do (
    set "gpu_name=%%a"
    echo !gpu_name! | findstr /I "RTX 50" >nul && (
        set "gpu_compute_type=float16"
        echo [检测到50系显卡: !gpu_name!]
        echo [自动切换compute_type为 float16]
        goto :gpu_detected
    )
)
:gpu_detected

set cpath=%~dp0
set cpath=%cpath:~0,-1%
if "%~1"=="" goto prompt_input
"%cpath%\infer.exe" --audio_suffixes="mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv" --sub_formats="srt,vtt,lrc" --device="cuda" --compute_type="!gpu_compute_type!" --task="transcribe" %*
goto end

:prompt_input
echo 请将音视频文件拖到此窗口，然后按回车:
set "input_files="
set /p "input_files="
if defined input_files goto run_input
goto no_input

:run_input
"%cpath%\infer.exe" --audio_suffixes="mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv" --sub_formats="srt,vtt,lrc" --device="cuda" --compute_type="!gpu_compute_type!" --task="transcribe" %input_files%
goto end

:no_input
echo 未提供输入文件。

:end
pause
