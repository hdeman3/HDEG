@echo off
chcp 65001
set cpath=%~dp0
set cpath=%cpath:~0,-1%
if "%~1"=="" goto prompt_input
"%cpath%\infer.exe" --audio_suffixes="mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv" --sub_formats="srt,vtt,lrc" --output_dir="输出" --device="cuda" --task="transcribe" %*
goto end

:prompt_input
echo 请将音视频文件拖到此窗口，然后按回车:
set "input_files="
set /p "input_files="
if defined input_files goto run_input
goto no_input

:run_input
"%cpath%\infer.exe" --audio_suffixes="mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv" --sub_formats="srt,vtt,lrc" --output_dir="输出" --device="cuda" --task="transcribe" %input_files%
goto end

:no_input
echo 未提供输入文件。

:end
pause
