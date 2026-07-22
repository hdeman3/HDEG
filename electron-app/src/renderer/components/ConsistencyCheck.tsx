import React, { useState, useEffect } from 'react';
import { Button, Tag, Spin, Empty, Collapse, Typography, Tooltip, message } from 'antd';
import {
  CheckCircleOutlined,
  WarningOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import type { ConsistencyReport, ConflictItem, TermCoverageItem } from '../types';

const { Text, Paragraph } = Typography;

interface Props {
  workPath: string;
}

const severityColors: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'blue',
};

const severityIcons: Record<string, React.ReactNode> = {
  high: <ExclamationCircleOutlined style={{ color: '#ff4d4f' }} />,
  medium: <WarningOutlined style={{ color: '#faad14' }} />,
  low: <InfoCircleOutlined style={{ color: '#1890ff' }} />,
};

const ConsistencyCheck: React.FC<Props> = ({ workPath }) => {
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<ConsistencyReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runCheck = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await (window as any).electronAPI.review.consistencyCheck(workPath);
      if (res.success) {
        setReport(res.data as ConsistencyReport);
      } else {
        setError(res.error || '检查失败');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '请求失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { runCheck(); }, [workPath]);

  if (loading) {
    return (
      <div className="workspace-empty" style={{ padding: 60 }}>
        <Spin size="large" />
        <div className="workspace-empty-title" style={{ marginTop: 16 }}>正在扫描译文...</div>
        <div className="workspace-empty-desc">提取关键词、比对译法、检查术语覆盖</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="workspace-empty" style={{ padding: 60 }}>
        <ExclamationCircleOutlined style={{ fontSize: 32, color: 'var(--color-danger)', opacity: 0.5 }} />
        <div className="workspace-empty-title" style={{ marginTop: 12 }}>{error}</div>
        <Button icon={<ReloadOutlined />} onClick={runCheck} style={{ marginTop: 12 }}>重试</Button>
      </div>
    );
  }

  if (!report) return null;

  const { summary, conflicts, uncovered_terms: uncovered } = report;

  // ── 统计卡片 ──
  const statCards = (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
      <StatCard icon={<ExclamationCircleOutlined />} color="var(--color-danger)" value={summary.high_conflicts} label="高严重" />
      <StatCard icon={<WarningOutlined />} color="var(--color-warning)" value={summary.medium_conflicts} label="中严重" />
      <StatCard icon={<CheckCircleOutlined />} color="var(--color-success)" value={`${conflicts.length}`} label="总冲突" />
      <StatCard icon={<FileTextOutlined />} color="var(--color-info)" value={`${summary.unused_terms}/${summary.total_terms_defined}`} label="未使用术语" />
    </div>
  );

  // ── 冲突列表 ──
  const conflictItems = conflicts.length === 0 ? [
    {
      key: 'empty',
      label: '🔴 翻译不一致 (0)',
      children: <Empty description="未发现译法不一致，翻译一致性良好" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
    },
  ] : [
    {
      key: 'conflicts',
      label: (
        <span>
          🔴 翻译不一致
          <Tag color="red" style={{ marginLeft: 8 }}>{conflicts.length}</Tag>
          {summary.high_conflicts > 0 && <Tag color="red" style={{ marginLeft: 4 }}>{summary.high_conflicts} 高</Tag>}
        </span>
      ),
      children: (
        <div style={{ maxHeight: 400, overflowY: 'auto' }}>
          {conflicts.slice(0, 100).map((item, i) => (
            <ConflictRow key={i} item={item} />
          ))}
          {conflicts.length > 100 && (
            <Text type="secondary" style={{ display: 'block', textAlign: 'center', padding: 12 }}>
              ... 还有 {conflicts.length - 100} 条未显示
            </Text>
          )}
        </div>
      ),
    },
  ];

  // ── 术语覆盖 ──
  const unmatchedItems = uncovered.filter(u => u.found && !u.match);
  const unusedItems = uncovered.filter(u => !u.found);

  const termItems = [];
  if (unmatchedItems.length > 0) {
    termItems.push({
      key: 'unmatched',
      label: (
        <span>
          🟡 术语偏离
          <Tag color="orange" style={{ marginLeft: 8 }}>{unmatchedItems.length}</Tag>
        </span>
      ),
      children: (
        <div style={{ maxHeight: 300, overflowY: 'auto' }}>
          {unmatchedItems.map((item, i) => (
            <div key={i} className="aids-char-row" style={{ justifyContent: 'flex-start', gap: 16 }}>
              <span className="aids-char-name" style={{ minWidth: 100 }}>{item.source}</span>
              <span style={{ color: 'var(--color-danger)' }}>实际: {item.actual}</span>
              <span style={{ color: 'var(--color-text-muted)' }}>→</span>
              <span style={{ color: 'var(--color-success)' }}>期望: {item.expected}</span>
            </div>
          ))}
        </div>
      ),
    });
  }
  if (unusedItems.length > 0) {
    termItems.push({
      key: 'unused',
      label: (
        <span>
          ⚪ 未使用术语
          <Tag style={{ marginLeft: 8 }}>{unusedItems.length}</Tag>
        </span>
      ),
      children: (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: 8 }}>
          {unusedItems.map((item, i) => (
            <Tooltip key={i} title={`期望译文: ${item.expected}`}>
              <Tag>{item.source} → {item.expected}</Tag>
            </Tooltip>
          ))}
        </div>
      ),
    });
  }
  if (termItems.length === 0 && Object.keys(uncovered).length > 0) {
    termItems.push({
      key: 'ok',
      label: '🟢 术语覆盖 (全部匹配)',
      children: <Empty description="术语表定义已全部在翻译中正确使用" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
    });
  } else if (termItems.length === 0) {
    termItems.push({
      key: 'noterms',
      label: '📋 术语覆盖',
      children: <Empty description="未使用术语表" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
    });
  }

  return (
    <div className="animate-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Text strong style={{ fontSize: 14 }}>
            {report.total_files} 文件 · {report.total_lines} 行
          </Text>
          <Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>
            {report.work_dir}
          </Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={runCheck} size="small">重新检查</Button>
      </div>

      {statCards}

      <Collapse
        defaultActiveKey={['conflicts', 'unmatched', 'unused']}
        items={[...conflictItems, ...termItems]}
        style={{ background: 'transparent' }}
      />
    </div>
  );
};

