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
var PROJECT_ROOT = path.resolve(__dirname, '..');
var CONFIG_PATH = path.join(PROJECT_ROOT, 'config.json');

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

function writeConfig(section, data) {
  var config = readConfig();
  config[section] = Object.assign({}, config[section] || {}, data);
  writeJSON(CONFIG_PATH, config);
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

function killPython() {
  if (pythonProcess) {
    try { pythonProcess.kill(); } catch(e) {}
    pythonProcess = null;
  }
}

function runPython(args) {
  return new Promise(function(resolve, reject) {
    var proc = spawn('python', ['-u'].concat(args), { cwd: PROJECT_ROOT, env: PYTHON_ENV });
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
      res.write('event: connected\ndata: {}\n\n');
      sseClients.add(res);
      req.on('close', function() { sseClients.delete(res); });
      return;
    }

    // ---- 配置 ----
    if (pathname === '/api/config' && req.method === 'GET') {
      return sendJSON(res, readConfig());
    }
    if (pathname.startsWith('/api/config/') && req.method === 'POST') {
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
      var searchDirs = [dir];
      try {
        var subEntries = fs.readdirSync(dir, { withFileTypes: true });
        subEntries.forEach(function(e) {
          if (e.isDirectory()) searchDirs.push(path.join(dir, e.name));
        });
      } catch(e) {
        console.log('[dev-server] readdir error for', dir, ':', e.message);
      }

      var candidates = ['.' + name + '.json', name + '.json'];
      for (var si = 0; si < searchDirs.length; si++) {
        for (var ci = 0; ci < candidates.length; ci++) {
          var filePath = path.join(searchDirs[si], candidates[ci]);
          var result = readJSON(filePath);
          if (result !== null) return { data: result, foundDir: searchDirs[si] };
        }
      }
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

      console.log('[dev-server] aids read: terms=' + Object.keys(termsData).length +
        ', alias=' + (Array.isArray(aliasData) ? aliasData.length : 0) +
        ', worldview keys=' + Object.keys(worldviewData).length);

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
      fs.writeFileSync(path.join(PROJECT_ROOT, 'input_path.txt'), _body3.workDir, 'utf-8');

      pythonProcess = spawn('python', ['-u', path.join(PROJECT_ROOT, 'translate.py')], {
        cwd: PROJECT_ROOT,
        env: PYTHON_ENV,
      });

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
            broadcastSSE('python:log', { level: 'info', message: line.trim() });
          }
        });
      });

      pythonProcess.stderr.on('data', function(data) {
        broadcastSSE('python:log', { level: 'error', message: data.toString('utf-8').trim() });
      });

      pythonProcess.on('close', function(code) {
        broadcastSSE('python:done', { exitCode: code });
        pythonProcess = null;
      });

      return sendJSON(res, { success: true });
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

    // 404
    sendJSON(res, { error: 'Not found' }, 404);
  } catch(e) {
    console.error('Server error:', e);
    sendJSON(res, { error: e.message }, 500);
  }
});

server.listen(PORT, function() {
  console.log('[转译 Dev Server] running on http://localhost:' + PORT);
  console.log('[转译 Dev Server] project root: ' + PROJECT_ROOT);
  console.log('[转译 Dev Server] config.json: ' + (fs.existsSync(CONFIG_PATH) ? 'FOUND' : 'NOT FOUND'));
});

// Cleanup
process.on('SIGINT', function() { killPython(); server.close(); process.exit(); });
process.on('SIGTERM', function() { killPython(); server.close(); process.exit(); });
