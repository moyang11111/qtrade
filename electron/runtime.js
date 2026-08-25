'use strict';

const fs = require('fs');
const path = require('path');
const net = require('net');
const http = require('http');
const { spawn, spawnSync } = require('child_process');

const HEALTH_PATH = '/api/health';
const DEFAULT_STARTUP_TIMEOUT_MS = 30_000;
const DEFAULT_POLL_INTERVAL_MS = 150;

function resolveRuntimePaths({
  packaged = false,
  dirname = __dirname,
  resourcesPath = process.resourcesPath,
  rootOverride,
} = {}) {
  const root = rootOverride
    ? path.resolve(rootOverride)
    : packaged
      ? path.join(resourcesPath, 'qtrade')
      : path.resolve(dirname, '..');

  return {
    root,
    serverScript: path.join(root, 'server.py'),
    staticDir: path.join(root, 'static'),
    paperTradingDir: path.join(root, 'paper_trading'),
    bridgeScript: path.join(root, 'qtrade_base_bridge.py'),
    factorsScript: path.join(root, 'factors.py'),
    iconFile: path.join(root, 'qtrade.ico'),
  };
}

function requiredRuntimeResources(paths) {
  return [
    ['server.py', paths.serverScript],
    ['static/index.html', path.join(paths.staticDir, 'index.html')],
    ['paper_trading/engine.py', path.join(paths.paperTradingDir, 'engine.py')],
    ['qtrade_base_bridge.py', paths.bridgeScript],
    ['factors.py', paths.factorsScript],
  ];
}

function assertRuntimeResources(paths, fsApi = fs) {
  const missing = requiredRuntimeResources(paths)
    .filter(([, resourcePath]) => !fsApi.existsSync(resourcePath))
    .map(([label]) => label);
  if (missing.length > 0) {
    throw new Error(
      `QTrade packaged resources are incomplete; missing: ${missing.join(', ')}. ` +
      'Rebuild the application from the repository root.'
    );
  }
  return paths;
}

function resolveDataDirectory({
  env = process.env,
  userDataPath,
  packaged = false,
  paths,
} = {}) {
  const configured = typeof env.QTRADE_DATA_DIR === 'string' ? env.QTRADE_DATA_DIR.trim() : '';
  if (configured) {
    return path.resolve(configured);
  }
  if (packaged && userDataPath) {
    return path.join(userDataPath, 'data', 'cache');
  }
  return path.join(paths.root, 'data', 'cache');
}

function pythonCandidates({ platform = process.platform, env = process.env } = {}) {
  const candidates = [];
  const configured = typeof env.QTRADE_PYTHON === 'string' ? env.QTRADE_PYTHON.trim() : '';
  if (configured) {
    candidates.push({ command: configured, args: [], source: 'QTRADE_PYTHON' });
  }

  if (platform === 'win32') {
    candidates.push({ command: 'py', args: ['-3'], source: 'Windows py -3' });
    candidates.push({ command: 'python', args: [], source: 'Windows PATH python' });
  } else {
    candidates.push({ command: 'python3', args: [], source: 'PATH python3' });
    candidates.push({ command: 'python', args: [], source: 'PATH python' });
  }
  return candidates;
}

function describePythonCandidate(candidate) {
  return candidate.args.length > 0
    ? `${candidate.command} ${candidate.args.join(' ')}`
    : candidate.command;
}

function probePython(candidate, {
  spawnSyncImpl = spawnSync,
  timeoutMs = 5_000,
} = {}) {
  try {
    const result = spawnSyncImpl(
      candidate.command,
      [...candidate.args, '-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'],
      {
        encoding: 'utf8',
        timeout: timeoutMs,
        shell: false,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      }
    );
    return !result.error && result.status === 0;
  } catch {
    return false;
  }
}

