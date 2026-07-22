import React, { useState, useEffect, useRef } from 'react';
import { Typography, Button, Tooltip, Modal, Slider, message } from 'antd';
import {
  FolderOpenOutlined,
  SettingOutlined,
  PlayCircleOutlined,
  EditOutlined,
  BookOutlined,
  SunOutlined,
  MoonOutlined,
  PictureOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useAppStore } from './stores/useAppStore';
import Workspace from './pages/Workspace';
import TranslationAids from './pages/TranslationAids';
import Settings from './pages/Settings';
import Monitor from './pages/Monitor';
import Review from './pages/Review';

import appIcon from './assets/icon.png';

const { Text } = Typography;

type ThemeMode = 'light' | 'dark';

interface AppProps {
  themeMode: ThemeMode;
  onToggleTheme: () => void;
}

const menuItems = [
  { key: 'workspace', icon: <FolderOpenOutlined />, label: '工作区' },
  { key: 'aids',     icon: <BookOutlined />,        label: '辅助翻译' },
  { key: 'settings', icon: <SettingOutlined />,      label: '翻译配置' },
  { key: 'monitor',  icon: <PlayCircleOutlined />,   label: '运行监控' },
  { key: 'review',   icon: <EditOutlined />,         label: '结果审核' },
];

const pageMap: Record<string, React.FC> = {
  workspace: Workspace,
  aids:      TranslationAids,
  settings:  Settings,
  monitor:   Monitor,
  review:    Review,
};

const BG_STORAGE_KEY = 'zhuanyi_bg';
const BG_OPACITY_KEY = 'zhuanyi_bg_opacity';

const App: React.FC<AppProps> = ({ themeMode, onToggleTheme }) => {
  const { activeTab, setActiveTab, workDir, isTranslating } = useAppStore();
  const PageComponent = pageMap[activeTab] || Workspace;
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── 背景图状态 ──
  const [bgImage, setBgImage] = useState<string>(() =>
    localStorage.getItem(BG_STORAGE_KEY) || ''
  );
  const [bgOpacity, setBgOpacity] = useState<number>(() => {
    const v = localStorage.getItem(BG_OPACITY_KEY);
    return v ? parseFloat(v) : 0.15;
  });
  const [bgModalOpen, setBgModalOpen] = useState(false);

  useEffect(() => {
    localStorage.setItem(BG_STORAGE_KEY, bgImage);
  }, [bgImage]);

  useEffect(() => {
    localStorage.setItem(BG_OPACITY_KEY, String(bgOpacity));
  }, [bgOpacity]);

  // ── 开始翻译 ──
  const handleStartTranslate = async () => {
    if (!workDir) {
      message.warning('请先在「工作区」选择工作目录');
      setActiveTab('workspace');
      return;
    }
    if (isTranslating) {
      message.warning('翻译正在进行中');
      return;
    }
    setActiveTab('monitor');
    useAppStore.getState().setTranslating(true);
    await (window as any).electronAPI.translate.start(workDir);
  };

  // ── 选择背景图 ──
  const handleBgFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setBgImage(reader.result as string);
      setBgModalOpen(false);
    };
    reader.readAsDataURL(file);
  };

  return (
    <div className="app-shell animate-fade-in">
      {/* ── 背景图层 ── */}
      {bgImage && (
        <div
          className="app-bg-layer"
          style={{
            backgroundImage: `url(${bgImage})`,
            opacity: bgOpacity,
          }}
        />
      )}

      {/* ---- Top Bar ---- */}
      <div className="app-topbar">
        <div className="app-topbar-left">
          <img src={appIcon} alt="HdeG" className="app-brand-icon" />
          {workDir ? (
            <span className="app-workdir" title={workDir}>
              {workDir}
            </span>
          ) : (
            <Text style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
              未选择工作目录
            </Text>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, WebkitAppRegion: 'no-drag' } as React.CSSProperties as any}>
          <Tooltip title="更换背景">
            <Button type="text" icon={<PictureOutlined />} onClick={() => setBgModalOpen(true)} />
          </Tooltip>
          <Tooltip title={themeMode === 'dark' ? '切换到日间模式' : '切换到夜间模式'}>
            <Button
              type="text"
              icon={themeMode === 'dark' ? <SunOutlined /> : <MoonOutlined />}
              onClick={onToggleTheme}
              style={{ fontSize: 16 }}
            />
          </Tooltip>
        </div>
      </div>

      {/* ---- Body ---- */}
      <div className="app-body">
        {/* Sidebar */}
        <div className="app-sidebar">
          <div className="app-sidebar-section-label">导航菜单</div>
          <nav className="app-sidebar-nav">
            {menuItems.map((item) => (
              <button
                key={item.key}
                className={`app-sidebar-item ${activeTab === item.key ? 'active' : ''}`}
                onClick={() => setActiveTab(item.key)}
              >
                <span className="app-sidebar-item-icon">{item.icon}</span>
                <span className="app-sidebar-item-label">{item.label}</span>
                {activeTab === item.key && <span className="app-sidebar-item-indicator" />}
              </button>
            ))}
          </nav>

          {/* ── 全局开始翻译按钮 ── */}
          <div className="app-sidebar-footer">
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              block
              size="large"
              disabled={!workDir}
              loading={isTranslating}
              onClick={handleStartTranslate}
              style={{
                fontWeight: 600,
                height: 40,
                borderRadius: 'var(--radius-md)',
              }}
            >
              {isTranslating ? '翻译中...' : '开始翻译'}
            </Button>
          </div>
        </div>

        {/* Content */}
        <div className="app-content">
          <div className="app-content-inner">
            <PageComponent />
          </div>
        </div>
      </div>

      {/* ── 背景图设置弹窗 ── */}
      <Modal
        open={bgModalOpen}
        onCancel={() => setBgModalOpen(false)}
        footer={null}
        title="背景图设置"
        width={400}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <Button icon={<PictureOutlined />} onClick={() => fileInputRef.current?.click()}>
              选择图片文件
            </Button>
            {bgImage && (
              <Button
                style={{ marginLeft: 8 }}
                danger
                onClick={() => { setBgImage(''); setBgModalOpen(false); }}
              >
                移除背景
              </Button>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              style={{ display: 'none' }}
              onChange={handleBgFileChange}
            />
          </div>

          {bgImage && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <Text style={{ fontSize: 12, whiteSpace: 'nowrap' }}>透明度</Text>
                <Slider
                  min={0.02}
                  max={0.5}
                  step={0.01}
                  value={bgOpacity}
                  onChange={setBgOpacity}
                  style={{ flex: 1 }}
                  tooltip={{ formatter: (v) => `${Math.round((v || 0) * 100)}%` }}
                />
                <Text style={{ fontSize: 12, width: 36, textAlign: 'right' }}>
                  {Math.round(bgOpacity * 100)}%
                </Text>
              </div>
              <div
                style={{
                  width: '100%',
                  height: 120,
                  borderRadius: 8,
                  backgroundImage: `url(${bgImage})`,
                  backgroundSize: 'cover',
                  backgroundPosition: 'center',
                  opacity: bgOpacity,
                  border: '1px solid var(--color-hairline)',
                }}
              />
            </>
          )}
        </div>
      </Modal>
    </div>
  );
};

export default App;
