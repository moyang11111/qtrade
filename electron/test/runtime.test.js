'use strict';

const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');

const runtime = require('../runtime');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');

function preflightOutput({
  python = 'C:\\Python312\\python.exe',
  version = '3.12.0',
  supported = true,
  missing = [],
} = {}) {
  return JSON.stringify({ python, version, supported, missing });
}

test('development and packaged runtime roots are deterministic', () => {
  const development = runtime.resolveRuntimePaths({
    dirname: path.join(PROJECT_ROOT, 'electron'),
  });
  assert.equal(development.root, PROJECT_ROOT);
  assert.equal(path.basename(development.serverScript), 'server.py');
  assert.equal(path.basename(development.staticDir), 'static');
  assert.equal(path.basename(development.paperTradingDir), 'paper_trading');

  const packaged = runtime.resolveRuntimePaths({
    packaged: true,
    resourcesPath: path.join('C:', 'QTrade', 'resources'),
  });
  assert.equal(packaged.root, path.join('C:', 'QTrade', 'resources', 'qtrade'));
  assert.equal(packaged.staticDir, path.join(packaged.root, 'static'));
});

test('data directory priority separates development and packaged defaults', () => {
  const paths = { root: path.join(PROJECT_ROOT, 'runtime-root') };
  const userDataPath = path.join(PROJECT_ROOT, 'user-data');
  const configured = path.join(PROJECT_ROOT, 'configured-cache');

  assert.equal(
    runtime.resolveDataDirectory({ paths, packaged: false, userDataPath, env: {} }),
    path.join(paths.root, 'data', 'cache')
  );
  assert.equal(
    runtime.resolveDataDirectory({ paths, packaged: true, userDataPath, env: {} }),
    path.join(userDataPath, 'data', 'cache')
  );
  assert.equal(
    runtime.resolveDataDirectory({
      paths,
      packaged: false,
      userDataPath,
      env: { QTRADE_DATA_DIR: configured },
    }),
    path.resolve(configured)
  );
  assert.equal(
    runtime.resolveDataDirectory({
      paths,
      packaged: true,
      userDataPath,
      env: { QTRADE_DATA_DIR: configured },
    }),
    path.resolve(configured)
  );
});

test('required packaged resources include the Python server and its local imports', () => {
  const paths = runtime.resolveRuntimePaths({ rootOverride: PROJECT_ROOT });
  const labels = runtime.requiredRuntimeResources(paths).map(([label]) => label);
  assert.deepEqual(labels, [
    'server.py',
    'static/index.html',
    'paper_trading/engine.py',
    'qtrade_base_bridge.py',
    'factors.py',
  ]);
  assert.doesNotThrow(() => runtime.assertRuntimeResources(paths));
});

test('Python discovery honors QTRADE_PYTHON without shell parsing', () => {
  const calls = [];
  const configured = 'C:\\Program Files\\Python312\\python.exe';
  const candidate = runtime.findPython({
    platform: 'win32',
    env: { QTRADE_PYTHON: configured },
    spawnSyncImpl(command, args, options) {
      calls.push({ command, args, options });
      return {
        status: 0,
        error: null,
        stdout: preflightOutput({ python: configured }),
        stderr: '',
      };
    },
  });

  assert.equal(candidate.command, configured);
  assert.deepEqual(candidate.args, []);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.shell, false);
  assert.equal(calls[0].command, configured);
  assert.equal(calls[0].args.at(-2), '-c');
  assert.match(calls[0].args.at(-1), /sys\.version_info >= \(3, 10\)/);
});

test('Python version probe rejects pre-3.10 and accepts 3.10+', () => {
  const candidate = { command: 'python', args: [] };
  const probeExpression = [];
  const oldPython = runtime.probePython(candidate, {
    spawnSyncImpl: (_command, args) => {
      probeExpression.push(args.at(-1));
      return {
        status: 1,
        error: null,
        stdout: preflightOutput({ supported: false, version: '3.9.18' }),
        stderr: '',
      };
    },
  });
  const supportedPython = runtime.probePython(candidate, {
    spawnSyncImpl: (_command, args) => {
      probeExpression.push(args.at(-1));
      return {
        status: 0,
        error: null,
        stdout: preflightOutput(),
        stderr: '',
      };
    },
  });

  assert.equal(oldPython, false);
  assert.equal(supportedPython, true);
  assert.equal(probeExpression[0], probeExpression[1]);
  assert.match(probeExpression[0], /SystemExit/);
  assert.match(probeExpression[0], /sys\.version_info >= \(3, 10\)/);
});

