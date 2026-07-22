import React, { useState, useEffect } from 'react';
import { Button, Form, Input, InputNumber, Select, Switch, Spin, Modal, message } from 'antd';
import { SaveOutlined, PlayCircleOutlined, ApiOutlined, SettingOutlined, ScanOutlined, DollarOutlined, GlobalOutlined, FileTextOutlined, SoundOutlined, PlusOutlined, DeleteOutlined, CopyOutlined } from '@ant-design/icons';
import { useAppStore } from '../stores/useAppStore';

// 合法的配置段名称，保存时只处理这些，防止 JSON 里的垃圾数据通过 flatten/unflatten 写回
const CONFIG_SECTIONS = ['api', 'app', 'ocr', 'pricing', 'network', 'prompts', 'transcription'];

// ---- 递归 flatten / unflatten 用于处理嵌套配置（如 api.generation_params.temperature） ----

function flatten(obj: Record<string, unknown>, prefix = ''): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (val !== null && typeof val === 'object' && !Array.isArray(val)) {
      Object.assign(result, flatten(val as Record<string, unknown>, fullKey));
    } else {
      result[fullKey] = val;
    }
  }
  return result;
}

function unflatten(flat: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(flat)) {
    const parts = key.split('.');
    let current = result;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!current[parts[i]] || typeof current[parts[i]] !== 'object') {
        current[parts[i]] = {};
      }
      current = current[parts[i]] as Record<string, unknown>;
    }
    current[parts[parts.length - 1]] = val;
  }
  return result;
}

