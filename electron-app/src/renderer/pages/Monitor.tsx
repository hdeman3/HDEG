import React, { useState, useEffect, useRef } from 'react';
import { Button, Progress, Tag } from 'antd';
import {
  PauseCircleOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useAppStore } from '../stores/useAppStore';
import type { LogEntry } from '../types';

const Monitor: React.FC = () => {
  const { workDir, isTranslating, setTranslating, progressPercent, progressStage, progressMessage } = useAppStore();
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [finished, setFinished] = useState(false);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const logIdRef = useRef(0);
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleMessage = (msg: Record<string, unknown>) => {
      if (msg?.type === 'progress') setTranslating(true);
    };

    const handleLog = (msg: Record<string, unknown>) => {
      logIdRef.current += 1;
      setLogs((prev) => [
        ...prev.slice(-199),
        {
          id: logIdRef.current,
          level: (msg?.level as 'info' | 'warn' | 'error') || 'info',
          message: String(msg?.message ?? ''),
          time: new Date().toLocaleTimeString(),
        },
      ]);
    };

    const handleDone = (code: unknown) => {
      setTranslating(false);
      setFinished(true);
      setExitCode(
        typeof code === 'object' && code
          ? (code as Record<string, unknown>).exitCode as number ?? null
          : null
      );
    };

    const handleError = (msg: Record<string, unknown>) => {
      logIdRef.current += 1;
      setLogs((prev) => [
        ...prev.slice(-199),
        {
          id: logIdRef.current,
          level: 'error',
          message: String(msg?.message ?? '未知错误'),
          time: new Date().toLocaleTimeString(),
        },
      ]);
    };

    window.electronAPI.on('python:message', handleMessage as never);
    window.electronAPI.on('python:log', handleLog as never);
    window.electronAPI.on('python:done', handleDone as never);
    window.electronAPI.on('python:error', handleError as never);

    return () => {
      window.electronAPI.off('python:message', handleMessage as never);
      window.electronAPI.off('python:log', handleLog as never);
      window.electronAPI.off('python:done', handleDone as never);
      window.electronAPI.off('python:error', handleError as never);
    };
  }, []);

  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [logs]);

  const handleCancel = async () => {
    await window.electronAPI.translate.cancel();
    setTranslating(false);
    setFinished(true);
  };

  const getStatusTag = () => {
    if (finished) {
      return exitCode === 0
        ? <Tag color="success" icon={<CheckCircleOutlined />} style={{ margin: 0 }}>完成</Tag>
        : <Tag color="error" icon={<CloseCircleOutlined />} style={{ margin: 0 }}>异常 ({exitCode})</Tag>;
    }
    if (isTranslating) {
      return <Tag color="processing" style={{ margin: 0 }}>运行中</Tag>;
    }
    return <Tag style={{ margin: 0 }}>等待开始</Tag>;
  };

  if (!workDir) {
    return (
      <div className="workspace-empty animate-fade-in">
        <div className="workspace-empty-icon"><PlayCircleOutlined /></div>
        <div className="workspace-empty-title">未选择工作目录</div>
        <div className="workspace-empty-desc">请先在「工作区」选择工作目录，并在「翻译配置」中启动翻译。</div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div className="monitor-header">
        <div>
          <div className="page-title">翻译监控</div>
          <div className="page-subtitle">实时查看翻译进度与日志输出</div>
        </div>
        <div className="monitor-status-row">
          {getStatusTag()}
          {isTranslating && (
            <Button danger icon={<PauseCircleOutlined />} onClick={handleCancel} size="small">
              取消
            </Button>
          )}
          {finished && (
            <Button
              type="default"
              icon={<ReloadOutlined />}
              onClick={() => window.location.reload()}
              size="small"
            >
              重新开始
            </Button>
          )}
        </div>
      </div>

      {/* Progress */}
      <div className="monitor-progress-card">
        <Progress
          percent={Math.round(progressPercent)}
          status={
            finished
              ? exitCode === 0 ? 'success' : 'exception'
              : isTranslating ? 'active' : 'normal'
          }
          strokeColor={
            finished
              ? exitCode === 0 ? 'var(--color-success)' : 'var(--color-danger)'
              : 'var(--color-accent)'
          }
          trailColor="var(--color-surface-3)"
          strokeWidth={8}
          format={(p) => `${p}%`}
        />
        {progressStage && (
          <div className="monitor-progress-stage">
            <span className="monitor-progress-stage-name">{progressStage}</span>
            {progressMessage && (
              <span className="monitor-progress-stage-msg">{progressMessage}</span>
            )}
          </div>
        )}
      </div>

      {/* Terminal Log Viewer */}
      <div className="monitor-terminal">
        <div className="monitor-terminal-header">
          <span className="monitor-terminal-title">输出日志</span>
          <div className="monitor-terminal-dots">
            <div className="monitor-terminal-dot red" />
            <div className="monitor-terminal-dot amber" />
            <div className="monitor-terminal-dot green" />
          </div>
        </div>

        <div className="monitor-terminal-body" ref={terminalRef}>
          {logs.length === 0 ? (
            <div className="monitor-terminal-empty">
              等待翻译进程输出...
            </div>
          ) : (
            logs.map((log) => (
              <div className="monitor-log-entry" key={log.id}>
                <span className="monitor-log-time">[{log.time}]</span>
                <span className={`monitor-log-text ${log.level}`}>
                  {log.message}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};

export default Monitor;