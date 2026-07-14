import React, { useState, useMemo, useEffect, useCallback } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import { setupBrowserAPI } from './browser-api';
import './styles.css';

// 在浏览器模式下自动注入 HTTP API 适配层
setupBrowserAPI();

// ────────────────────────────────────────
// Theme types
// ────────────────────────────────────────
type ThemeMode = 'light' | 'dark';

const THEME_STORAGE_KEY = 'zhuanyi_theme';

// ────────────────────────────────────────
// Shared base tokens (non-color)
// ────────────────────────────────────────
const sharedTokens = {
  fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
  fontSize: 13,
  fontSizeSM: 11,
  fontSizeLG: 14,
  fontSizeXL: 16,
  fontSizeHeading1: 24,
  fontSizeHeading2: 20,
  fontSizeHeading3: 16,
  fontSizeHeading4: 14,
  fontSizeHeading5: 13,
  lineHeight: 1.55,
  lineHeightSM: 1.4,
  lineHeightLG: 1.6,
  borderRadius: 6,
  borderRadiusLG: 8,
  borderRadiusSM: 4,
  borderRadiusXS: 2,
  controlHeight: 32,
  controlHeightSM: 24,
  controlHeightLG: 40,
  padding: 16,
  paddingSM: 8,
  paddingLG: 24,
  paddingXS: 4,
  margin: 16,
  marginSM: 8,
  marginLG: 24,
};

// ────────────────────────────────────────
// Shared component overrides
// ────────────────────────────────────────
const sharedComponents = {
  Card: {
    paddingLG: 16,
    padding: 16,
  },
  Button: {
    primaryShadow: 'none',
    defaultShadow: 'none',
    dangerShadow: 'none',
    paddingInline: 14,
    paddingInlineSM: 8,
    paddingInlineLG: 16,
  },
  Form: {
    itemMarginBottom: 12,
  },
};

// ────────────────────────────────────────
// 🌞 LIGHT theme
// ────────────────────────────────────────
const lightThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    ...sharedTokens,
    // Seed
    colorPrimary: '#3b82f6',
    colorBgBase: '#ffffff',

    // Derivative
    colorBgContainer: '#ffffff',
    colorBgElevated: '#ffffff',
    colorBgLayout: '#f8fafc',
    colorBgSpotlight: '#ffffff',
    colorBgMask: 'rgba(0, 0, 0, 0.25)',

    colorBorder: 'rgba(15, 23, 42, 0.06)',
    colorBorderSecondary: 'rgba(15, 23, 42, 0.04)',

    colorText: '#0f172a',
    colorTextSecondary: '#475569',
    colorTextTertiary: '#64748b',
    colorTextQuaternary: '#94a3b8',

    colorSuccess: '#10b981',
    colorWarning: '#f59e0b',
    colorError: '#ef4444',
    colorInfo: '#0ea5e9',

    boxShadow: '0 1px 2px rgba(0, 0, 0, 0.04)',
    boxShadowSecondary: '0 2px 8px rgba(0, 0, 0, 0.06)',
  },
  components: {
    ...sharedComponents,
    Table: {
      cellPaddingBlock: 8,
      cellPaddingInline: 12,
      headerBg: '#f8fafc',
      headerColor: '#475569',
      rowHoverBg: '#f9fafb',
      borderColor: 'rgba(15, 23, 42, 0.03)',
    },
    Menu: {
      itemBg: 'transparent',
      itemColor: '#475569',
      itemHoverColor: '#0f172a',
      itemHoverBg: '#f1f5f9',
      itemSelectedColor: '#3b82f6',
      itemSelectedBg: 'rgba(59, 130, 246, 0.12)',
      itemHeight: 32,
      itemMarginInline: 8,
      itemBorderRadius: 4,
      iconSize: 15,
    },
    Input: {
      paddingInline: 12,
      paddingBlock: 6,
      activeBorderColor: '#3b82f6',
      activeShadow: '0 0 0 2px rgba(59, 130, 246, 0.12)',
    },
    Select: {
      optionSelectedBg: 'rgba(59, 130, 246, 0.12)',
    },
    Tag: {
      defaultBg: 'rgba(15, 23, 42, 0.04)',
      defaultColor: '#475569',
    },
    Progress: {
      defaultColor: '#3b82f6',
      remainingColor: 'rgba(15, 23, 42, 0.04)',
    },
    Layout: {
      bodyBg: '#f8fafc',
      headerBg: '#ffffff',
      siderBg: '#ffffff',
      triggerBg: '#f1f5f9',
    },
    Form: {
      labelColor: '#475569',
    },
    Empty: {
      colorTextDescription: '#64748b',
    },
    Divider: {
      colorSplit: 'rgba(15, 23, 42, 0.05)',
    },
    Tooltip: {
      colorBgSpotlight: '#1e293b',
    },
  },
};