test('Python discovery falls back to Windows py -3 and PATH python', () => {
  const calls = [];
  const candidate = runtime.findPython({
    platform: 'win32',
    env: {},
    spawnSyncImpl(command, args) {
      calls.push({ command, args });
      return command === 'py'
        ? {
          status: 1,
          error: null,
          stdout: preflightOutput({ supported: false, version: '3.9.18' }),
          stderr: '',
        }
        : {
          status: 0,
          error: null,
          stdout: preflightOutput({ python: 'C:\\Python312\\python.exe' }),
          stderr: '',
        };
    },
  });

  assert.equal(candidate.command, 'python');
  assert.deepEqual(calls.map(({ command }) => command), ['py', 'python']);
  assert.deepEqual(calls[0].args.slice(0, 1), ['-3']);
});

test('Python discovery reports an actionable error for an unusable explicit candidate', () => {
  assert.throws(
    () => runtime.findPython({
      platform: 'win32',
      env: { QTRADE_PYTHON: 'missing-python.exe' },
      spawnSyncImpl: () => ({ status: 1, error: null }),
    }),
    /QTRADE_PYTHON is explicitly configured.*Python path\/source: missing-python\.exe/
  );
});

test('Python preflight defaults to pandas and akshare without shell execution', () => {
  const calls = [];
  const candidate = { command: 'python', args: [] };
  const result = runtime.probePythonDetails(candidate, {
    spawnSyncImpl(command, args, options) {
      calls.push({ command, args, options });
      return {
        status: 0,
        error: null,
        stdout: preflightOutput(),
        stderr: '',
      };
    },
  });

  assert.equal(result.ok, true);
  assert.deepEqual(result.requiredModules, ['pandas', 'akshare']);
  assert.equal(calls[0].options.shell, false);
  assert.match(calls[0].args.at(-1), /importlib\.import_module/);
  assert.match(calls[0].args.at(-1), /"pandas"/);
  assert.match(calls[0].args.at(-1), /"akshare"/);
});

test('Python preflight reports missing modules and explicit paths do not fall back', () => {
  const configured = 'C:\\Users\\ASUS\\Apps\\QTrade\\runtime\\.venv\\Scripts\\python.exe';
  const calls = [];
  assert.throws(
    () => runtime.findPython({
      platform: 'win32',
      env: { QTRADE_PYTHON: configured },
      spawnSyncImpl(command, args) {
        calls.push({ command, args });
        return {
          status: 1,
          error: null,
          stdout: preflightOutput({
            python: configured,
            missing: ['akshare'],
          }),
          stderr: '',
        };
      },
    }),
    (error) => (
      /QTRADE_PYTHON is explicitly configured/.test(error.message)
      && /missing modules: akshare/.test(error.message)
      && error.message.includes(`Python path/source: ${configured}`)
      && /Do not silently switch interpreters/.test(error.message)
    )
  );
  assert.deepEqual(calls.map(({ command }) => command), [configured]);
});

test('Python preflight required modules can be injected for isolated tools', () => {
  let script = '';
  const candidate = runtime.findPython({
    env: { QTRADE_PYTHON: 'test-python' },
    requiredModules: ['pandas'],
    spawnSyncImpl: (_command, args) => {
      script = args.at(-1);
      return {
        status: 0,
        error: null,
        stdout: preflightOutput(),
        stderr: '',
      };
    },
  });

  assert.equal(candidate.command, 'test-python');
  assert.match(script, /"pandas"/);
  assert.doesNotMatch(script, /"akshare"/);
});

