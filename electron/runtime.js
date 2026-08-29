'use strict';

const fs = require('fs');
const path = require('path');
const net = require('net');
const http = require('http');
const { spawn, spawnSync } = require('child_process');

const HEALTH_PATH = '/api/health';
const DEFAULT_STARTUP_TIMEOUT_MS = 30_000;
const DEFAULT_POLL_INTERVAL_MS = 150;
const DEFAULT_PYTHON_PREFLIGHT_TIMEOUT_MS = 15_000;
const MAX_PYTHON_PREFLIGHT_ATTEMPTS = 2;
const DEFAULT_REQUIRED_PYTHON_MODULES = Object.freeze(['pandas', 'akshare']);

const PYTHON_PREFLIGHT_SCRIPT = [
  'import importlib, json, sys',
  'required = ' + JSON.stringify(DEFAULT_REQUIRED_PYTHON_MODULES),
  'missing = []',
  'for name in required:',
  '    try:',
  '        importlib.import_module(name)',
  '    except ModuleNotFoundError as error:',
  "        missing.append(name if error.name == name else f'{name} (requires {error.name})')",
  '    except Exception as error:',
  "        missing.append(f'{name} ({type(error).__name__})')",
  "version = '.'.join(str(part) for part in sys.version_info[:3])",
  'supported = sys.version_info >= (3, 10)',
  "print(json.dumps({'python': sys.executable, 'version': version, 'supported': supported, 'missing': missing}, separators=(',', ':')))",
  'raise SystemExit(0 if supported and not missing else 1)',
].join('\n');

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
    schedulerScript: path.join(root, 'scripts', 'daily_update_1830.py'),
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
    ['scripts/daily_update_1830.py', paths.schedulerScript],
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