// ────────────────────────────────────────
// 🌙 DARK theme
// ────────────────────────────────────────
const darkThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    ...sharedTokens,
    // Seed
    colorPrimary: '#6C5CE7',
    colorBgBase: '#0B0C10',

    // Derivative
    colorBgContainer: '#1A1B21',
    colorBgElevated: '#22232B',
    colorBgLayout: '#131418',
    colorBgSpotlight: '#22232B',
    colorBgMask: 'rgba(0, 0, 0, 0.65)',

    colorBorder: 'rgba(255, 255, 255, 0.05)',
    colorBorderSecondary: 'rgba(255, 255, 255, 0.04)',

    colorText: '#EDEDEF',
    colorTextSecondary: '#9898A0',
    colorTextTertiary: '#5F5F68',
    colorTextQuaternary: '#3F3F46',

    colorSuccess: '#34D399',
    colorWarning: '#FBBF24',
    colorError: '#F87171',
    colorInfo: '#60A5FA',

    boxShadow: '0 1px 2px rgba(0, 0, 0, 0.30)',
    boxShadowSecondary: '0 2px 8px rgba(0, 0, 0, 0.40)',
  },
  components: {
    ...sharedComponents,
    Table: {
      cellPaddingBlock: 8,
      cellPaddingInline: 12,
      headerBg: '#131418',
      headerColor: '#9898A0',
      rowHoverBg: '#22232B',
      borderColor: 'rgba(255, 255, 255, 0.05)',
    },
    Menu: {
      itemBg: 'transparent',
      itemColor: '#9898A0',
      itemHoverColor: '#EDEDEF',
      itemHoverBg: '#1E1F25',
      itemSelectedColor: '#6C5CE7',
      itemSelectedBg: 'rgba(108, 92, 231, 0.12)',
      itemHeight: 32,
      itemMarginInline: 8,
      itemBorderRadius: 4,
      iconSize: 15,
    },
    Input: {
      paddingInline: 12,
      paddingBlock: 6,
      activeBorderColor: '#6C5CE7',
      activeShadow: '0 0 0 2px rgba(108, 92, 231, 0.12)',
    },
    Select: {
      optionSelectedBg: 'rgba(108, 92, 231, 0.12)',
    },
    Tag: {
      defaultBg: 'rgba(255, 255, 255, 0.04)',
      defaultColor: '#9898A0',
    },
    Progress: {
      defaultColor: '#6C5CE7',
      remainingColor: 'rgba(255, 255, 255, 0.06)',
    },
    Layout: {
      bodyBg: '#0B0C10',
      headerBg: '#131418',
      siderBg: '#131418',
      triggerBg: '#1A1B21',
    },
    Form: {
      labelColor: '#9898A0',
    },
    Empty: {
      colorTextDescription: '#5F5F68',
    },
    Divider: {
      colorSplit: 'rgba(255, 255, 255, 0.05)',
    },
    Tooltip: {
      colorBgSpotlight: '#22232B',
    },
  },
};

// ────────────────────────────────────────
// Theme Wrapper — reads persisted theme,
// sets data-theme on <html>, and provides
// the correct Ant Design ConfigProvider.
// ────────────────────────────────────────
const ThemeWrapper: React.FC = () => {
  const [mode, setMode] = useState<ThemeMode>(() => {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
    // Default to dark — matches the original app aesthetic
    return 'dark';
  });

  // Keep data-theme attribute in sync
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', mode);
  }, [mode]);

  const toggleTheme = useCallback(() => {
    setMode((prev) => {
      const next: ThemeMode = prev === 'dark' ? 'light' : 'dark';
      localStorage.setItem(THEME_STORAGE_KEY, next);
      return next;
    });
  }, []);

  const config = mode === 'dark' ? darkThemeConfig : lightThemeConfig;

  return (
    <ConfigProvider locale={zhCN} theme={config}>
      <App themeMode={mode} onToggleTheme={toggleTheme} />
    </ConfigProvider>
  );
};

const root = createRoot(document.getElementById('root')!);
root.render(<ThemeWrapper />);
