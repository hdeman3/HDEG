import React, { useState, useEffect } from 'react';
import { Button, Input, Typography, message, Select, Empty, Tag, Tabs } from 'antd';
import {
  PlusOutlined, DeleteOutlined, SaveOutlined, BookOutlined,
  SwapOutlined, GlobalOutlined, SoundOutlined,
} from '@ant-design/icons';
import { useAppStore } from '../stores/useAppStore';
import type { AliasItem, WorldviewCharacter } from '../types';

const { Text } = Typography;
const { TextArea } = Input;

const TranslationAids: React.FC = () => {
  const { workDir, works } = useAppStore();
  const [selectedWorkId, setSelectedWorkId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string>('terms');

  // ---- Terms state ----
  const [terms, setTerms] = useState<Record<string, string>>({});
  const [newTermKey, setNewTermKey] = useState('');
  const [newTermVal, setNewTermVal] = useState('');
  const [editingTerm, setEditingTerm] = useState<string | null>(null);  // 正在编辑的术语 key
  const [editTermVal, setEditTermVal] = useState('');                   // 编辑中的值

  // ---- Alias state ----
  const [aliasList, setAliasList] = useState<AliasItem[]>([]);
  const [newAliasFrom, setNewAliasFrom] = useState('');
  const [newAliasTo, setNewAliasTo] = useState('');
  const [newAliasConf, setNewAliasConf] = useState(0.9);
  const [editingAlias, setEditingAlias] = useState<number | null>(null); // 正在编辑的 alias 索引
  const [editAliasFrom, setEditAliasFrom] = useState('');
  const [editAliasTo, setEditAliasTo] = useState('');

  // ---- Worldview state ----
  const [worldviewText, setWorldviewText] = useState('');
  const [characters, setCharacters] = useState<Record<string, string>>({});
  const [charArray, setCharArray] = useState<WorldviewCharacter[]>([]);
  const [newCharName, setNewCharName] = useState('');
  const [newCharDesc, setNewCharDesc] = useState('');
  const [sceneText, setSceneText] = useState('');
  const [themes, setThemes] = useState<string[]>([]);
  const [specialTerms, setSpecialTerms] = useState<Record<string, string>>({});
  const [newSTKey, setNewSTKey] = useState('');
  const [newSTVal, setNewSTVal] = useState('');
  const [newTheme, setNewTheme] = useState('');

  // ---- Save state ----
  const [savingTerms, setSavingTerms] = useState(false);
  const [savingAlias, setSavingAlias] = useState(false);
  const [savingWorldview, setSavingWorldview] = useState(false);

  const selectedWork = works.find(function(w) { return w.id === selectedWorkId; });
  const selectedWorkPath = selectedWork ? selectedWork.path : null;

  // ---- Load all aids when work changes ----
  useEffect(function() {
    if (!selectedWorkPath) {
      setTerms({});
      setAliasList([]);
      setWorldviewText('');
      setCharacters({});
      setSceneText('');
      return;
    }
    (async function() {
      try {
        console.log('[TranslationAids] 读取辅助翻译, workDir:', selectedWorkPath);
        console.log('[TranslationAids] window.electronAPI:', typeof window.electronAPI, Object.keys(window.electronAPI || {}));
        const data = await (window as any).electronAPI.aid.read(selectedWorkPath);
        console.log('[TranslationAids] 读取结果:', {
          termsCount: Object.keys(data.terms || {}).length,
          aliasCount: (data.alias || []).length,
          worldviewKeys: Object.keys(data.worldview || {}),
        });
        setTerms(data.terms || {});
        setAliasList(data.alias || []);
        const wv = data.worldview || {};
        setWorldviewText(typeof wv.worldview === 'string' ? wv.worldview : '');
        setSceneText(typeof wv.scene === 'string' ? wv.scene : '');

        // 处理 characters: 可能是简单 {name: desc} 或复杂 [{name, role, ...}]
        const chars = wv.characters;
        if (Array.isArray(chars)) {
          setCharArray(chars as WorldviewCharacter[]);
          // 也转换为简单格式用于编辑
          var simpleChars: Record<string, string> = {};
          (chars as WorldviewCharacter[]).forEach(function(c) {
            if (c.name) {
              var parts = [c.role, c.personality, c.description].filter(Boolean).join(', ');
              simpleChars[c.name] = parts || '';
            }
          });
          setCharacters(simpleChars);
        } else if (chars && typeof chars === 'object') {
          setCharacters(chars as Record<string, string>);
          setCharArray([]);
        } else {
          setCharacters({});
          setCharArray([]);
        }

        // 扩展字段: themes, special_terms
        setThemes(Array.isArray(wv.themes) ? wv.themes as string[] : []);
        setSpecialTerms(wv.special_terms && typeof wv.special_terms === 'object' ? wv.special_terms as Record<string, string> : {});
      } catch(e) {
        console.error('[TranslationAids] 读取失败:', e);
        message.error('读取辅助翻译数据失败: ' + (e instanceof Error ? e.message : String(e)));
        setTerms({});
        setAliasList([]);
        setWorldviewText('');
        setCharacters({});
        setCharArray([]);
        setSceneText('');
        setThemes([]);
        setSpecialTerms({});
      }
    })();
  }, [selectedWorkPath]);

  // ============ Terms helpers ============
  const handleAddTerm = function() {
    if (!newTermKey.trim() || !newTermVal.trim()) return;
    setTerms(function(prev) {
      var next: Record<string, string> = {};
      var keys = Object.keys(prev);
      for (var i = 0; i < keys.length; i++) { next[keys[i]] = prev[keys[i]]; }
      next[newTermKey.trim()] = newTermVal.trim();
      return next;
    });
    setNewTermKey('');
    setNewTermVal('');
  };

  const handleRemoveTerm = function(key: string) {
    setTerms(function(prev) {
      var next: Record<string, string> = {};
      var keys = Object.keys(prev);
      for (var i = 0; i < keys.length; i++) {
        if (keys[i] !== key) next[keys[i]] = prev[keys[i]];
      }
      return next;
    });
  };

  // 开始编辑术语的值
  const startEditTerm = function(key: string) {
    setEditingTerm(key);
    setEditTermVal(terms[key] || '');
  };

  // 保存编辑中的术语值
  const commitEditTerm = function(key: string) {
    if (editTermVal.trim()) {
      setTerms(function(prev) {
        var next: Record<string, string> = {};
        var keys = Object.keys(prev);
        for (var i = 0; i < keys.length; i++) {
          next[keys[i]] = keys[i] === key ? editTermVal.trim() : prev[keys[i]];
        }
        return next;
      });
    }
    setEditingTerm(null);
  };

  const handleSaveTerms = async function() {
    if (!selectedWorkPath) return;
    setSavingTerms(true);
    try {
      await (window as any).electronAPI.aid.save(selectedWorkPath, 'terms', terms);
      message.success('术语表已保存到 ' + (selectedWorkId || ''));
    } catch(e) {
      message.error('保存术语表失败');
    } finally {
      setSavingTerms(false);
    }
  };

  // ============ Alias helpers ============
  const handleAddAlias = function() {
    if (!newAliasFrom.trim() || !newAliasTo.trim()) return;
    setAliasList(function(prev) {
      return prev.concat([{ alias: newAliasFrom.trim(), target: newAliasTo.trim(), confidence: newAliasConf }]);
    });
    setNewAliasFrom('');
    setNewAliasTo('');
  };

  const handleRemoveAlias = function(idx: number) {
    setAliasList(function(prev) {
      return prev.filter(function(_, i) { return i !== idx; });
    });
  };

  const startEditAlias = function(idx: number) {
    setEditingAlias(idx);
    setEditAliasFrom(aliasList[idx].alias);
    setEditAliasTo(aliasList[idx].target);
  };

  const commitEditAlias = function(idx: number) {
    if (editAliasFrom.trim() && editAliasTo.trim()) {
      setAliasList(function(prev) {
        var next = prev.slice();
        next[idx] = { alias: editAliasFrom.trim(), target: editAliasTo.trim(), confidence: prev[idx].confidence };
        return next;
      });
    }
    setEditingAlias(null);
  };

  const handleAliasConfChange = function(idx: number, val: string) {
    var num = parseFloat(val);
    if (isNaN(num)) num = 0;
    if (num < 0) num = 0;
    if (num > 1) num = 1;
    setAliasList(function(prev) {
      var next = prev.slice();
      next[idx] = { alias: next[idx].alias, target: next[idx].target, confidence: num };
      return next;
    });
  };

  const handleSaveAlias = async function() {
    if (!selectedWorkPath) return;
    setSavingAlias(true);
    try {
      await (window as any).electronAPI.aid.save(selectedWorkPath, 'alias', aliasList);
      message.success('别名表已保存到 ' + (selectedWorkId || ''));
    } catch(e) {
      message.error('保存别名表失败');
    } finally {
      setSavingAlias(false);
    }
  };

  // ============ Worldview helpers ============
  const handleAddCharacter = function() {
    if (!newCharName.trim() || !newCharDesc.trim()) return;
    setCharacters(function(prev) {
      var next: Record<string, string> = {};
      var keys = Object.keys(prev);
      for (var i = 0; i < keys.length; i++) { next[keys[i]] = prev[keys[i]]; }
      next[newCharName.trim()] = newCharDesc.trim();
      return next;
    });
    setNewCharName('');
    setNewCharDesc('');
  };

  const handleRemoveCharacter = function(name: string) {
    setCharacters(function(prev) {
      var next: Record<string, string> = {};
      var keys = Object.keys(prev);
      for (var i = 0; i < keys.length; i++) {
        if (keys[i] !== name) next[keys[i]] = prev[keys[i]];
      }
      return next;
    });
  };

  const handleSaveWorldview = async function() {
    if (!selectedWorkPath) return;
    setSavingWorldview(true);
    try {
      // 保留原始复杂格式的 charArray（如果有），否则用简单 characters
      var charsToSave: Record<string, string> | WorldviewCharacter[] = charArray.length > 0 ? charArray : characters;
      var data: Record<string, unknown> = {
        worldview: worldviewText,
        characters: charsToSave,
        scene: sceneText,
        themes: themes,
        special_terms: specialTerms,
      };
      await (window as any).electronAPI.aid.save(selectedWorkPath, 'worldview', data);
      message.success('世界观已保存到 ' + (selectedWorkId || ''));
    } catch(e) {
      message.error('保存世界观失败');
    } finally {
      setSavingWorldview(false);
    }
  };

  // ============ Empty states ============
  if (!workDir) {
    return (
      <div className="workspace-empty animate-fade-in">
        <div className="workspace-empty-icon"><BookOutlined /></div>
        <div className="workspace-empty-title">未选择工作目录</div>
        <div className="workspace-empty-desc">请先在「工作区」选择工作目录后再配置辅助翻译。</div>
      </div>
    );
  }

  if (works.length === 0) {
    return (
      <div className="workspace-empty animate-fade-in">
        <div className="workspace-empty-icon"><BookOutlined /></div>
        <div className="workspace-empty-title">未找到 RJ 作品</div>
        <div className="workspace-empty-desc">请先在「工作区」扫描目录，然后再为每个作品配置辅助翻译。</div>
      </div>
    );
  }

  // ============ Tab items ============
  var termCount = Object.keys(terms).length;
  var aliasCount = aliasList.length;
  var charCount = Object.keys(characters).length;

  var tabItems = [
    // ── Tab 1: 术语表 ──
    {
      key: 'terms',
      label: '术语表 (' + termCount + ')',
      children: (
        <div className="aids-tab-content">
          {!selectedWorkId ? (
            <div className="workspace-empty" style={{ padding: '40px 24px' }}>
              <div className="workspace-empty-title">请先选择作品</div>
              <div className="workspace-empty-desc">在上方下拉菜单中选择要编辑的 RJ 作品。</div>
            </div>
          ) : (
            <div className="aids-layout">
              <div className="aids-add-panel">
                <div className="aids-add-title">为 {selectedWorkId} 添加术语</div>
                <div className="aids-add-inputs">
                  <Input placeholder="日文术语" value={newTermKey}
                    onChange={function(e) { setNewTermKey(e.target.value); }}
                    onPressEnter={handleAddTerm} size="small" />
                  <Input placeholder="中文翻译" value={newTermVal}
                    onChange={function(e) { setNewTermVal(e.target.value); }}
                    onPressEnter={handleAddTerm} size="small" />
                </div>
                <Button type="dashed" icon={<PlusOutlined />} onClick={handleAddTerm}
                  block size="small" disabled={!newTermKey.trim() || !newTermVal.trim()}>
                  添加
                </Button>
                <div style={{ marginTop: 16 }}>
                  <Text style={{ fontSize: 11, color: 'var(--color-text-muted)', lineHeight: 1.6, display: 'block' }}>
                    提示：Enter 快速添加 &middot; 点击中文翻译值可编辑 &middot; 术语将在翻译时自动替换匹配的日文词汇
                  </Text>
                </div>
              </div>
              <div className="aids-term-list">
                <div className="aids-term-list-header">
                  <span className="aids-term-count">
                    {selectedWorkId} 术语列表 &middot; {termCount}
                  </span>
                  <Button type="primary" icon={<SaveOutlined />} loading={savingTerms}
                    onClick={handleSaveTerms} size="small">
                    保存术语表
                  </Button>
                </div>
                {termCount === 0 ? (
                  <div className="aids-empty">
                    <div className="aids-empty-icon">[ ]</div>
                    <div className="aids-empty-text">暂无术语，添加术语以辅助翻译该作品</div>
                  </div>
                ) : (
                  <div style={{ maxHeight: 480, overflowY: 'auto' }}>
                    {Object.entries(terms).map(function(entry) {
                      var key = entry[0], val = entry[1];
                      var isEditing = editingTerm === key;
                      return (
                        <div className="aids-term-row" key={key}>
                          {isEditing ? (
                            <span className="aids-term-pair" style={{ flex: 1 }}>
                              <span className="aids-term-key" style={{ cursor: 'default' }}>{key}</span>
                              <span className="aids-term-arrow">&rarr;</span>
                              <Input size="small" value={editTermVal}
                                onChange={function(e) { setEditTermVal(e.target.value); }}
                                onPressEnter={function() { commitEditTerm(key); }}
                                onBlur={function() { commitEditTerm(key); }}
                                onKeyDown={function(e) { if (e.key === 'Escape') setEditingTerm(null); }}
                                autoFocus
                                style={{ width: 160 }} />
                            </span>
                          ) : (
                            <span className="aids-term-pair" style={{ flex: 1, cursor: 'pointer' }}
                              onClick={function() { startEditTerm(key); }}
                              title="点击编辑中文翻译">
                              <span className="aids-term-key">{key}</span>
                              <span className="aids-term-arrow">&rarr;</span>
                              <span className="aids-term-val" style={{ borderBottom: '1px dashed var(--color-text-muted)' }}>{val}</span>
                            </span>
                          )}
                          <Button type="link" danger icon={<DeleteOutlined />}
                            onClick={function() { handleRemoveTerm(key); }}
                            size="small" style={{ opacity: 0.5, padding: '0 4px' }} />
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      ),
    },

    // ── Tab 2: 别名表 (ASR误识别参考) ──
    {
      key: 'alias',
      label: '别名表 (' + aliasCount + ')',
      children: (
        <div className="aids-tab-content">
          {!selectedWorkId ? (
            <div className="workspace-empty" style={{ padding: '40px 24px' }}>
              <div className="workspace-empty-title">请先选择作品</div>
              <div className="workspace-empty-desc">在上方下拉菜单中选择要编辑的 RJ 作品。</div>
            </div>
          ) : (
            <div className="aids-layout">
              <div className="aids-add-panel">
                <div className="aids-add-title">为 {selectedWorkId} 添加别名</div>
                <div className="aids-add-inputs">
                  <Input placeholder="ASR 误识别词（如 いく）" value={newAliasFrom}
                    onChange={function(e) { setNewAliasFrom(e.target.value); }}
                    onPressEnter={handleAddAlias} size="small" />
                  <Input placeholder="实际应为（如 行く）" value={newAliasTo}
                    onChange={function(e) { setNewAliasTo(e.target.value); }}
                    onPressEnter={handleAddAlias} size="small" />
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Text style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>置信度:</Text>
                    <Input placeholder="0.9" value={String(newAliasConf)}
                      onChange={function(e) { var v = parseFloat(e.target.value); if (!isNaN(v)) setNewAliasConf(v); }}
                      size="small" style={{ width: 80 }} />
                  </div>
                </div>
                <Button type="dashed" icon={<PlusOutlined />} onClick={handleAddAlias}
                  block size="small" disabled={!newAliasFrom.trim() || !newAliasTo.trim()}>
                  添加
                </Button>
                <div style={{ marginTop: 16 }}>
                  <Text style={{ fontSize: 11, color: 'var(--color-text-muted)', lineHeight: 1.6, display: 'block' }}>
                    别名表帮助修正日语音频中容易听错的词汇 &middot; 点击别名可编辑 &middot; 置信度越高越强制替换
                  </Text>
                </div>
              </div>
              <div className="aids-term-list">
                <div className="aids-term-list-header">
                  <span className="aids-term-count">
                    {selectedWorkId} 别名列表 &middot; {aliasCount}
                  </span>
                  <Button type="primary" icon={<SaveOutlined />} loading={savingAlias}
                    onClick={handleSaveAlias} size="small">
                    保存别名表
                  </Button>
                </div>
                {aliasCount === 0 ? (
                  <div className="aids-empty">
                    <div className="aids-empty-icon"><SoundOutlined /></div>
                    <div className="aids-empty-text">暂无别名，添加 ASR 误识别参考以提升翻译准确度</div>
                  </div>
                ) : (
                  <div style={{ maxHeight: 480, overflowY: 'auto' }}>
                    {aliasList.map(function(item, idx) {
                      var isEdit = editingAlias === idx;
                      return (
                        <div className="aids-alias-row" key={idx}>
                          {isEdit ? (
                            <span className="aids-alias-pair" style={{ flex: 1 }}>
                              <Input size="small" value={editAliasFrom}
                                onChange={function(e) { setEditAliasFrom(e.target.value); }}
                                onPressEnter={function() { commitEditAlias(idx); }}
                                onKeyDown={function(e) { if (e.key === 'Escape') setEditingAlias(null); }}
                                autoFocus style={{ width: 120 }} />
                              <span className="aids-term-arrow">&rarr;</span>
                              <Input size="small" value={editAliasTo}
                                onChange={function(e) { setEditAliasTo(e.target.value); }}
                                onPressEnter={function() { commitEditAlias(idx); }}
                                onBlur={function() { commitEditAlias(idx); }}
                                onKeyDown={function(e) { if (e.key === 'Escape') setEditingAlias(null); }}
                                style={{ width: 120 }} />
                            </span>
                          ) : (
                            <span className="aids-alias-pair" style={{ flex: 1, cursor: 'pointer' }}
                              onClick={function() { startEditAlias(idx); }}
                              title="点击编辑别名">
                              <span className="aids-term-key">{item.alias}</span>
                              <span className="aids-term-arrow">&rarr;</span>
                              <span className="aids-term-val" style={{ borderBottom: '1px dashed var(--color-text-muted)' }}>{item.target}</span>
                            </span>
                          )}
                          <span className="aids-alias-conf">
                            <Input
                              size="small"
                              value={String(item.confidence)}
                              onChange={function(e) { handleAliasConfChange(idx, e.target.value); }}
                              style={{ width: 64, textAlign: 'center', fontSize: 11 }}
                            />
                          </span>
                          <Button type="link" danger icon={<DeleteOutlined />}
                            onClick={function() { handleRemoveAlias(idx); }}
                            size="small" style={{ opacity: 0.5, padding: '0 4px' }} />
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      ),
    },

    // ── Tab 3: 世界观 ──
    {
      key: 'worldview',
      label: '世界观' + (worldviewText || charCount > 0 || sceneText || themes.length > 0 || Object.keys(specialTerms).length > 0 ? ' ●' : ''),
      children: (
        <div className="aids-tab-content">
          {!selectedWorkId ? (
            <div className="workspace-empty" style={{ padding: '40px 24px' }}>
              <div className="workspace-empty-title">请先选择作品</div>
              <div className="workspace-empty-desc">在上方下拉菜单中选择要编辑的 RJ 作品。</div>
            </div>
          ) : (
            <div className="aids-worldview-layout">
              {/* 世界观描述 */}
              <div className="aids-worldview-section">
                <div className="aids-section-label">
                  <GlobalOutlined style={{ marginRight: 6 }} />世界观描述
                </div>
                <TextArea
                  placeholder="输入作品的世界观设定、故事背景等..."
                  value={worldviewText}
                  onChange={function(e) { setWorldviewText(e.target.value); }}
                  rows={4}
                  style={{ background: 'var(--color-surface-1)', border: '1px solid var(--color-hairline)',
                    borderRadius: 'var(--radius-sm)', color: 'var(--color-text-primary)', fontSize: 13 }}
                />
              </div>

              {/* 场景 */}
              <div className="aids-worldview-section">
                <div className="aids-section-label">
                  <GlobalOutlined style={{ marginRight: 6 }} />场景 / 舞台设定
                </div>
                <TextArea
                  placeholder="输入场景设定（如：学校、异世界、未来都市...）"
                  value={sceneText}
                  onChange={function(e) { setSceneText(e.target.value); }}
                  rows={2}
                  style={{ background: 'var(--color-surface-1)', border: '1px solid var(--color-hairline)',
                    borderRadius: 'var(--radius-sm)', color: 'var(--color-text-primary)', fontSize: 13 }}
                />
              </div>

              {/* 主题 (themes) */}
              {themes.length > 0 && (
                <div className="aids-worldview-section">
                  <div className="aids-section-label" style={{ marginBottom: 8 }}>
                    主题标签 ({themes.length})
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {themes.map(function(t, i) {
                      return <Tag key={i} color="purple">{t}</Tag>;
                    })}
                  </div>
                </div>
              )}

              {/* 角色 */}
              <div className="aids-worldview-section">
                <div className="aids-section-label" style={{ marginBottom: 12 }}>
                  角色设定 ({charCount > 0 ? charCount : charArray.length})
                </div>

                {/* 复杂格式角色（只读展示） */}
                {charArray.length > 0 && (
                  <div className="aids-char-list" style={{ marginBottom: 12 }}>
                    {charArray.map(function(c, i) {
                      return (
                        <div className="aids-char-row" key={i}>
                          <span className="aids-char-name">{c.name}</span>
                          <span className="aids-char-desc">
                            {[c.role ? '角色: ' + c.role : '', c.personality ? '性格: ' + c.personality : '', c.description].filter(Boolean).join(' | ')}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* 添加/编辑简单格式角色 */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  <Input placeholder="角色名" value={newCharName}
                    onChange={function(e) { setNewCharName(e.target.value); }}
                    onPressEnter={handleAddCharacter} size="small" style={{ flex: 1 }} />
                  <Input placeholder="角色描述" value={newCharDesc}
                    onChange={function(e) { setNewCharDesc(e.target.value); }}
                    onPressEnter={handleAddCharacter} size="small" style={{ flex: 2 }} />
                  <Button type="dashed" icon={<PlusOutlined />} onClick={handleAddCharacter}
                    size="small" disabled={!newCharName.trim() || !newCharDesc.trim()}>
                    添加
                  </Button>
                </div>
                {charCount > 0 && (
                  <div className="aids-char-list">
                    {Object.entries(characters).map(function(entry) {
                      var name = entry[0], desc = entry[1];
                      return (
                        <div className="aids-char-row" key={name}>
                          <span className="aids-char-name">{name}</span>
                          <span className="aids-char-desc">{desc}</span>
                          <Button type="link" danger icon={<DeleteOutlined />}
                            onClick={function() { handleRemoveCharacter(name); }}
                            size="small" style={{ opacity: 0.5, padding: '0 4px', flexShrink: 0 }} />
                        </div>
                      );
                    })}
                  </div>
                )}
                {charCount === 0 && charArray.length === 0 && (
                  <div className="aids-empty" style={{ padding: '24px' }}>
                    <div className="aids-empty-text">暂无角色设定</div>
                  </div>
                )}
              </div>

              {/* 特殊术语 (special_terms) */}
              <div className="aids-worldview-section">
                <div className="aids-section-label" style={{ marginBottom: 12 }}>
                  特殊术语 ({Object.keys(specialTerms).length})
                </div>
                <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  <Input placeholder="术语" value={newSTKey}
                    onChange={function(e) { setNewSTKey(e.target.value); }}
                    size="small" style={{ flex: 1 }} />
                  <Input placeholder="含义" value={newSTVal}
                    onChange={function(e) { setNewSTVal(e.target.value); }}
                    size="small" style={{ flex: 2 }} />
                  <Button type="dashed" icon={<PlusOutlined />}
                    onClick={function() {
                      if (!newSTKey.trim() || !newSTVal.trim()) return;
                      setSpecialTerms(function(prev) {
                        var next: Record<string, string> = {};
                        Object.keys(prev).forEach(function(k) { next[k] = prev[k]; });
                        next[newSTKey.trim()] = newSTVal.trim();
                        return next;
                      });
                      setNewSTKey('');
                      setNewSTVal('');
                    }}
                    size="small" disabled={!newSTKey.trim() || !newSTVal.trim()}>
                    添加
                  </Button>
                </div>
                {Object.keys(specialTerms).length > 0 ? (
                  <div className="aids-char-list">
                    {Object.entries(specialTerms).map(function(entry) {
                      var key = entry[0], val = entry[1];
                      return (
                        <div className="aids-char-row" key={key}>
                          <span className="aids-char-name">{key}</span>
                          <span className="aids-char-desc">{val}</span>
                          <Button type="link" danger icon={<DeleteOutlined />}
                            onClick={function() {
                              setSpecialTerms(function(prev) {
                                var next: Record<string, string> = {};
                                Object.keys(prev).forEach(function(k) {
                                  if (k !== key) next[k] = prev[k];
                                });
                                return next;
                              });
                            }}
                            size="small" style={{ opacity: 0.5, padding: '0 4px', flexShrink: 0 }} />
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="aids-empty" style={{ padding: '24px' }}>
                    <div className="aids-empty-text">暂无特殊术语</div>
                  </div>
                )}
              </div>

              {/* Save */}
              <div style={{ marginTop: 16 }}>
                <Button type="primary" icon={<SaveOutlined />} loading={savingWorldview}
                  onClick={handleSaveWorldview}>
                  保存世界观
                </Button>
              </div>
            </div>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div className="page-header">
        <div>
          <div className="page-title">辅助翻译</div>
          <div className="page-subtitle">管理术语表、ASR 别名表和世界观设定，翻译时自动生效确保一致性</div>
        </div>
      </div>

      {/* Work Selector */}
      <div style={{ marginBottom: 20, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Text style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
          <SwapOutlined style={{ marginRight: 4 }} />选择作品：
        </Text>
        <Select
          value={selectedWorkId}
          onChange={function(v) { setSelectedWorkId(v); }}
          placeholder="选择要编辑的 RJ 作品"
          style={{ minWidth: 300 }}
          size="small"
          options={works.map(function(w) {
            return {
              value: w.id,
              label: w.id + (w.hasScriptbook ? ' [台本]' : '') + ' — ' + w.lrcFiles.length + ' 字幕',
            };
          })}
        />
      </div>

      {/* Tabs */}
      <Tabs
        activeKey={activeTab}
        onChange={function(k) { setActiveTab(k); }}
        items={tabItems}
        style={{ marginTop: -8 }}
      />
    </div>
  );
};

export default TranslationAids;
