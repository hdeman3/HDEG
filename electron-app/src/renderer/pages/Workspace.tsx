import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Button, Table, Tag, Typography, message } from 'antd';
import {
  FolderOpenOutlined,
  ReloadOutlined,
  FileTextOutlined,
  SoundOutlined,
  FolderOutlined,
  ArrowRightOutlined,
  InboxOutlined,
} from '@ant-design/icons';
import { useAppStore } from '../stores/useAppStore';
import type { WorkItem, LrcFileInWork } from '../types';

const { Text } = Typography;

const Workspace: React.FC = () => {
  const { workDir, works, setWorkDir, setWorks, setActiveTab } = useAppStore();
  const [dragOver, setDragOver] = useState(false);
  const scanWorks = useCallback(async (dir: string) => {
    try {
      var result = await window.electronAPI.folder.scanWorks(dir);
      if (result.works && result.works.length > 0) {
        setWorks(result.works);
        localStorage.setItem('zhuanyi_basePath', dir);
        message.success('扫描完成：' + result.works.length + ' 个作品');
      } else {
        setWorks([]);
        message.warning('该目录下未找到 RJ 作品文件夹');
      }
    } catch(e) {
      message.error('扫描目录失败');
    }
  }, [setWorks]);

  const handleSelectFolder = async () => {
    const dir = await window.electronAPI.dialog.selectFolder();
    if (dir) {
      setWorkDir(dir);
      await scanWorks(dir);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);

    try {
      // ── 方案 1：通过 File.path 获取（Electron 原生支持）──
      const files = e.dataTransfer.files;
      var dirPath = '';
      for (var i = 0; i < files.length; i++) {
        var file = files[i] as File & { path?: string };
        if (file.path) {
          dirPath = file.path.replace(/[/\\][^/\\]*$/, '');
          break;
        }
      }
      if (dirPath) {
        setWorkDir(dirPath);
        await scanWorks(dirPath);
        return;
      }

      // ── 方案 2：浏览器模式 — 尝试通过 HTTP API 解析文件夹名 ──
      const isElectron = window.electronAPI?.isElectron;
      if (!isElectron) {
        var items = e.dataTransfer.items;
        if (items) {
          for (var j = 0; j < items.length; j++) {
            var entry = items[j].webkitGetAsEntry?.();
            if (entry && entry.isDirectory) {
              var folderName = entry.name;
              try {
                var basePath = localStorage.getItem('zhuanyi_basePath') || '';
                var resolveRes = await Promise.race([
                  fetch('/api/resolve-path', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ folderName: folderName, basePath: basePath }),
                  }),
                  new Promise<Response>(function(_, reject) {
                    setTimeout(function() { reject(new Error('timeout')); }, 3000);
                  }),
                ]);
                if (resolveRes.ok) {
                  var resolveData = await resolveRes.json();
                  if (resolveData.found && resolveData.path) {
                    setWorkDir(resolveData.path);
                    localStorage.setItem('zhuanyi_basePath', resolveData.path);
                    await scanWorks(resolveData.path);
                    return;
                  }
                }
              } catch(_e) { /* 浏览器模式不可用时安静失败 */ }

              message.info('已捕获「' + folderName + '」，点击「选择工作目录」按钮打开该文件夹');
              return;
            }
          }
        }
      }

      message.info('无法获取文件夹路径。请粘贴路径到输入框或点击选择。');
    } catch (err: any) {
      console.error('[Workspace] 拖放处理失败:', err);
      message.error('拖放处理失败: ' + (err?.message || err));
    }
  };

  var initialScanDone = useRef(false);

  useEffect(() => {
    // 只在首次加载时自动扫描，后续切换回来不重复扫
    if (workDir && !initialScanDone.current) {
      initialScanDone.current = true;
      scanWorks(workDir);
    }
  }, [workDir]);

  function getLrcStatus(rec: LrcFileInWork) {
    if (rec.hasCnLrc) return { label: '翻译', color: 'purple' as const };
    if (rec.hasJaLrc) return { label: '转录', color: 'blue' as const };
    return { label: '无', color: 'default' as const };
  }

  const lrcColumns = [
    {
      title: '音轨',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => {
        const trackName = name.replace(/\.(lrc|srt|vtt)$/i, '');
        return (
          <Text style={{ fontSize: 12, color: 'var(--color-text-primary)', lineHeight: 1.4 }}>
            {trackName}
          </Text>
        );
      },
    },
    {
      title: '状态',
      key: 'status',
      width: 80,
      render: (_: unknown, record: LrcFileInWork) => {
        const st = getLrcStatus(record);
        return <Tag color={st.color} style={{ margin: 0 }}>{st.label}</Tag>;
      },
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
      width: 90,
      render: (size: number) => {
        if (size < 1024) return `${size} B`;
        if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
        return `${(size / (1024 * 1024)).toFixed(1)} MB`;
      },
    },
  ];

  const totalLrcCount = works.reduce((sum, w) => sum + w.lrcFiles.length, 0);
  const totalAudioCount = works.reduce((sum, w) => sum + w.audioFiles.length, 0);

  var folderInputRef = React.useRef<HTMLInputElement>(null);
  var handleFolderInputChange = function(_e: React.ChangeEvent<HTMLInputElement>) {
    message.info('请点击下方「选择工作目录」按钮来选择文件夹');
    if (folderInputRef.current) folderInputRef.current.value = '';
  };

  const DropZone = (
    <div
      className={`workspace-dropzone ${dragOver ? 'drag-over' : ''}`}
      style={{
        border: `2px dashed ${dragOver ? 'var(--color-accent)' : 'var(--color-hairline)'}`,
        background: dragOver ? 'var(--color-accent-soft)' : 'var(--color-surface-2)',
        borderRadius: 'var(--radius-lg)',
        padding: '48px 24px',
        textAlign: 'center',
        cursor: 'pointer',
        transition: 'all 0.2s ease-out',
        marginBottom: 24,
      }}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onClick={function(e) {
        if ((e.target as HTMLElement).closest('.path-input-row')) return;
        if ((window as any).electronAPI?.dialog?.selectFolder) {
          handleSelectFolder();
        } else if (folderInputRef.current) {
          folderInputRef.current.click();
        }
      }}
    >
      <InboxOutlined style={{ fontSize: 48, color: dragOver ? 'var(--color-accent)' : 'var(--color-text-muted)', marginBottom: 16, display: 'block' }} />
      <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 8 }}>
        拖放文件夹到此处
      </div>
      <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
        或点击选择 / 拖入包含 RJ 作品文件夹的目录
      </div>

      <input
        ref={folderInputRef}
        type="file"
        // @ts-ignore
        webkitdirectory=""
        style={{ display: 'none' }}
        onChange={handleFolderInputChange}
      />

      <div style={{ textAlign: 'center', marginTop: 16 }}>
        <Button
          type="primary"
          size="large"
          icon={<FolderOpenOutlined />}
          onClick={function(e) { e.stopPropagation(); handleSelectFolder(); }}
        >
          选择工作目录
        </Button>
      </div>
    </div>
  );

  if (!workDir) {
    return (
      <div className="animate-fade-in">
        <div className="page-header">
          <div>
            <div className="page-title">工作区</div>
            <div className="page-subtitle">拖放文件夹或输入路径以扫描 RJ 音声作品</div>
          </div>
        </div>
        {DropZone}

        <div className="workspace-empty">
          <div className="workspace-empty-icon">
            <FolderOpenOutlined />
          </div>
          <div className="workspace-empty-title">支持格式</div>
          <div className="workspace-empty-desc">
            MP3, WAV, FLAC, M4A, MP4, MKV 等音视频文件 &middot; LRC / SRT / VTT 字幕文件 &middot; 自动识别 .ja.lrc (日文) 和 .cn.lrc (中文)
          </div>
        </div>
      </div>
    );
  }

  if (works.length === 0) {
    return (
      <div className="animate-fade-in">
        <div className="page-header">
          <div>
            <div className="page-title">工作区</div>
            <div className="page-subtitle">
              当前目录：<Text code style={{ fontSize: 12 }}>{workDir}</Text>
            </div>
          </div>
          <span style={{ display: 'flex', gap: 8 }}>
            <Button icon={<FolderOpenOutlined />} onClick={handleSelectFolder}>更换目录</Button>
            <Button icon={<ReloadOutlined />} onClick={() => scanWorks(workDir)}>重新扫描</Button>
          </span>
        </div>

        {DropZone}

        <div className="workspace-empty">
          <div className="workspace-empty-icon">
            <FolderOutlined />
          </div>
          <div className="workspace-empty-title">未找到 RJ 作品</div>
          <div className="workspace-empty-desc">
            该目录下未找到以 RJ 开头的作品文件夹（如 RJ01508941）。请确认该目录包含 RJ 音声作品。
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <div className="page-header">
        <div>
          <div className="page-title">工作区</div>
          <div className="page-subtitle">
            目录：<Text code style={{ fontSize: 12 }}>{workDir}</Text>
          </div>
        </div>
        <span style={{ display: 'flex', gap: 8 }}>
          <Button icon={<FolderOpenOutlined />} onClick={handleSelectFolder}>更换目录</Button>
          <Button icon={<ReloadOutlined />} onClick={() => scanWorks(workDir)}>刷新</Button>
          <Button type="primary" icon={<ArrowRightOutlined />} onClick={() => setActiveTab('settings')}>
            配置翻译
          </Button>
        </span>
      </div>

      {/* Stats */}
      <div className="workspace-stats">
        <div className="workspace-stat-card">
          <span className="workspace-stat-icon"><FolderOutlined /></span>
          <span className="workspace-stat-value" style={{ color: 'var(--color-accent)' }}>{works.length}</span>
          <span className="workspace-stat-label">作品总数</span>
        </div>
        <div className="workspace-stat-card">
          <span className="workspace-stat-icon"><FileTextOutlined /></span>
          <span className="workspace-stat-value" style={{ color: 'var(--color-success)' }}>{totalLrcCount}</span>
          <span className="workspace-stat-label">LRC 字幕文件</span>
        </div>
        <div className="workspace-stat-card">
          <span className="workspace-stat-icon"><SoundOutlined /></span>
          <span className="workspace-stat-value" style={{ color: 'var(--color-warning)' }}>{totalAudioCount}</span>
          <span className="workspace-stat-label">音频/视频文件</span>
        </div>
      </div>

      <div className="page-section-title">作品列表 &middot; {works.length}</div>

      {works.map((work: WorkItem) => (
        <div className="workspace-work-card" key={work.id}>
          <div className="workspace-work-header">
            <span className="workspace-work-id">{work.id}</span>
            <span className="workspace-work-meta">
              {work.hasScriptbook && <Tag color="green">台本</Tag>}
              <Tag>{work.lrcFiles.length} 字幕</Tag>
              <Tag>{work.audioFiles.length} 音频</Tag>
            </span>
          </div>

          <Table
            dataSource={work.lrcFiles}
            columns={lrcColumns}
            rowKey="path"
            size="small"
            pagination={false}
            locale={{ emptyText: '未找到 LRC 文件' }}
            style={{ marginBottom: work.audioFiles.length > 0 ? 12 : 0 }}
          />

          {work.audioFiles.length > 0 && (
            <div>
              <div className="page-section-title" style={{ marginTop: 0 }}>
                音频文件 &middot; {work.audioFiles.length}
              </div>
              <div className="workspace-file-chips">
                {work.audioFiles.map((f) => (
                  <Tag key={f} icon={<SoundOutlined />} style={{ fontSize: 12 }}>{f}</Tag>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

export default Workspace;