const Settings: React.FC = () => {
  const { workDir, setActiveTab } = useAppStore();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [presets, setPresets] = useState<string[]>([]);
  const [activePreset, setActivePreset] = useState<string>('default');
  const [newPresetName, setNewPresetName] = useState('');

  const loadConfig = async () => {
    setLoadingConfig(true);
    try {
      const config = await (window as any).electronAPI.config.getAll();
      const flat = flatten(config as Record<string, unknown>);
      form.setFieldsValue(flat);
    } catch (e) {
      message.error('无法加载配置');
    } finally {
      setLoadingConfig(false);
    }
  };

  const loadPresets = async () => {
    try {
      const res = await (window as any).electronAPI.config.listPresets();
      setPresets(res.presets || ['default']);
      setActivePreset(res.active || 'default');
    } catch {}
  };

  useEffect(() => {
    (async () => {
      await loadPresets();
      await loadConfig();
    })();
  }, []);

  const handleSwitchPreset = async (name: string) => {
    const res = await (window as any).electronAPI.config.setActivePreset(name);
    if (res.success) {
      setActivePreset(name);
      await loadConfig();
      message.success('已切换到预设: ' + name);
    } else {
      message.error('预设不存在');
    }
  };

  const handleSavePreset = async () => {
    const name = newPresetName.trim();
    if (!name) { message.warning('请输入预设名称'); return; }
    // 先保存当前表单到 config
    const values = form.getFieldsValue();
    const nested = unflatten(values);
    for (const section of CONFIG_SECTIONS) {
      const data = nested[section];
      if (typeof data === 'object' && data !== null) {
        // 清除 undefined 值，避免未触摸的复选框缺失字段覆盖已有配置
        const clean: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
          if (v !== undefined) clean[k] = v;
        }
        await (window as any).electronAPI.config.setSection(section, clean);
      }
    }
    const res = await (window as any).electronAPI.config.savePreset(name);
    if (res.success) {
      setNewPresetName('');
      await loadPresets();
      setActivePreset(name);
      message.success('预设已保存: ' + name);
    } else {
      message.error('保存预设失败');
    }
  };

  const handleDeletePreset = async (name: string) => {
    if (name === 'default') { message.warning('不能删除默认预设'); return; }
    Modal.confirm({
      title: '删除预设',
      content: `确定要删除预设「${name}」吗？配置数据不会丢失，切回 default 即可恢复。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        const res = await (window as any).electronAPI.config.deletePreset(name);
        if (res.success) {
          await loadPresets();
          setActivePreset('default');
          await loadConfig();
          message.success('已删除预设: ' + name);
        }
      },
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const values = form.getFieldsValue();
      const nested = unflatten(values);
      for (const section of CONFIG_SECTIONS) {
        const data = nested[section];
        if (typeof data === 'object' && data !== null) {
          // 清除 undefined 值，避免未触摸的复选框缺失字段覆盖已有配置
          const clean: Record<string, unknown> = {};
          for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
            if (v !== undefined) clean[k] = v;
          }
          await (window as any).electronAPI.config.setSection(section, clean);
        }
      }
      message.success('配置已保存到 config.json');
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleStartTranslate = async () => {
    if (!workDir) return;
    await handleSave();
    useAppStore.getState().setTranslating(true);
    setActiveTab('monitor');
    await (window as any).electronAPI.translate.start(workDir);
  };

  if (!workDir) {
    return (
      <div className="workspace-empty animate-fade-in">
        <div className="workspace-empty-icon"><SettingOutlined /></div>
        <div className="workspace-empty-title">未选择工作目录</div>
        <div className="workspace-empty-desc">请先在「工作区」选择工作目录后再配置翻译参数。</div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div className="page-header">
        <div>
          <div className="page-title">翻译配置</div>
          <div className="page-subtitle">配置 API、OCR 及生成参数</div>
        </div>
      </div>

      {/* ── 预设管理栏 ── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20,
        padding: '12px 16px', background: 'var(--color-surface-2)',
        border: '1px solid var(--color-hairline)', borderRadius: 'var(--radius-md)',
      }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-secondary)', whiteSpace: 'nowrap' }}>
          翻译预设
        </span>
        <Select
          value={activePreset}
          onChange={handleSwitchPreset}
          size="small"
          style={{ minWidth: 160 }}
          options={presets.map(p => ({ value: p, label: p === 'default' ? '📋 默认配置' : '💾 ' + p }))}
        />
        <div style={{ display: 'flex', gap: 6 }}>
          <Input
            placeholder="新预设名称"
            value={newPresetName}
            onChange={e => setNewPresetName(e.target.value)}
            size="small"
            style={{ width: 140 }}
            onPressEnter={handleSavePreset}
          />
          <Button
            type="dashed"
            icon={<PlusOutlined />}
            onClick={handleSavePreset}
            size="small"
            disabled={!newPresetName.trim()}
          >
            保存为预设
          </Button>
          {activePreset !== 'default' && (
            <Button
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDeletePreset(activePreset)}
              size="small"
            >
              删除此预设
            </Button>
          )}
        </div>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
          预设可保存 API 密钥、模型、生成参数等全部配置，一键切换
        </span>
      </div>

      <Form form={form} layout="vertical" style={{ maxWidth: 900 }}>
        <Spin spinning={loadingConfig} tip="加载配置中...">
        <div className="settings-grid">

          {/* ---- API ---- */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="settings-section-icon"><ApiOutlined /></span>
              <span className="settings-section-title">API 设置</span>
            </div>
            <Form.Item name="api.key" label="API Key">
              <Input.Password placeholder="sk-..." size="small" />
            </Form.Item>
            <Form.Item name="api.base_url" label="API Base URL">
              <Input placeholder="https://api.deepseek.com" size="small" />
            </Form.Item>
            <Form.Item name="api.model" label="模型名称">
              <Input placeholder="例: deepseek-chat / gpt-4o / claude-opus-4-8" size="small" />
            </Form.Item>
            <Form.Item name="api.timeout" label="请求超时 (ms)">
              <InputNumber min={500} max={60000} step={500} size="small" style={{ width: '100%' }} />
            </Form.Item>
          </div>

          {/* ---- Generation ---- */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="settings-section-icon"><SettingOutlined /></span>
              <span className="settings-section-title">生成参数</span>
            </div>
            <div className="settings-form-row">
              <Form.Item name="api.generation_params.temperature" label="Temperature">
                <InputNumber min={0} max={2} step={0.1} size="small" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="api.generation_params.top_p" label="Top P">
                <InputNumber min={0} max={1} step={0.05} size="small" style={{ width: '100%' }} />
              </Form.Item>
            </div>
            <div className="settings-form-row">
              <Form.Item name="api.generation_params.max_tokens" label="最大 Token">
                <InputNumber min={1024} max={262144} step={1024} size="small" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="api.generation_params.reasoning_effort" label="推理力度">
                <Select size="small" options={[
                  { value: 'low', label: '低' },
                  { value: 'medium', label: '中' },
                  { value: 'high', label: '高' },
                ]} />
              </Form.Item>
            </div>
          </div>

          {/* ---- App Settings ---- */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="settings-section-icon"><FileTextOutlined /></span>
              <span className="settings-section-title">应用设置</span>
            </div>
            <div className="settings-form-row">
              <Form.Item name="app.lrc_max_lines_per_request" label="每次请求最大行数">
                <InputNumber min={10} max={500} size="small" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="app.retry_count" label="失败重试次数">
                <InputNumber min={0} max={10} size="small" style={{ width: '100%' }} />
              </Form.Item>
            </div>
            <div className="settings-form-row">
              <Form.Item name="app.delay_between_requests" label="请求间隔 (秒)">
                <InputNumber min={0} max={10} step={0.1} size="small" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="app.translation_mode" label="翻译模式">
                <Select size="small" options={[
                  { value: 'per_track', label: '按音轨' },
                  { value: 'batch', label: '批量' },
                ]} />
              </Form.Item>
            </div>
            <div className="settings-form-row">
              <Form.Item name="app.scriptbook_mode" label="台本模式">
                <Select size="small" options={[
                  { value: 'full', label: '完整台本（推荐）' },
                  { value: 'keyword', label: '仅关键台词' },
                ]} />
              </Form.Item>
              <Form.Item name="app.debug" label="调试模式" valuePropName="checked">
                <Switch size="small" />
              </Form.Item>
            </div>
            <div className="settings-form-row">
              <Form.Item name="app.use_scriptbook_for_translation" label="台本辅助翻译" valuePropName="checked">
                <Switch size="small" />
              </Form.Item>
              <Form.Item name="app.export_ja_lrc" label="导出日文 LRC" valuePropName="checked">
                <Switch size="small" />
              </Form.Item>
            </div>
            <Form.Item name="app.export_scriptbook_content" label="导出台本内容" valuePropName="checked">
              <Switch size="small" />
            </Form.Item>
          </div>

          {/* ---- OCR ---- */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="settings-section-icon"><ScanOutlined /></span>
              <span className="settings-section-title">OCR 设置</span>
            </div>
            <div className="settings-form-row">
              <Form.Item name="ocr.dpi" label="DPI">
                <InputNumber min={72} max={600} step={10} size="small" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="ocr.x_tolerance" label="X 容差">
                <InputNumber min={5} max={100} size="small" style={{ width: '100%' }} />
              </Form.Item>
            </div>
            <div className="settings-form-row">
              <Form.Item name="ocr.use_gpu" label="GPU 加速" valuePropName="checked">
                <Switch size="small" />
              </Form.Item>
              <Form.Item name="ocr.vertical_ratio_threshold" label="垂直比率阈值">
                <InputNumber min={0.5} max={5} step={0.1} size="small" style={{ width: '100%' }} />
              </Form.Item>
            </div>
            <div className="settings-form-row">
              <Form.Item name="ocr.vertical_block_ratio" label="垂直块比率">
                <InputNumber min={0} max={1} step={0.05} size="small" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="ocr.text_det_thresh" label="文本检测阈值">
                <InputNumber min={0.1} max={0.9} step={0.05} size="small" style={{ width: '100%' }} />
              </Form.Item>
            </div>
            <div className="settings-form-row">
              <Form.Item name="ocr.text_det_box_thresh" label="文本框阈值">
                <InputNumber min={0.1} max={0.9} step={0.05} size="small" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="ocr.text_recognition_batch_size" label="识别批次大小">
                <InputNumber min={1} max={32} size="small" style={{ width: '100%' }} />
              </Form.Item>
            </div>
            <Form.Item name="ocr.adaptive_threshold_block_size" label="自适应阈值块大小">
              <InputNumber min={5} max={99} step={2} size="small" style={{ width: '100%' }} />
            </Form.Item>
          </div>

          {/* ---- Pricing ---- */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="settings-section-icon"><DollarOutlined /></span>
              <span className="settings-section-title">计价设置</span>
            </div>
            <div className="settings-form-row-3">
              <Form.Item name="pricing.hit_per_1m" label="缓存命中 ($/1M)">
                <InputNumber min={0} step={0.01} size="small" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="pricing.miss_per_1m" label="缓存未命中 ($/1M)">
                <InputNumber min={0} step={0.01} size="small" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="pricing.completion_per_1m" label="生成 ($/1M)">
                <InputNumber min={0} step={0.01} size="small" style={{ width: '100%' }} />
              </Form.Item>
            </div>
          </div>

          {/* ---- Transcription ---- */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="settings-section-icon"><SoundOutlined /></span>
              <span className="settings-section-title">转录模型</span>
            </div>
            <Form.Item name="transcription.infer_exe" label="infer.exe 路径"
              extra="Whisper 转录引擎，留空则不启用转录功能">
              <Input placeholder="例: E:\转录模型\infer.exe" size="small" />
            </Form.Item>
            <Form.Item name="transcription.model_dir" label="模型目录"
              extra="转录模型和 PaddleOCR 模型所在目录">
              <Input placeholder="例: E:\转录模型\models" size="small" />
            </Form.Item>
            <div className="settings-form-row">
              <Form.Item name="transcription.device" label="推理设备">
                <Select size="small" options={[
                  { value: 'cuda', label: 'GPU (CUDA)' },
                  { value: 'cpu', label: 'CPU' },
                ]} />
              </Form.Item>
              <Form.Item name="transcription.compute_type" label="计算精度">
                <Select size="small" options={[
                  { value: 'int8_float16', label: 'int8_float16 (推荐)' },
                  { value: 'float16', label: 'float16' },
                  { value: 'float32', label: 'float32' },
                  { value: 'int8', label: 'int8' },
                ]} />
              </Form.Item>
            </div>
          </div>

          {/* ---- Network ---- */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="settings-section-icon"><GlobalOutlined /></span>
              <span className="settings-section-title">网络 & 提示词</span>
            </div>
            <Form.Item name="network.clear_proxy_on_startup" label="启动时清除代理" valuePropName="checked">
              <Switch size="small" />
            </Form.Item>
            <Form.Item name="prompts.system_prompt_file" label="系统提示词文件路径">
              <Input placeholder="留空使用默认 system_prompt.txt" size="small" />
            </Form.Item>
          </div>

        </div>
        </Spin>
      </Form>

      {/* Actions */}
      <div className="settings-actions">
        <Button type="primary" icon={<SaveOutlined />} onClick={handleSave} loading={saving}>
          保存配置
        </Button>
        <Button
          style={{
            background: 'var(--color-accent)',
            borderColor: 'var(--color-accent)',
            color: '#fff',
            fontWeight: 600,
          }}
          icon={<PlayCircleOutlined />}
          onClick={handleStartTranslate}
        >
          开始翻译
        </Button>
      </div>
    </div>
  );
};

export default Settings;