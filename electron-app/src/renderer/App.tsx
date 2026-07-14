import React from 'react';
import { Typography, Button, Tooltip } from 'antd';
import {
  FolderOpenOutlined,
  SettingOutlined,
  PlayCircleOutlined,
  EditOutlined,
  BookOutlined,
  SunOutlined,
  MoonOutlined,
} from '@ant-design/icons';
import { useAppStore } from './stores/useAppStore';
import Workspace from './pages/Workspace';
import TranslationAids from './pages/TranslationAids';
import Settings from './pages/Settings';
import Monitor from './pages/Monitor';
import Review from './pages/Review';

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

const App: React.FC<AppProps> = ({ themeMode, onToggleTheme }) => {
  const { activeTab, setActiveTab, workDir } = useAppStore();

  const PageComponent = pageMap[activeTab] || Workspace;

  return (
    <div className="app-shell animate-fade-in">
      {/* ---- Top Bar ---- */}
      <div className="app-topbar">
        <div className="app-topbar-left">
          <span className="app-brand">
            转<span className="app-brand-accent">译</span>
          </span>
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

        {/* Theme Toggle — explicitly no-drag so the button is clickable */}
        <div style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties as any}>
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
        </div>

        {/* Content */}
        <div className="app-content">
          <div className="app-content-inner">
            <PageComponent />
          </div>
        </div>
      </div>
    </div>
  );
};

export default App;
