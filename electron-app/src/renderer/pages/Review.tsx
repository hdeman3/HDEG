import React, { useState, useEffect } from 'react';
import { Button, Input, Typography, message, Select, Tabs } from 'antd';
import { ReloadOutlined, SaveOutlined, EditOutlined, SwapOutlined, SoundOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { useAppStore } from '../stores/useAppStore';
import type { TranslationRow } from '../types';
import ConsistencyCheck from '../components/ConsistencyCheck';

const { Text } = Typography;
const { TextArea } = Input;

const Review: React.FC = () => {
  const { workDir, works } = useAppStore();
  const [selectedWorkId, setSelectedWorkId] = useState<string | null>(null);
  const [selectedTrack, setSelectedTrack] = useState<string | null>(null);
  const [allRows, setAllRows] = useState<TranslationRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editValues, setEditValues] = useState<Record<number, string>>({});
  const [modifiedSet, setModifiedSet] = useState<Set<number>>(new Set());
  const [reviewTab, setReviewTab] = useState<string>('review');

  var selectedWork = works.find(function(w) { return w.id === selectedWorkId; });
  var selectedWorkPath = selectedWork ? selectedWork.path : null;

  var tracks = Array.from(new Set(allRows.map(function(r) { return r.filename; }))).sort();

  var rows = selectedTrack
    ? allRows.filter(function(r) { return r.filename === selectedTrack; })
    : [];

  var trackModified = rows.filter(function(r) { return modifiedSet.has(r.index); });
  var modifiedCount = trackModified.length;

  useEffect(function() {
    if (!selectedWorkPath) { setAllRows([]); setSelectedTrack(null); return; }
    setLoading(true);
    setSelectedTrack(null);
    setEditValues({});
    setModifiedSet(new Set());
    (async function() {
      try {
        var res = await window.electronAPI.review.fetchResults(selectedWorkPath);
        if (res.success && res.data) {
          setAllRows(res.data as TranslationRow[]);
        } else {
          message.error(res.error || '获取失败');
          setAllRows([]);
        }
      } catch(e) {
        message.error('请求失败');
        setAllRows([]);
      } finally { setLoading(false); }
    })();
  }, [selectedWorkPath]);

  var handleEdit = function(index: number, value: string) {
    setEditValues(function(prev) {
      var n: Record<number,string> = {};
      Object.keys(prev).forEach(function(k) { n[Number(k)] = prev[Number(k)]; });
      n[index] = value;
      return n;
    });
    setModifiedSet(function(prev) { var s = new Set(prev); s.add(index); return s; });
  };

  var handleBatchSave = async function() {
    if (!selectedWorkPath || modifiedCount === 0) return;
    setSaving(true);
    var successCount = 0;
    var failCount = 0;
    var items = trackModified;
    for (var i = 0; i < items.length; i++) {
      var record = items[i];
      var newTranslation = editValues[record.index] ?? record.translation;
      try {
        var res = await window.electronAPI.review.saveEdit(selectedWorkPath, record.filename, record.index, newTranslation);
        if (res.success) {
          successCount++;
          setAllRows(function(prev) {
            return prev.map(function(r) {
              return r.index === record.index && r.filename === record.filename
                ? { ...r, translation: newTranslation } : r;
            });
          });
          setModifiedSet(function(prev) { var s = new Set(prev); s.delete(record.index); return s; });
          setEditValues(function(prev) {
            var n: Record<number,string> = {};
            Object.keys(prev).forEach(function(k) { var nk=Number(k); if(nk!==record.index)n[nk]=prev[nk]; });
            return n;
          });
        } else { failCount++; }
      } catch(e) { failCount++; }
    }
    if (failCount === 0) {
      message.success('全部保存成功（' + successCount + ' 条）');
    } else {
      message.warning('保存完成：' + successCount + ' 成功, ' + failCount + ' 失败');
    }
    setSaving(false);
  };

  var handleTrackChange = function(v: string) {
    if (modifiedCount > 0) {
      if (!confirm('当前音轨有 ' + modifiedCount + ' 条未保存的修改，切换将丢失修改。是否继续？')) return;
    }
    setSelectedTrack(v);
    setEditValues({});
    setModifiedSet(new Set());
  };

  if (!workDir) {
    return (
      <div className="workspace-empty animate-fade-in">
        <div className="workspace-empty-icon"><EditOutlined /></div>
        <div className="workspace-empty-title">未选择工作目录</div>
        <div className="workspace-empty-desc">请先在「工作区」选择工作目录。</div>
      </div>
    );
  }
  if (works.length === 0) {
    return (
      <div className="workspace-empty animate-fade-in">
        <div className="workspace-empty-icon"><EditOutlined /></div>
        <div className="workspace-empty-title">未找到 RJ 作品</div>
        <div className="workspace-empty-desc">请先在「工作区」扫描目录。</div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div className="review-toolbar">
        <div>
          <div className="page-title">翻译结果审核</div>
          <div className="page-subtitle">
            {selectedTrack
              ? '审核 ' + selectedTrack.replace(/^.*[\\/]/, '').replace(/\.(lrc|srt|vtt)$/i, '')
              : '选择作品和音轨'}
          </div>
        </div>
        <span style={{ display: 'flex', gap: 8 }}>
          {selectedWorkPath && (
            <Button icon={<ReloadOutlined />} onClick={function() { setSelectedWorkId(selectedWorkId); }} loading={loading}>刷新</Button>
          )}
          {modifiedCount > 0 && (
            <Button type="primary" icon={<SaveOutlined />} onClick={handleBatchSave} loading={saving}>
              保存 {modifiedCount} 条修改
            </Button>
          )}
        </span>
      </div>

      {/* Selectors */}
      <div style={{ marginBottom: 20, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <Text style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
          <SwapOutlined style={{ marginRight: 4 }} />作品：
        </Text>
        <Select value={selectedWorkId} onChange={function(v) { setSelectedWorkId(v); }}
          placeholder="选择 RJ 作品" style={{ minWidth: 220 }} size="small"
          options={works.map(function(w) { return { value: w.id, label: w.id + ' — ' + w.lrcFiles.length + ' 字幕' }; })} />
        {tracks.length > 0 && (
          <>
            <Text style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
              <SoundOutlined style={{ marginRight: 4 }} />音轨：
            </Text>
            <Select value={selectedTrack} onChange={handleTrackChange}
              placeholder="选择音轨" style={{ minWidth: 260 }} size="small"
              options={tracks.map(function(t) {
                var name = t.replace(/^.*[\\/]/, '').replace(/\.(lrc|srt|vtt)$/i, '');
                return { value: t, label: name };
              })} />
            <Text style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{rows.length} 行</Text>
            {modifiedCount > 0 && (
              <Text style={{ fontSize: 11, color: 'var(--color-accent)' }}>（{modifiedCount} 修改）</Text>
            )}
          </>
        )}
      </div>

      {/* Content */}
      {!selectedWorkId ? (
        <div className="workspace-empty" style={{ padding: 40 }}><div className="workspace-empty-icon"><EditOutlined /></div><div className="workspace-empty-title">请先选择作品</div></div>
      ) : !selectedWorkPath ? (
        <div className="workspace-empty" style={{ padding: 40 }}><div className="workspace-empty-icon"><SoundOutlined /></div><div className="workspace-empty-title">作品路径无效</div></div>
      ) : (
        <Tabs
          activeKey={reviewTab}
          onChange={setReviewTab}
          items={[
            {
              key: 'review',
              label: '音轨审核',
              children: !selectedTrack ? (
                <div className="workspace-empty" style={{ padding: 40 }}>
                  <div className="workspace-empty-icon"><SoundOutlined /></div>
                  <div className="workspace-empty-title">请选择音轨</div>
                  <div className="workspace-empty-desc">{tracks.length > 0 ? tracks.length + ' 个字幕文件可选' : '暂无翻译结果'}</div>
                </div>
              ) : rows.length === 0 && !loading ? (
                <div className="workspace-empty" style={{ padding: 40 }}><div className="workspace-empty-title">无内容</div></div>
              ) : (
                <div className="review-table-wrapper">
                  <div style={{ display: 'grid', gridTemplateColumns: '40px 80px 1fr 1.2fr', gap: 0, borderBottom: '1px solid var(--color-hairline)', background: 'var(--color-surface-1)' }}>
                    {['#', '时间戳', '原文', '译文'].map(function(h) {
                      return <div key={h} style={{ padding: '8px 12px', fontSize: 10, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</div>;
                    })}
                  </div>
                  <div style={{ maxHeight: 'calc(100vh - 300px)', overflowY: 'auto' }}>
                    {rows.filter(function(r) { return (r.original || '').trim() || (r.translation || '').trim(); }).map(function(record) {
                      var value = editValues[record.index] ?? record.translation;
                      var isModified = modifiedSet.has(record.index);
                      return (
                        <div key={record.filename + '-' + record.index}
                          style={{ display: 'grid', gridTemplateColumns: '40px 80px 1fr 1.2fr', gap: 0, borderBottom: '1px solid var(--color-border-subtle)', transition: 'background 0.15s', minHeight: 36, alignItems: 'start' }}
                          onMouseEnter={function(e) { (e.currentTarget as HTMLElement).style.background = 'var(--color-surface-hover)'; }}
                          onMouseLeave={function(e) { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}>
                          <div style={{ padding: '6px 8px', fontSize: 11, color: 'var(--color-text-muted)', lineHeight: '24px', textAlign: 'center' }}>{record.index}</div>
                          <div style={{ padding: '6px 8px' }}><span className="review-timestamp">{record.timestamp}</span></div>
                          <div style={{ padding: '6px 10px' }}>
                            <div style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--color-text-secondary)', wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}>{record.original}</div>
                          </div>
                          <div style={{ padding: '4px 10px' }}>
                            <TextArea className="review-edit-textarea" value={value}
                              onChange={function(e) { handleEdit(record.index, e.target.value); }}
                              autoSize={{ minRows: 1, maxRows: 4 }}
                              style={{ fontSize: 12, lineHeight: 1.5, background: isModified ? 'var(--color-accent-soft)' : undefined, borderColor: isModified ? 'var(--color-accent)' : undefined, resize: 'none' }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ),
            },
            {
              key: 'consistency',
              label: (
                <span>
                  <CheckCircleOutlined style={{ marginRight: 4 }} />
                  一致性检查
                </span>
              ),
              children: <ConsistencyCheck workPath={selectedWorkPath} />,
            },
          ]}
        />
      )}
    </div>
  );
};

export default Review;