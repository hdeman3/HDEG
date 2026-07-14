import React, { useState, useEffect } from 'react';
import { Button, Form, Input, InputNumber, Select, Switch, Spin, message } from 'antd';
import { SaveOutlined, PlayCircleOutlined, ApiOutlined, SettingOutlined, ScanOutlined, DollarOutlined, GlobalOutlined, FileTextOutlined } from '@ant-design/icons';
import { useAppStore } from '../stores/useAppStore';

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

  useEffect(() => {
    (async () => {
      try {
        setLoadingConfig(true);
        const config = await (window as any).electronAPI.config.getAll();
        const flat = flatten(config as Record<string, unknown>);
        form.setFieldsValue(flat);
      } catch (e) {
        message.error('无法加载配置，请确认后端服务已启动');
      } finally {
        setLoadingConfig(false);
      }
    })();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const values = form.getFieldsValue();
      const nested = unflatten(values);
  for (const [section, data] of Object.entries(nested)) {
    if (typeof data === 'object' && data !== null) {
      await (window as any).electronAPI.config.setSection(section, data as Record<string, unknown>);
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
                <InputNumber min={100} max={4096} step={100} size="small" style={{ width: '100%' }} />
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
                  { value: 'full', label: '完整' },
                  { value: 'per_chapter', label: '按章节' },
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