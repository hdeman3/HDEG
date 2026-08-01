#!/usr/bin/env node
/**
 * 转译 Dev Server — 在浏览器开发模式下替代 Electron IPC 的 HTTP 后端
 *
 * 用法: node dev-server.js [port]
 * 默认端口: 5199
 */

var http = require('http');
var fs = require('fs');
var path = require('path');
var spawn = require('child_process').spawn;
var url = require('url');

var PORT = parseInt(process.argv[2]) || 5199;
// 自动检测项目根目录：
// 1. pkg 编译模式：process.pkg 为 true，用 exe 所在目录
// 2. 发布包目录（含 main.exe）：用 __dirname
// 3. 开发模式（electron-app 内）：向上一级到项目根
var PROJECT_ROOT = (typeof process.pkg !== 'undefined')
  ? path.dirname(process.execPath)
  : fs.existsSync(path.join(__dirname, 'main.exe'))
    ? __dirname
    : path.resolve(__dirname, '..');
// main.exe 统一放在 HDEG/Hde_G_release/ 下
var MAIN_EXE_DIR = path.join(PROJECT_ROOT, 'Hde_G_release');
var MAIN_EXE = path.join(MAIN_EXE_DIR, 'main.exe');
var USE_MAIN_EXE = fs.existsSync(MAIN_EXE);
var CONFIG_PATH = path.join(PROJECT_ROOT, 'config.json');
var PRESETS_PATH = path.join(PROJECT_ROOT, 'presets.json');
var CONFIG_SECTIONS = ['api', 'app', 'ocr', 'pricing', 'network', 'prompts', 'transcription'];
var RENDERER_DIR = (typeof process.pkg !== 'undefined')
  ? path.join(path.dirname(process.execPath), 'dist', 'renderer')
  : path.join(__dirname, 'dist', 'renderer');
var IS_PRODUCTION = fs.existsSync(path.join(RENDERER_DIR, 'index.html'));

// 迁移旧数据：config.json 里的 presets 拆到独立文件
(function migratePresets() {
  if (fs.existsSync(PRESETS_PATH)) return;
  var oldCfg = readJSON(CONFIG_PATH);
  if (oldCfg && oldCfg.presets) {
    writeJSON(PRESETS_PATH, oldCfg.presets);
    delete oldCfg.presets;
    writeJSON(CONFIG_PATH, oldCfg);
    console.log('[dev-server] 已将 presets 从 config.json 迁移到 presets.json');
  }
})();

// ---- Python 环境：强制 UTF-8 I/O，避免 Windows GBK 编码日文字符出错 ----
var PYTHON_ENV = Object.assign({}, process.env, {
  PYTHONUNBUFFERED: '1',
  PYTHONIOENCODING: 'utf-8',
  PYTHONUTF8: '1',
});

// ---- SSE 客户端管理 ----
var sseClients = new Set();

function broadcastSSE(event, data) {
  var msg = 'event: ' + event + '\ndata: ' + JSON.stringify(data) + '\n\n';
  sseClients.forEach(function(res) {
    try { res.write(msg); } catch(e) {}
  });
}

// ---- 工具函数 ----
function readJSON(filePath) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf-8')); }
  catch(e) { return null; }
}

function writeJSON(filePath, data) {
  var dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf-8');
}

function readConfig() {
  return readJSON(CONFIG_PATH) || {};
}

function readPresets() {
  return readJSON(PRESETS_PATH) || {};
}

function writePresets(presets) {
  writeJSON(PRESETS_PATH, presets);
}

function writeConfig(section, data) {
  var config = readConfig();
  // 清除 undefined 值
  var clean = {};
  for (var k in data) {
    if (data.hasOwnProperty(k) && data[k] !== undefined) clean[k] = data[k];
  }
  config[section] = Object.assign({}, config[section] || {}, clean);
  // config.json 只保存配置段 + active_preset，不保存 presets
  var toSave = {};
  for (var _si = 0; _si < CONFIG_SECTIONS.length; _si++) {
    var _sk = CONFIG_SECTIONS[_si];
    if (config[_sk] !== undefined) toSave[_sk] = config[_sk];
  }
  if (config.active_preset !== undefined) toSave.active_preset = config.active_preset;
  writeJSON(CONFIG_PATH, toSave);
  return config;
}