function findPython({
  platform = process.platform,
  env = process.env,
  candidates = pythonCandidates({ platform, env }),
  spawnSyncImpl = spawnSync,
} = {}) {
  for (const candidate of candidates) {
    if (probePython(candidate, { spawnSyncImpl })) {
      return candidate;
    }
  }

  const checked = candidates.map(describePythonCandidate).join(', ');
  throw new Error(
    `Unable to find a usable Python 3 interpreter. Checked: ${checked || '(none)'}. ` +
    'Install Python 3.10+ with the project dependencies, or set QTRADE_PYTHON ' +
    'to the executable path.'
  );
}

function getAvailablePort({ host = '127.0.0.1', preferredPort = 0 } = {}) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    const onError = (error) => {
      server.close();
      reject(error);
    };
    server.once('error', onError);
    server.listen(Number(preferredPort) || 0, host, () => {
      server.removeListener('error', onError);
      const address = server.address();
      const port = address && typeof address === 'object' ? address.port : 0;
      server.close((closeError) => {
        if (closeError) {
          reject(closeError);
        } else if (!port) {
          reject(new Error('Unable to allocate a local TCP port for QTrade.'));
        } else {
          resolve(port);
        }
      });
    });
  });
}

function parseHealthPayload(statusCode, body) {
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    return { ok: false, reason: 'health response was not valid JSON' };
  }

  const valid = statusCode === 200
    && payload
    && payload.status === 'ok'
    && (payload.mode === 'csv' || payload.mode === 'live')
    && Number.isInteger(payload.symbols)
    && payload.symbols >= 0;
  if (!valid) {
    return { ok: false, reason: 'health response was not a QTrade health payload', payload };
  }
  return { ok: true, payload };
}

function requestHealth({
  host = '127.0.0.1',
  port,
  timeoutMs = 1_000,
  requestImpl = http.get,
} = {}) {
  return new Promise((resolve) => {
    let settled = false;
    let body = '';
    const finish = (result) => {
      if (!settled) {
        settled = true;
        resolve(result);
      }
    };

    let request;
    try {
      request = requestImpl({
        host,
        port,
        path: HEALTH_PATH,
        method: 'GET',
        headers: { Accept: 'application/json' },
        agent: false,
      }, (response) => {
        response.setEncoding('utf8');
        response.on('data', (chunk) => { body += chunk; });
        response.on('end', () => finish(parseHealthPayload(response.statusCode, body)));
      });
    } catch (error) {
      finish({ ok: false, reason: error.message });
      return;
    }

    request.setTimeout(timeoutMs, () => {
      request.destroy();
      finish({ ok: false, reason: 'health request timed out' });
    });
    request.on('error', (error) => finish({ ok: false, reason: error.message }));
  });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function processHasExited(child) {
  return child && (child.exitCode != null || child.signalCode != null);
}

function createIdempotentCleanup(cleanup) {
  let cleanupPromise = null;
  return (...args) => {
    if (!cleanupPromise) {
      cleanupPromise = Promise.resolve().then(() => cleanup(...args));
    }
    return cleanupPromise;
  };
}

function createChildStopper(child, { killTimeoutMs = 5_000 } = {}) {
  return createIdempotentCleanup(() => new Promise((resolve) => {
    if (!child || processHasExited(child)) {
      resolve();
      return;
    }

    let timer;
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      if (timer) clearTimeout(timer);
      child.removeListener('exit', finish);
      child.removeListener('close', finish);
      resolve();
    };
    child.once('exit', finish);
    child.once('close', finish);
    timer = setTimeout(() => {
      if (!processHasExited(child)) {
        try {
          child.kill('SIGKILL');
        } catch {
          // The process may have exited between the check and kill.
        }
      }
      finish();
    }, killTimeoutMs);
    try {
      child.kill();
    } catch {
      finish();
      return;
    }
  }));
}

function captureProcessOutput(child, output) {
  const append = (key, chunk) => {
    output[key] = (output[key] + String(chunk)).slice(-16_000);
  };
  if (child.stdout && typeof child.stdout.on === 'function') {
    child.stdout.on('data', (chunk) => append('stdout', chunk));
  }
  if (child.stderr && typeof child.stderr.on === 'function') {
    child.stderr.on('data', (chunk) => append('stderr', chunk));
  }
}

