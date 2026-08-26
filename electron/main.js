'use strict';

const fs = require('fs');
const path = require('path');
const { app, BrowserWindow, dialog } = require('electron');
const {
  assertRuntimeResources,
  findPython,
  resolveDataDirectory,
  resolveFactorLibraryFile,
  resolveRuntimePaths,
  startBackend,
} = require('./runtime');

let mainWindow = null;
let backend = null;
let backendStartupPromise = null;
let shutdownPromise = null;
let quitting = false;

function requestedPort(env) {
  const raw = typeof env.QTRADE_ELECTRON_PORT === 'string'
    ? env.QTRADE_ELECTRON_PORT.trim()
    : '';
  if (!raw) return 0;
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error('QTRADE_ELECTRON_PORT must be an integer from 1 to 65535.');
  }
  return port;
}

function createWindow(url, paths) {
  const icon = fs.existsSync(paths.iconFile) ? paths.iconFile : undefined;
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1000,
    minHeight: 650,
    title: 'QTrade',
    ...(icon ? { icon } : {}),
    backgroundColor: '#0B0E14',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  window.setMenuBarVisibility(false);
  window.loadURL(url, {
    extraHeaders: 'pragma: no-cache\ncache-control: no-cache',
  }).catch((error) => {
    console.error(`[electron] failed to load QTrade UI: ${error.message}`);
  });
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null;
  });
  mainWindow = window;
  return window;
}

async function stopBackend() {
  if (shutdownPromise) return shutdownPromise;
  shutdownPromise = (async () => {
    let pendingBackend = null;
    if (backendStartupPromise) {
      try {
        pendingBackend = await backendStartupPromise;
      } catch {
        // startBackend performs its own cleanup when startup fails.
      }
    }
    const running = backend || pendingBackend;
    backend = null;
    if (running) {
      await running.stop();
    }
  })();
  return shutdownPromise;
}

function conciseStartupMessage(error) {
  const raw = error && error.message ? error.message : String(error || 'Unknown startup error');
  const firstLine = raw.split(/\r?\n/, 1)[0].trim() || 'Unknown startup error';
  return `${firstLine.slice(0, 240)}\n\n` +
    'Check Python 3.10+ and project dependencies, or set QTRADE_PYTHON ' +
    'to the executable path.';
}

async function bootstrap() {
  if (quitting) return;
  const packaged = app.isPackaged;
  const paths = resolveRuntimePaths({
    packaged,
    resourcesPath: process.resourcesPath,
  });
  assertRuntimeResources(paths);

  const userDataPath = app.getPath('userData');
  const dataDir = resolveDataDirectory({
    env: process.env,
    userDataPath,
    packaged,
    paths,
  });
  fs.mkdirSync(dataDir, { recursive: true });

  const python = findPython();
  if (quitting) return;
  const startupPromise = startBackend({
    paths,
    python,
    cwd: userDataPath,
    dataDir,
    factorLibraryFile: resolveFactorLibraryFile(userDataPath),
    csvOnly: process.env.QTRADE_ELECTRON_CSV_ONLY === '1',
    preferredPort: requestedPort(process.env),
  });
  backendStartupPromise = startupPromise;
  let startedBackend;
  try {
    startedBackend = await startupPromise;
  } finally {
    if (backendStartupPromise === startupPromise) backendStartupPromise = null;
  }
  if (quitting) {
    await startedBackend.stop();
    return;
  }
  backend = startedBackend;
  console.log(`[electron] QTrade backend ready at ${backend.url}`);
  createWindow(backend.url, paths);
}

app.whenReady().then(bootstrap).catch(async (error) => {
  console.error('[electron] startup failed:', error && error.stack ? error.stack : error);
  try {
    dialog.showErrorBox('QTrade 启动失败', conciseStartupMessage(error));
  } catch (dialogError) {
    console.error('[electron] failed to show startup error dialog:', dialogError);
  }
  await stopBackend();
  app.exit(1);
});

app.on('before-quit', (event) => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  stopBackend()
    .catch((error) => console.error(`[electron] backend cleanup failed: ${error.message}`))
    .finally(() => app.quit());
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (mainWindow === null && backend) {
    createWindow(backend.url, resolveRuntimePaths({
      packaged: app.isPackaged,
      resourcesPath: process.resourcesPath,
    }));
  }
});