function findRJWorks(dirPath) {
  var audioExts = ['.mp3','.wav','.flac','.m4a','.aac','.ogg','.wma','.mp4','.mkv','.avi','.mov','.webm'];
  var lrcExts = ['.lrc','.srt','.vtt'];
  var works = [];

  try {
    var entries = fs.readdirSync(dirPath, { withFileTypes: true });
    entries.forEach(function(entry) {
      if (!entry.isDirectory() || !entry.name.startsWith('RJ')) return;
      var workPath = path.join(dirPath, entry.name);
      var workId = entry.name;
      var lrcFiles = [];
      var audioFiles = [];

      function scanDir(currentPath) {
        try {
          var subs = fs.readdirSync(currentPath, { withFileTypes: true });
          subs.forEach(function(sub) {
            var fullPath = path.join(currentPath, sub.name);
            if (sub.isDirectory()) {
              scanDir(fullPath);
            } else {
              var ext = path.extname(sub.name).toLowerCase();
              if (lrcExts.indexOf(ext) >= 0) {
                // 跳过带语言标记的文件（.ja.lrc / .cn.lrc），只处理基础字幕文件
                var isLangTagged = /\.([a-z]{2})\.(lrc|srt|vtt)$/i.test(sub.name);
                if (isLangTagged) return;

                var baseName = sub.name.replace(new RegExp(ext.replace('.','\\.') + '$'), '');
                var jaLrcPath = path.join(currentPath, baseName + '.ja.lrc');
                var hasJaLrc = fs.existsSync(jaLrcPath);
                var lrcSize = fs.statSync(fullPath).size;

                // 后端判断状态：读取 LRC 内容检测是否含中文翻译
                var hasTranslation = false;
                if (lrcSize > 10) {
                  try {
                    var content = fs.readFileSync(fullPath, 'utf-8').slice(0, 4096);
                    // 检测是否含中文（CJK统一表意文字）或翻译分隔符 ／
                    hasTranslation = /[一-鿿㐀-䶿]|[／]/.test(content);
                  } catch(e) {}
                }

                lrcFiles.push({
                  name: path.basename(sub.name),
                  path: fullPath,
                  hasJaLrc: hasJaLrc,
                  hasCnLrc: hasTranslation, // 实际检测到中文=已翻译
                  size: lrcSize,
                });
              } else if (audioExts.indexOf(ext) >= 0) {
                audioFiles.push(sub.name);
              }
            }
          });
        } catch(e) {}
      }

      scanDir(workPath);

      // 检测台本文件 (.txt / .pdf)，使用正则匹配常见台本命名
      var hasScriptbook = false;
      var scriptbookPattern = /(台本|シナリオ|script|台詞|セリフ|せりふ|原作|テキスト|筋書き|脚本|戯曲)/i;
      try {
        var allFiles = fs.readdirSync(workPath, { recursive: true });
        hasScriptbook = allFiles.some(function(f) {
          if (typeof f !== 'string') return false;
          var ext = require('path').extname(f).toLowerCase();
          if (ext !== '.txt' && ext !== '.pdf') return false;
          return scriptbookPattern.test(f);
        });
      } catch(e) {}

      works.push({ id: workId, path: workPath, lrcFiles: lrcFiles, audioFiles: audioFiles, totalFiles: lrcFiles.length + audioFiles.length, hasScriptbook: hasScriptbook });
    });
  } catch(e) {
    console.error('scan error:', e.message);
  }
  return works;
}

// ---- Python 进程管理 ----
var pythonProcess = null;

// 翻译状态缓存（浏览器重连时恢复）
var translateState = {
  running: false,
  workDir: null,
  startTime: null,
  stage: '',
  percent: 0,
  message: ''
};
var recentLogs = [];  // 最近 200 条日志

if (USE_MAIN_EXE) {
  console.log('[转译 Server] 检测到 main.exe，将使用打包后的 Python 后端');
}