test('non-Windows Python discovery uses python3 before python', () => {
  assert.deepEqual(
    runtime.pythonCandidates({ platform: 'linux', env: {} }).map((candidate) => candidate.command),
    ['python3', 'python']
  );
});

test('health validation rejects unrelated HTTP services', () => {
  assert.equal(
    runtime.parseHealthPayload(200, JSON.stringify({ status: 'ok', mode: 'csv', symbols: 1 })).ok,
    true
  );
  assert.equal(runtime.parseHealthPayload(200, JSON.stringify({ status: 'ok' })).ok, false);
  assert.equal(
    runtime.parseHealthPayload(200, JSON.stringify({ status: 'ok', mode: 'html', symbols: 1 })).ok,
    false
  );
  assert.equal(runtime.parseHealthPayload(200, '<html>not qtrade</html>').ok, false);
  assert.equal(
    runtime.parseHealthPayload(503, JSON.stringify({ status: 'ok', mode: 'csv', symbols: 1 })).ok,
    false
  );
});

test('backend arguments are argv-only and include safe local-mode flags', () => {
  const args = runtime.buildServerArguments({
    python: { args: ['-3'] },
    serverScript: 'C:\\QTrade Root\\server.py',
    port: 43210,
    dataDir: 'C:\\QTrade Data\\cache',
    csvOnly: true,
  });
  assert.deepEqual(args, [
    '-3', '-X', 'utf8', 'C:\\QTrade Root\\server.py',
    '--port', '43210', '--no-browser', '--single-instance',
    '--data-dir', 'C:\\QTrade Data\\cache', '--csv-only',
  ]);
});

test('cleanup is idempotent for repeated callers', async () => {
  let calls = 0;
  const cleanup = runtime.createIdempotentCleanup(async () => {
    calls += 1;
  });
  const first = cleanup();
  const second = cleanup();
  assert.equal(first, second);
  await Promise.all([first, second]);
  assert.equal(calls, 1);
});

test('child cleanup does not issue duplicate kills', async () => {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  child.killCalls = 0;
  child.kill = () => {
    child.killCalls += 1;
    child.exitCode = 0;
    child.signalCode = null;
    child.emit('exit', 0, null);
    child.emit('close', 0);
    return true;
  };

  const stop = runtime.createChildStopper(child);
  await Promise.all([stop(), stop()]);
  assert.equal(child.killCalls, 1);
});

test('backend startup timeout stops the child process', async () => {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.killCalls = 0;
  child.kill = () => {
    child.killCalls += 1;
    child.exitCode = 0;
    child.emit('exit', 0, null);
    child.emit('close', 0);
    return true;
  };

  await assert.rejects(
    runtime.startBackend({
      paths: runtime.resolveRuntimePaths({ rootOverride: PROJECT_ROOT }),
      python: { command: 'test-python', args: [] },
      spawnImpl: () => child,
      requestHealthImpl: async () => ({ ok: false, reason: 'test backend is not ready' }),
      startupTimeoutMs: 10,
      pollIntervalMs: 1,
    }),
    /health check timed out/
  );
  assert.equal(child.killCalls, 1);
});