// ── 子组件 ──

const StatCard: React.FC<{ icon: React.ReactNode; color: string; value: string | number; label: string }> = ({ icon, color, value, label }) => (
  <div className="workspace-stat-card" style={{ textAlign: 'center', padding: 16 }}>
    <span style={{ fontSize: 20, color }}>{icon}</span>
    <span className="workspace-stat-value" style={{ fontSize: 22, color: 'var(--color-text-primary)' }}>{value}</span>
    <span className="workspace-stat-label">{label}</span>
  </div>
);

const ConflictRow: React.FC<{ item: ConflictItem }> = ({ item }) => (
  <div style={{
    padding: '10px 14px',
    borderBottom: '1px solid var(--color-border-subtle)',
    display: 'flex',
    alignItems: 'flex-start',
    gap: 12,
    transition: 'background 0.15s',
  }}>
    <span style={{ marginTop: 2 }}>{severityIcons[item.severity]}</span>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <Text strong style={{ fontSize: 13 }}>{item.source}</Text>
        <Tag color={severityColors[item.severity]} style={{ fontSize: 10, lineHeight: '16px' }}>
          {item.severity === 'high' ? '高严重' : item.severity === 'medium' ? '中严重' : '低'}
        </Tag>
        <Text type="secondary" style={{ fontSize: 11 }}>出现 {item.count} 次</Text>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {item.translations.map((t, i) => (
          <Tag key={i} color={i === 0 ? 'green' : 'red'}>{t}</Tag>
        ))}
      </div>
      <div style={{ marginTop: 4 }}>
        <Text type="secondary" style={{ fontSize: 11 }}>
          {item.files.slice(0, 3).join(', ')}
          {item.files.length > 3 ? ` 等 ${item.files.length} 个文件` : ''}
        </Text>
      </div>
    </div>
  </div>
);

export default ConsistencyCheck;