function resolveFactorLibraryFile(userDataPath) {
  if (typeof userDataPath !== 'string' || !userDataPath.trim()) {
    throw new Error('QTrade factor library requires an Electron user-data directory.');
  }
  return path.join(userDataPath, 'factor_library.json');
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

function normalizeRequiredModules(requiredModules = DEFAULT_REQUIRED_PYTHON_MODULES) {
  if (!Array.isArray(requiredModules)) {
    throw new TypeError('requiredModules must be an array of module names.');
  }
  return [...new Set(requiredModules.filter((moduleName) => (
    typeof moduleName === 'string' && moduleName.trim()
  )).map((moduleName) => moduleName.trim()))];
}

function buildPythonPreflightScript(requiredModules = DEFAULT_REQUIRED_PYTHON_MODULES) {
  const modules = normalizeRequiredModules(requiredModules);
  return PYTHON_PREFLIGHT_SCRIPT.replace(
    `required = ${JSON.stringify(DEFAULT_REQUIRED_PYTHON_MODULES)}`,
    `required = ${JSON.stringify(modules)}`
  );
}

function isPythonPreflightTimeout(value) {
  return value?.code === 'ETIMEDOUT'
    || (typeof value?.message === 'string' && /\bETIMEDOUT\b/.test(value.message));
}

function getSpawnErrorMessage(error) {
  if (!error) return null;
  if (typeof error.message === 'string' && error.message) return error.message;
  return typeof error === 'string' ? error : String(error);
}

function parsePythonPreflight(result, candidate, requiredModules) {
  const stdout = typeof result?.stdout === 'string' ? result.stdout : '';
  const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  let payload = {};
  try {
    if (lines.length > 0) payload = JSON.parse(lines.at(-1));
  } catch {
    payload = {};
  }
  const timedOut = isPythonPreflightTimeout(result?.error);
  const missing = Array.isArray(payload.missing)
    ? payload.missing.filter((moduleName) => typeof moduleName === 'string')
    : [];
  const supported = payload.supported === true;
  const error = timedOut ? null : getSpawnErrorMessage(result?.error);
  return {
    ok: !error && result?.status === 0 && supported && missing.length === 0,
    error,
    missing,
    pythonPath: typeof payload.python === 'string' && payload.python ? payload.python : candidate.command,
    requiredModules,
    status: result?.status ?? null,
    supported,
    timedOut,
    version: typeof payload.version === 'string' ? payload.version : null,
  };
}

function probePythonDetails(candidate, {
  spawnSyncImpl = spawnSync,
  timeoutMs = DEFAULT_PYTHON_PREFLIGHT_TIMEOUT_MS,
  requiredModules = DEFAULT_REQUIRED_PYTHON_MODULES,
} = {}) {
  const modules = normalizeRequiredModules(requiredModules);
  for (let attempt = 1; attempt <= MAX_PYTHON_PREFLIGHT_ATTEMPTS; attempt += 1) {
    try {
      const result = spawnSyncImpl(
        candidate.command,
        [...candidate.args, '-c', buildPythonPreflightScript(modules)],
        {
          encoding: 'utf8',
          timeout: timeoutMs,
          shell: false,
          windowsHide: true,
          stdio: ['ignore', 'pipe', 'pipe'],
        }
      );
      const details = parsePythonPreflight(result, candidate, modules);
      details.attempts = attempt;
      details.timeoutMs = timeoutMs;
      if (!details.timedOut || attempt === MAX_PYTHON_PREFLIGHT_ATTEMPTS) return details;
    } catch (error) {
      const timedOut = isPythonPreflightTimeout(error);
      if (!timedOut || attempt === MAX_PYTHON_PREFLIGHT_ATTEMPTS) {
        return {
          ok: false,
          error: timedOut ? null : getSpawnErrorMessage(error),
          missing: [],
          pythonPath: candidate.command,
          requiredModules: modules,
          status: null,
          supported: false,
          timedOut,
          attempts: attempt,
          timeoutMs,
          version: null,
        };
      }
    }
  }

  throw new Error('Python preflight attempt loop did not return a result.');
}

function probePython(candidate, options = {}) {
  return probePythonDetails(candidate, options).ok;
}

function describePythonFailure(candidate, details) {
  const problems = [];
  if (details.timedOut) {
    problems.push(
      `Python preflight timed out after ${details.timeoutMs} ms `
      + `(attempts: ${details.attempts || 1})`
    );
    problems.push('ensure the interpreter is available and retry');
  } else {
    if (details.missing.length > 0) {
      problems.push(`missing modules: ${details.missing.join(', ')}`);
    }
    if (!details.supported) {
      problems.push(
        details.version
          ? `Python ${details.version} is below 3.10`
          : 'Python version could not be verified'
      );
    }
    if (details.error) problems.push(details.error);
  }
  if (problems.length === 0) problems.push(`preflight exited with status ${details.status}`);
  return `${candidate.source || 'Python candidate'}; Python path/source: ${details.pythonPath}; ${problems.join('; ')}`;
}

function findPython({
  platform = process.platform,
  env = process.env,
  candidates = pythonCandidates({ platform, env }),
  spawnSyncImpl = spawnSync,
  requiredModules = DEFAULT_REQUIRED_PYTHON_MODULES,
} = {}) {
  const modules = normalizeRequiredModules(requiredModules);
  const configured = typeof env.QTRADE_PYTHON === 'string' ? env.QTRADE_PYTHON.trim() : '';
  if (configured) {
    const candidate = { command: configured, args: [], source: 'QTRADE_PYTHON' };
    const details = probePythonDetails(candidate, { spawnSyncImpl, requiredModules: modules });
    if (details.ok) return candidate;
    throw new Error(
      `QTRADE_PYTHON is explicitly configured but failed QTrade Python preflight: ` +
      `${describePythonFailure(candidate, details)}. ` +
      'Install Python 3.10+ with the project dependencies, or retry after the interpreter starts. ' +
      'Do not silently switch interpreters.'
    );
  }

  const failures = [];
  for (const candidate of candidates) {
    const details = probePythonDetails(candidate, { spawnSyncImpl, requiredModules: modules });
    if (details.ok) {
      return candidate;
    }
    failures.push(describePythonFailure(candidate, details));
  }

  const checked = candidates.map(describePythonCandidate).join(', ');
  throw new Error(
    `Unable to find a usable Python 3 interpreter. Checked: ${checked || '(none)'}. ` +
    `Required modules: ${modules.join(', ') || '(none)'}. ` +
    `${failures.join(' | ')}. Install Python 3.10+ with the project dependencies, or set ` +
    'QTRADE_PYTHON to the executable path.'
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

function requestManualUpdateStop({
  host = '127.0.0.1',
  port,
  timeoutMs = 1_500,
  requestImpl = http.request,
} = {}) {
  return new Promise((resolve) => {
    let settled = false;
    let timer;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve(ok);
    };

    let request;
    try {
      request = requestImpl({
        host,
        port,
        path: '/api/update/run/stop',
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'Content-Length': 2,
        },
        agent: false,
      }, (response) => {
        if (typeof response.resume === 'function') response.resume();
        finish(response.statusCode === 200);
      });
    } catch {
      finish(false);
      return;
    }

    timer = setTimeout(() => {
      try { request.destroy(); } catch { /* already closed */ }
      finish(false);
    }, timeoutMs);
    request.on('error', () => finish(false));
    try {
      request.end('{}');
    } catch {
      finish(false);
    }
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

function terminateChildTree(child) {
  if (process.platform !== 'win32' || !child || !Number.isInteger(child.pid) || child.pid <= 0) return false;
  try {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
      shell: false,
      windowsHide: true,
      stdio: 'ignore',
      timeout: 1_000,
    });
    return true;
  } catch {
    return false;
  }
}

function createChildStopper(child, { killTimeoutMs = 5_000, treeKillImpl = terminateChildTree } = {}) {
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
    try { treeKillImpl(child); } catch { /* fallback to the exact child below */ }
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
  factorLibraryFile,
  stateDir,
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
  if (factorLibraryFile) {
    args.push('--factor-library-file', factorLibraryFile);
  }
  if (stateDir) {
    args.push('--state-dir', stateDir);
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
  factorLibraryFile,
  stateDir,
  csvOnly = false,
  preferredPort = 0,
  spawnImpl = spawn,
  requestHealthImpl = requestHealth,
  requestShutdownImpl = requestManualUpdateStop,
  shutdownTimeoutMs = 1_500,
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
    factorLibraryFile,
    stateDir,
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
  const stop = createIdempotentCleanup(async () => {
    await requestShutdownImpl({ port, timeoutMs: shutdownTimeoutMs });
    await createChildStopper(child)();
  });
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
  DEFAULT_PYTHON_PREFLIGHT_TIMEOUT_MS,
  MAX_PYTHON_PREFLIGHT_ATTEMPTS,
  DEFAULT_REQUIRED_PYTHON_MODULES,
  HEALTH_PATH,
  assertRuntimeResources,
  buildServerArguments,
  buildPythonPreflightScript,
  createChildStopper,
  createIdempotentCleanup,
  findPython,
  getAvailablePort,
  parseHealthPayload,
  probePython,
  probePythonDetails,
  pythonCandidates,
  requiredRuntimeResources,
  requestHealth,
  requestManualUpdateStop,
  resolveDataDirectory,
  resolveFactorLibraryFile,
  resolveRuntimePaths,
  startBackend,
  waitForBackend,
};