function killPython() {
  if (pythonProcess) {
    // Windows: 杀整个进程树（含 infer.exe 等子进程）
    try {
      var pid = pythonProcess.pid;
      require('child_process').execSync('taskkill /F /T /PID ' + pid, { stdio: 'ignore' });
    } catch(e) {
      // taskkill 失败时回退到普通 kill
      try { pythonProcess.kill(); } catch(e2) {}
    }
    pythonProcess = null;
  }
  translateState.running = false;
}

function addLog(level, message) {
  recentLogs.push({ level: level, message: message, time: new Date().toLocaleTimeString() });
  if (recentLogs.length > 200) recentLogs.shift();
}

function getPythonCmd(args) {
  if (USE_MAIN_EXE) {
    return { exe: MAIN_EXE, args: args };
  }
  return { exe: 'python', args: ['-u'].concat(args) };
}

function runPython(args) {
  return new Promise(function(resolve, reject) {
    var cmd = getPythonCmd(args);
    var proc = spawn(cmd.exe, cmd.args, { cwd: PROJECT_ROOT, env: PYTHON_ENV });
    var stdout = '', stderr = '';
    proc.stdout.on('data', function(d) { stdout += d.toString('utf-8'); });
    proc.stderr.on('data', function(d) { stderr += d.toString('utf-8'); });
    proc.on('close', function(code) {
      if (code === 0) resolve(stdout);
      else reject(new Error('exit ' + code + ': ' + stderr));
    });
    proc.on('error', reject);
  });
}

// ---- HTTP 路由 ----
function sendJSON(res, data, status) {
  status = status || 200;
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  });
  res.end(JSON.stringify(data));
}

function parseBody(req) {
  return new Promise(function(resolve) {
    var chunks = [];
    req.on('data', function(chunk) { chunks.push(chunk); });
    req.on('end', function() {
      try {
        var body = Buffer.concat(chunks).toString('utf-8');
        resolve(JSON.parse(body));
      } catch(e) { resolve({}); }
    });
  });
}

// ---- 构建临时 Python 脚本（避免模板字符串中的转义问题） ----
function buildFetchScript(projectRoot, workDir) {
  return [
    "import sys, json, re",
    "sys.path.insert(0, " + JSON.stringify(projectRoot) + ")",
    "sys.stdout.reconfigure(encoding='utf-8')",
    "from pathlib import Path",
    "from io_adapter.lrc_handler import parse_lrc_file",
    "",
    "work_dir = Path(" + JSON.stringify(workDir) + ")",
    "results = []",
    "",
    "# 递归查找 .lrc，排除 .ja.lrc / .cn.lrc 等语言标记文件",
    "all_lrc = sorted(work_dir.glob('**/*.lrc'))",
    "base_lrc = [f for f in all_lrc if not re.search(r'\\.[a-z]{2}\\.lrc$', f.name)]",
    "",
    "for lrc_file in base_lrc:",
    "    ja_file = lrc_file.with_name(lrc_file.stem + '.ja.lrc')",
    "    ja_lines = parse_lrc_file(ja_file) if ja_file.exists() else []",
    "    cn_lines = parse_lrc_file(lrc_file)",
    "    for i, cn_line in enumerate(cn_lines):",
    "        ja_text = ja_lines[i].text if i < len(ja_lines) else ''",
    "        results.append({",
    "            'index': i + 1,",
    "            'timestamp': cn_line.timestamp_str,",
    "            'original': ja_text,",
    "            'translation': cn_line.text,",
    "            'filename': str(lrc_file.relative_to(work_dir))",
    "        })",
    "",
    "print(json.dumps(results, ensure_ascii=False))",
  ].join('\n');
}

function buildSaveScript(projectRoot, workDir, filename, index, newTranslation) {
  return [
    "import sys",
    "sys.path.insert(0, " + JSON.stringify(projectRoot) + ")",
    "sys.stdout.reconfigure(encoding='utf-8')",
    "from pathlib import Path",
    "from io_adapter.lrc_handler import parse_lrc_file, save_lrc",
    "",
    "work_dir = Path(" + JSON.stringify(workDir) + ")",
    "lrc_path = work_dir / " + JSON.stringify(filename),
    "lrc_lines = parse_lrc_file(lrc_path)",
    "translations = []",
    "for line in lrc_lines:",
    "    text = line.text",
    "    if '／' in text:",
    "        translations.append(text.split('／')[-1])",
    "    else:",
    "        translations.append(text)",
    "",
    "if 0 <= " + String(index - 1) + " < len(translations):",
    "    translations[" + String(index - 1) + "] = " + JSON.stringify(newTranslation),
    "save_lrc(lrc_path, lrc_lines, translations)",
    "print('ok')",
  ].join('\n');
}