function buildServerArguments({
  python,
  serverScript,
  port,
  dataDir,
  csvOnly = false,
} = {}) {
  const args = [
    ...python.args,
    '-X',
    'utf8',
    serverScript,
    '--port',
    String(port),
    '--no-browser',
    '--single-instance',
  ];
  if (dataDir) {
    args.push('--data-dir', dataDir);
  }
  if (csvOnly) {
    args.push('--csv-only');
  }
  return args;
}

async function waitForBackend({
  child,
  port,
  timeoutMs = DEFAULT_STARTUP_TIMEOUT_MS,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  requestHealthImpl = requestHealth,
  output,
} = {}) {
  const deadline = Date.now() + timeoutMs;
  let earlyFailure = null;
  const onExit = (code, signal) => {
    if (Date.now() < deadline) {
      earlyFailure = new Error(
        `QTrade Python backend exited before health check (code=${code}, signal=${signal || 'none'}).`
      );
    }
  };
  const onError = (error) => {
    earlyFailure = new Error(`QTrade Python backend failed to start: ${error.message}`);
  };
  child.once('exit', onExit);
  child.once('error', onError);

  let lastReason = 'no health response';
  try {
    while (Date.now() < deadline) {
      if (earlyFailure) throw earlyFailure;
      if (processHasExited(child)) {
        throw new Error('QTrade Python backend exited before health check.');
      }
      const health = await requestHealthImpl({ port });
      if (health.ok) return health.payload;
      lastReason = health.reason || lastReason;
      if (earlyFailure) throw earlyFailure;
      await delay(pollIntervalMs);
    }
  } finally {
    child.removeListener('exit', onExit);
    child.removeListener('error', onError);
  }

  const log = output && (output.stdout || output.stderr)
    ? ` Backend output:\n${output.stdout}${output.stderr}`
    : '';
  throw new Error(`QTrade backend health check timed out: ${lastReason}.${log}`);
}

async function startBackend({
  paths,
  python,
  env = {},
  cwd,
  dataDir,
  csvOnly = false,
  preferredPort = 0,
  spawnImpl = spawn,
  requestHealthImpl = requestHealth,
  startupTimeoutMs = DEFAULT_STARTUP_TIMEOUT_MS,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
} = {}) {
  if (!paths || !python) {
    throw new Error('QTrade backend startup requires resolved paths and a Python candidate.');
  }
  assertRuntimeResources(paths);
  const port = await getAvailablePort({ preferredPort });
  const output = { stdout: '', stderr: '' };
  const args = buildServerArguments({
    python,
    serverScript: paths.serverScript,
    port,
    dataDir,
    csvOnly,
  });
  let child;
  try {
    child = spawnImpl(python.command, args, {
      cwd: cwd || paths.root,
      env: { ...process.env, ...env },
      shell: false,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (error) {
    throw new Error(`Unable to launch Python backend: ${error.message}`);
  }

  captureProcessOutput(child, output);
  const stop = createChildStopper(child);
  try {
    await waitForBackend({
      child,
      port,
      timeoutMs: startupTimeoutMs,
      pollIntervalMs,
      requestHealthImpl,
      output,
    });
  } catch (error) {
    await stop();
    const details = output.stdout || output.stderr
      ? `\nBackend output:\n${output.stdout}${output.stderr}`
      : '';
    throw new Error(`${error.message}${details}`);
  }

  return {
    child,
    port,
    url: `http://127.0.0.1:${port}`,
    python,
    args,
    output,
    stop,
  };
}

module.exports = {
  HEALTH_PATH,
  assertRuntimeResources,
  buildServerArguments,
  createChildStopper,
  createIdempotentCleanup,
  findPython,
  getAvailablePort,
  parseHealthPayload,
  probePython,
  pythonCandidates,
  requiredRuntimeResources,
  requestHealth,
  resolveDataDirectory,
  resolveRuntimePaths,
  startBackend,
  waitForBackend,
};