test('preload, package resources, and launcher are present and portable', () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(PROJECT_ROOT, 'electron', 'package.json'), 'utf8'));
  assert.equal(packageJson.main, 'main.js');
  assert.ok(fs.existsSync(path.join(PROJECT_ROOT, 'electron', packageJson.main)));
  assert.ok(fs.existsSync(path.join(PROJECT_ROOT, 'electron', 'preload.js')));
  const mainSource = fs.readFileSync(path.join(PROJECT_ROOT, 'electron', 'main.js'), 'utf8');
  assert.match(mainSource, /dialog\.showErrorBox/);
  assert.match(mainSource, /slice\(0, 240\)/);
  const adapterEntry = packageJson.build.extraResources.find(
    (entry) => entry.to === 'qtrade/qtrade_adapters'
  );
  assert.deepEqual(adapterEntry, {
    from: '../qtrade_adapters',
    to: 'qtrade/qtrade_adapters',
    filter: [
      '**/*.py',
      '!**/__pycache__/**',
      '!**/*.pyc',
    ],
  });
  for (const relativePath of [
    'qtrade_adapters/__init__.py',
    'qtrade_adapters/deepseek_harness/__init__.py',
    'qtrade_adapters/deepseek_harness/config.py',
    'qtrade_adapters/deepseek_harness/handler.py',
    'qtrade_adapters/deepseek_harness/decisions.py',
    'qtrade_adapters/deepseek_harness/runtime.py',
  ]) {
    assert.ok(fs.existsSync(path.join(PROJECT_ROOT, relativePath)), relativePath);
  }
  const factorEntry = packageJson.build.extraResources.find(
    (entry) => entry.to === 'qtrade/qtrade_factors'
  );
  assert.deepEqual(factorEntry, {
    from: '../qtrade_factors',
    to: 'qtrade/qtrade_factors',
    filter: [
      '**/*.py',
      '!**/__pycache__/**',
      '!**/*.pyc',
    ],
  });
  for (const relativePath of [
    'qtrade_factors/__init__.py',
    'qtrade_factors/common.py',
    'qtrade_factors/price_volume.py',
    'qtrade_factors/classic.py',
    'qtrade_factors/empirical.py',
    'qtrade_factors/registry.py',
    'qtrade_factors/scoring.py',
  ]) {
    assert.ok(fs.existsSync(path.join(PROJECT_ROOT, relativePath)), relativePath);
  }
  assert.ok(packageJson.build.extraResources.some((entry) => entry.to === 'qtrade/static'));
  const paperTradingEntry = packageJson.build.extraResources.find(
    (entry) => entry.to === 'qtrade/paper_trading'
  );
  assert.deepEqual(paperTradingEntry, {
    from: '../paper_trading',
    to: 'qtrade/paper_trading',
    filter: [
      '**/*.py',
      '!**/__pycache__/**',
      '!**/*.pyc',
    ],
  });
  assert.deepEqual(
    paperTradingEntry.filter.filter((pattern) => !pattern.startsWith('!')),
    ['**/*.py']
  );
  assert.ok(paperTradingEntry.filter.includes('!**/__pycache__/**'));
  assert.ok(paperTradingEntry.filter.includes('!**/*.pyc'));
  for (const relativePath of [
    'paper_trading/__init__.py',
    'paper_trading/engine.py',
    'paper_trading/market_data.py',
    'paper_trading/service.py',
  ]) {
    assert.ok(fs.existsSync(path.join(PROJECT_ROOT, relativePath)), relativePath);
  }

  const launcher = fs.readFileSync(path.join(PROJECT_ROOT, 'qtrade_electron.bat'), 'utf8');
  assert.doesNotMatch(launcher, /C:\\Users\\ASUS/i);
  assert.match(launcher, /call\s+npm/i);
  assert.match(launcher, /exit\s+\/b\s+%EXIT_CODE%/i);
  if (process.platform === 'win32') {
    const bytes = fs.readFileSync(path.join(PROJECT_ROOT, 'qtrade_electron.bat'));
    assert.ok(bytes.includes(Buffer.from([13, 10])), 'Windows CMD launcher should contain CRLF');
    for (let index = 0; index < bytes.length; index += 1) {
      if (bytes[index] === 10) assert.equal(bytes[index - 1], 13, 'launcher contains lone LF');
    }
  }
});

test('Windows launcher propagates npm failure codes', { skip: process.platform !== 'win32' }, () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'qtrade-electron-bat-test-'));
  try {
    const fakeNpm = path.join(tempRoot, 'npm.cmd');
    fs.writeFileSync(fakeNpm, '@echo off\r\nexit /b 17\r\n', 'utf8');
    const result = spawnSync(
      process.env.ComSpec || 'cmd.exe',
      ['/d', '/c', path.join(PROJECT_ROOT, 'qtrade_electron.bat')],
      {
        cwd: PROJECT_ROOT,
        env: { ...process.env, PATH: `${tempRoot};${process.env.PATH || ''}` },
        encoding: 'utf8',
        windowsHide: true,
      }
    );
    assert.equal(result.status, 17, result.stderr || result.stdout);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});