var server = http.createServer(async function(req, res) {
  // CORS preflight
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    return res.end();
  }

  var parsed = url.parse(req.url, true);
  var pathname = parsed.pathname;
  var query = parsed.query;

  try {
    // ---- SSE 事件流 ----
    if (pathname === '/api/events') {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*',
      });
      // 发送当前状态（浏览器重连恢复）
      var initPayload = JSON.stringify({
        type: 'init',
        running: translateState.running,
        workDir: translateState.workDir,
        stage: translateState.stage,
        percent: translateState.percent,
        message: translateState.message,
        logs: recentLogs.slice(-100),
      });
      res.write('event: init\ndata: ' + initPayload + '\n\n');
      res.write('event: connected\ndata: {}\n\n');
      sseClients.add(res);
      req.on('close', function() { sseClients.delete(res); });
      return;
    }

    // ---- 配置 ----
    if (pathname === '/api/config' && req.method === 'GET') {
      return sendJSON(res, readConfig());
    }
    // /api/config/<section> — 仅一层路径，避免拦截 /api/config/presets/* 等子路由
    if (/^\/api\/config\/[^/]+$/.test(pathname) && req.method === 'POST') {
      var section = pathname.replace('/api/config/', '');
      var _data = await parseBody(req);
      var _config = writeConfig(section, _data);
      return sendJSON(res, { success: true, config: _config });
    }

    // ---- 路径解析（浏览器拖放时自动查找完整路径） ----
    if (pathname === '/api/resolve-path' && req.method === 'POST') {
      var _body0 = await parseBody(req);
      var folderName = _body0.folderName;
      var basePath = _body0.basePath || '';
      if (!folderName) return sendJSON(res, { found: false });

      var candidates = [];
      // 1. 如果提供了 basePath，拼接尝试
      if (basePath) {
        candidates.push(path.join(basePath, folderName));
        // 也尝试 basePath 本身（如果拖入的就是目标文件夹）
        if (basePath.endsWith(folderName) || basePath.split(/[\\/]/).pop() === folderName) {
          candidates.push(basePath);
        }
      }
      // 2. 从 input_path.txt 读取上次使用的工作目录
      try {
        var prevPath = fs.readFileSync(path.join(PROJECT_ROOT, 'input_path.txt'), 'utf-8').trim();
        if (prevPath) candidates.push(path.join(prevPath, folderName));
      } catch(e) {}
      // 3. 如果拖入的名字本身就是一个完整路径，直接尝试
      if (/^[A-Za-z]:[\\/]/.test(folderName)) candidates.push(folderName);

      var foundPath = '';
      for (var ci = 0; ci < candidates.length; ci++) {
        try {
          if (fs.existsSync(candidates[ci]) && fs.statSync(candidates[ci]).isDirectory()) {
            foundPath = candidates[ci];
            break;
          }
        } catch(e) {}
      }

      return sendJSON(res, { found: !!foundPath, path: foundPath || '', tried: candidates.slice(0, 5) });
    }

    // ---- 文件夹扫描 ----
    if (pathname === '/api/scan-works' && req.method === 'POST') {
      var _body1 = await parseBody(req);
      var _works = findRJWorks(_body1.dirPath);
      return sendJSON(res, { works: _works });
    }

    // ---- 辅助翻译: 公共搜索函数 ----
    // 搜索子目录: Python pipeline 可能把文件保存在子目录（如 RJ/SEあり/）
    function findAndReadAidFile(dir, name) {
      // 递归收集所有子目录（最多 3 层）
      var searchDirs = [dir];
      (function collectDirs(d, depth) {
        if (depth > 3) return;
        try {
          var subEntries = fs.readdirSync(d, { withFileTypes: true });
          subEntries.forEach(function(e) {
            if (e.isDirectory()) {
              var fullPath = path.join(d, e.name);
              searchDirs.push(fullPath);
              collectDirs(fullPath, depth + 1);
            }
          });
        } catch(e) {
          console.log('[dev-server] readdir error for', d, ':', e.message);
        }
      })(dir, 1);

      // 反转搜索顺序：从最深子目录开始搜索，优先找到离字幕文件最近的数据
      // 避免父目录的空 {} 文件抢先匹配而遮盖子目录中的真实数据
      searchDirs.reverse();

      var candidates = ['.' + name + '.json', name + '.json'];
      console.log('[dev-server] 搜索 ' + name + ' 文件, 根目录: ' + dir + ', 搜索 ' + searchDirs.length + ' 个目录');
      for (var si = 0; si < searchDirs.length; si++) {
        for (var ci = 0; ci < candidates.length; ci++) {
          var filePath = path.join(searchDirs[si], candidates[ci]);
          var result = readJSON(filePath);
          if (result !== null) {
            // 跳过空数据：空对象 {} 或空数组 []
            var isEmpty = (Array.isArray(result) && result.length === 0) ||
              (!Array.isArray(result) && typeof result === 'object' && result !== null && Object.keys(result).length === 0);
            if (isEmpty) {
              console.log('[dev-server] 跳过空的 ' + name + ' 文件: ' + filePath);
              continue;
            }
            console.log('[dev-server] 找到 ' + name + ': ' + filePath);
            return { data: result, foundDir: searchDirs[si] };
          }
        }
      }
      console.log('[dev-server] 未找到 ' + name + ' 文件');
      return null;
    }

    // ---- 辅助翻译: GET ----
    if (pathname === '/api/aids' && req.method === 'GET') {
      var aidsWorkDir = query.workDir;
      if (!aidsWorkDir) return sendJSON(res, { error: 'workDir required' }, 400);

      console.log('[dev-server] aids GET workDir:', aidsWorkDir);

      var termsResult = findAndReadAidFile(aidsWorkDir, 'terms');
      var aliasResult = findAndReadAidFile(aidsWorkDir, 'alias');
      var worldviewResult = findAndReadAidFile(aidsWorkDir, 'worldview');

      var termsData = termsResult ? termsResult.data : {};
      var aliasData = aliasResult ? aliasResult.data : [];
      var worldviewData = worldviewResult ? worldviewResult.data : {};

      var _termKeys = Object.keys(termsData);
      console.log('[dev-server] aids read: terms=' + _termKeys.length +
        ' (前5: ' + _termKeys.slice(0, 5).join(', ') + ')' +
        ', alias=' + (Array.isArray(aliasData) ? aliasData.length : 0) +
        ', worldview keys=' + (Object.keys(worldviewData).join(',') || '(空)'));

      return sendJSON(res, { terms: termsData, alias: aliasData, worldview: worldviewData });
    }

    // ---- 辅助翻译: POST save ----
    if (pathname === '/api/aids/save' && req.method === 'POST') {
      var _body2 = await parseBody(req);
      if (!_body2.workDir || !_body2.type) return sendJSON(res, { error: 'workDir and type required' }, 400);

      // 查找现有文件所在的目录，保存到相同位置
      var existing = findAndReadAidFile(_body2.workDir, _body2.type);
      var targetDir = existing ? existing.foundDir : _body2.workDir;

      writeJSON(path.join(targetDir, '.' + _body2.type + '.json'), _body2.data);
      console.log('[dev-server] aids save: ' + _body2.type + ' → ' + path.join(targetDir, '.' + _body2.type + '.json'));
      return sendJSON(res, { success: true });
    }

    // ---- 翻译 ----
    if (pathname === '/api/translate/start' && req.method === 'POST') {
      var _body3 = await parseBody(req);
      if (!_body3.workDir) return sendJSON(res, { error: 'workDir required' }, 400);

      killPython();
      recentLogs = [];
      translateState = {
        running: true,
        workDir: _body3.workDir,
        startTime: new Date().toISOString(),
        stage: '启动中',
        percent: 0,
        message: ''
      };

      if (USE_MAIN_EXE) {
        pythonProcess = spawn(MAIN_EXE, [_body3.workDir], {
          cwd: PROJECT_ROOT,
          env: PYTHON_ENV,
          windowsHide: false,
        });
      } else {
        fs.writeFileSync(path.join(PROJECT_ROOT, 'input_path.txt'), _body3.workDir, 'utf-8');
        pythonProcess = spawn('python', ['-u', path.join(PROJECT_ROOT, 'main.py')], {
          cwd: PROJECT_ROOT,
          env: PYTHON_ENV,
          windowsHide: false,
        });
      }

      pythonProcess.stdout.on('data', function(data) {
        var text = data.toString('utf-8');
        var lines = text.split('\n').filter(function(l) { return l.trim(); });
        lines.forEach(function(line) {
          try {
            var parsed = JSON.parse(line.trim());
            if (parsed.type) {
              broadcastSSE('python:message', parsed);
            }
          } catch(e) {
            addLog('info', line.trim());
            broadcastSSE('python:log', { level: 'info', message: line.trim() });
          }
        });
      });

      pythonProcess.stderr.on('data', function(data) {
        var msg = data.toString('utf-8').trim();
        if (msg) {
          addLog('error', msg);
          broadcastSSE('python:log', { level: 'error', message: msg });
        }
      });

      pythonProcess.on('close', function(code) {
        translateState.running = false;
        translateState.stage = code === 0 ? '完成' : '异常';
        broadcastSSE('python:done', { exitCode: code });
        pythonProcess = null;
      });

      return sendJSON(res, { success: true });
    }

    // ---- 翻译状态查询（浏览器重连恢复）----
    if (pathname === '/api/translate/status' && req.method === 'GET') {
      return sendJSON(res, {
        ...translateState,
        logs: recentLogs.slice(-100),
      });
    }

    if (pathname === '/api/translate/cancel' && req.method === 'POST') {
      killPython();
      return sendJSON(res, { success: true });
    }

    // ---- 结果审核 ----
    if (pathname === '/api/review/results' && req.method === 'GET') {
      var reviewWorkDir = query.workDir;
      if (!reviewWorkDir) return sendJSON(res, { error: 'workDir required' }, 400);

      try {
        var fetchScript = buildFetchScript(PROJECT_ROOT, reviewWorkDir);
        var tmpScript = path.join(PROJECT_ROOT, '_tmp_fetch.py');
        fs.writeFileSync(tmpScript, fetchScript, 'utf-8');
        try {
          var fetchOutput = await runPython([tmpScript]);
          return sendJSON(res, { success: true, data: JSON.parse(fetchOutput) });
        } finally {
          try { fs.unlinkSync(tmpScript); } catch(e) {}
        }
      } catch(e) {
        console.error('review fetch error:', e.message);
        return sendJSON(res, { success: false, error: e.message });
      }
    }

    if (pathname === '/api/review/save-edit' && req.method === 'POST') {
      var _body4 = await parseBody(req);
      if (!_body4.workDir) return sendJSON(res, { error: 'workDir required' }, 400);

      try {
        var saveScript = buildSaveScript(PROJECT_ROOT, _body4.workDir, _body4.filename, _body4.index, _body4.newTranslation);
        var tmpSave = path.join(PROJECT_ROOT, '_tmp_save.py');
        fs.writeFileSync(tmpSave, saveScript, 'utf-8');
        try {
          await runPython([tmpSave]);
          return sendJSON(res, { success: true });
        } finally {
          try { fs.unlinkSync(tmpSave); } catch(e) {}
        }
      } catch(e) {
        console.error('review save error:', e.message);
        return sendJSON(res, { success: false, error: e.message });
      }
    }

    // ---- 一致性检查 ----
    if (pathname === '/api/review/consistency-check' && req.method === 'POST') {
      var _body5 = await parseBody(req);
      if (!_body5.workDir) return sendJSON(res, { error: 'workDir required' }, 400);

      try {
        var checkScript = [
          'import sys',
          'sys.path.insert(0, r\'' + PROJECT_ROOT.replace(/\\/g, '\\\\') + '\')',
          'from pathlib import Path',
          'from core.consistency_checker import check_consistency',
          'import json',
          'report = check_consistency(Path(r\'' + _body5.workDir.replace(/\\/g, '\\\\') + '\'))',
          'print(json.dumps(report, ensure_ascii=False))',
        ].join('\n');
        var tmpCheck = path.join(PROJECT_ROOT, '_tmp_consistency.py');
        fs.writeFileSync(tmpCheck, checkScript, 'utf-8');
        try {
          var checkOutput = await runPython([tmpCheck]);
          return sendJSON(res, { success: true, data: JSON.parse(checkOutput) });
        } finally {
          try { fs.unlinkSync(tmpCheck); } catch(e) {}
        }
      } catch(e) {
        console.error('consistency check error:', e.message);
        return sendJSON(res, { success: false, error: e.message });
      }
    }

    // ---- 预设管理（独立文件 presets.json）----
    if (pathname === '/api/config/presets' && req.method === 'GET') {
      var cfg = readConfig();
      var _presets = readPresets();
      var presetNames = Object.keys(_presets);
      if (presetNames.indexOf('default') === -1) presetNames.unshift('default');
      return sendJSON(res, { presets: presetNames, active: cfg.active_preset || 'default' });
    }

    if (pathname === '/api/config/presets/activate' && req.method === 'POST') {
      var _body6 = await parseBody(req);
      var _cfg = readConfig();
      var _presets2 = readPresets();
      var _name = _body6.name;
      if (_name !== 'default' && (!_presets2 || !_presets2[_name])) {
        return sendJSON(res, { success: false });
      }
      // 保存当前配置到旧预设（如果当前激活的不是 default 且有对应预设）
      var currentActive = _cfg.active_preset || 'default';
      if (currentActive !== 'default' && _presets2[currentActive]) {
        var curSections = {};
        for (var _si2 = 0; _si2 < CONFIG_SECTIONS.length; _si2++) {
          var _sk3 = CONFIG_SECTIONS[_si2];
          if (_cfg[_sk3] !== undefined) curSections[_sk3] = JSON.parse(JSON.stringify(_cfg[_sk3]));
        }
        _presets2[currentActive] = curSections;
      }
      // 从预设加载配置
      var presetData = _presets2[_name];
      if (presetData) {
        for (var _dk in presetData) {
          if (presetData.hasOwnProperty(_dk)) {
            _cfg[_dk] = JSON.parse(JSON.stringify(presetData[_dk]));
          }
        }
      }
      _cfg.active_preset = _name;
      // 保存 config（只含配置段 + active_preset）
      var _savedCfg = {};
      for (var _si3 = 0; _si3 < CONFIG_SECTIONS.length; _si3++) {
        var _sk4 = CONFIG_SECTIONS[_si3];
        if (_cfg[_sk4] !== undefined) _savedCfg[_sk4] = _cfg[_sk4];
      }
      _savedCfg.active_preset = _name;
      writeJSON(CONFIG_PATH, _savedCfg);
      writePresets(_presets2);
      return sendJSON(res, { success: true, config: _cfg });
    }

    if (pathname === '/api/config/presets/save' && req.method === 'POST') {
      var _body7 = await parseBody(req);
      var _cfg2 = readConfig();
      var _presets3 = readPresets();
      var _name2 = _body7.name;
      if (!_name2 || _name2 === 'default') return sendJSON(res, { success: false });
      // 首次创建预设时，自动备份当前配置为 default
      if (!_presets3['default'] && Object.keys(_presets3).length === 0) {
        var _defaultSections = {};
        for (var _dk2 = 0; _dk2 < CONFIG_SECTIONS.length; _dk2++) {
          var _sk5 = CONFIG_SECTIONS[_dk2];
          if (_cfg2[_sk5] !== undefined) _defaultSections[_sk5] = JSON.parse(JSON.stringify(_cfg2[_sk5]));
        }
        _presets3['default'] = _defaultSections;
      }
      // 快照当前配置到预设
      var sections = {};
      for (var _sk2 = 0; _sk2 < CONFIG_SECTIONS.length; _sk2++) {
        var _sk6 = CONFIG_SECTIONS[_sk2];
        if (_cfg2[_sk6] !== undefined) sections[_sk6] = JSON.parse(JSON.stringify(_cfg2[_sk6]));
      }
      _presets3[_name2] = sections;
      _cfg2.active_preset = _name2;
      // 保存 config（只含配置段 + active_preset）
      var _savedCfg2 = {};
      for (var _si4 = 0; _si4 < CONFIG_SECTIONS.length; _si4++) {
        var _sk7 = CONFIG_SECTIONS[_si4];
        if (_cfg2[_sk7] !== undefined) _savedCfg2[_sk7] = _cfg2[_sk7];
      }
      _savedCfg2.active_preset = _name2;
      writeJSON(CONFIG_PATH, _savedCfg2);
      writePresets(_presets3);
      return sendJSON(res, { success: true });
    }

    if (pathname === '/api/config/presets/delete' && req.method === 'POST') {
      var _body8 = await parseBody(req);
      var _cfg3 = readConfig();
      var _presets4 = readPresets();
      var _name3 = _body8.name;
      if (!_presets4 || !_presets4[_name3]) return sendJSON(res, { success: false });
      delete _presets4[_name3];
      if (_cfg3.active_preset === _name3) {
        _cfg3.active_preset = 'default';
        var _savedCfg3 = {};
        for (var _si5 = 0; _si5 < CONFIG_SECTIONS.length; _si5++) {
          var _sk8 = CONFIG_SECTIONS[_si5];
          if (_cfg3[_sk8] !== undefined) _savedCfg3[_sk8] = _cfg3[_sk8];
        }
        _savedCfg3.active_preset = 'default';
        writeJSON(CONFIG_PATH, _savedCfg3);
      }
      writePresets(_presets4);
      return sendJSON(res, { success: true });
    }

    // ---- 静态文件服务（生产模式）----
    if (IS_PRODUCTION) {
      var filePath = pathname === '/' ? '/index.html' : pathname;
      var fullPath = path.join(RENDERER_DIR, filePath);
      // 安全检查：防止路径穿越
      if (fullPath.indexOf(RENDERER_DIR) === 0) {
        try {
          if (fs.existsSync(fullPath) && fs.statSync(fullPath).isFile()) {
            var ext = path.extname(fullPath).toLowerCase();
            var mimeTypes = {
              '.html': 'text/html; charset=utf-8',
              '.js': 'application/javascript; charset=utf-8',
              '.css': 'text/css; charset=utf-8',
              '.json': 'application/json; charset=utf-8',
              '.png': 'image/png',
              '.jpg': 'image/jpeg',
              '.jpeg': 'image/jpeg',
              '.svg': 'image/svg+xml',
              '.ico': 'image/x-icon',
              '.woff': 'font/woff',
              '.woff2': 'font/woff2',
            };
            var mime = mimeTypes[ext] || 'application/octet-stream';
            res.writeHead(200, { 'Content-Type': mime, 'Access-Control-Allow-Origin': '*' });
            return res.end(fs.readFileSync(fullPath));
          }
        } catch {}
      }
      // SPA fallback：非 API 路由返回 index.html
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      return res.end(fs.readFileSync(path.join(RENDERER_DIR, 'index.html')));
    }

    // 404
    sendJSON(res, { error: 'Not found' }, 404);
  } catch(e) {
    console.error('Server error:', e);
    sendJSON(res, { error: e.message }, 500);
  }
});

server.listen(PORT, function() {
  var mode = IS_PRODUCTION ? 'PRODUCTION (static frontend + API)' : 'DEVELOPMENT (API only, run Vite separately)';
  console.log('[转译 Server] running on http://localhost:' + PORT);
  console.log('[转译 Server] mode: ' + mode);
  console.log('[转译 Server] project root: ' + PROJECT_ROOT);
  console.log('[转译 Server] config.json: ' + (fs.existsSync(CONFIG_PATH) ? 'FOUND' : 'NOT FOUND'));
  console.log('[转译 Server] presets.json: ' + (fs.existsSync(PRESETS_PATH) ? 'FOUND' : 'NOT FOUND'));
});

// Cleanup
process.on('SIGINT', function() { killPython(); server.close(); process.exit(); });
process.on('SIGTERM', function() { killPython(); server.close(); process.exit(); });
