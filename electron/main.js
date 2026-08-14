const { app, BrowserWindow, session } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const SERVER_PORT = 8765;
const SERVER_URL = `http://127.0.0.1:${SERVER_PORT}`;

let mainWindow = null;
let pythonProcess = null;

// ---- Start Python backend ----
function startPythonBackend() {
  const serverScript = path.join(__dirname, '..', 'server.py');
  const pythonExe = process.platform === 'win32' ? 'python' : 'python3';

  pythonProcess = spawn(pythonExe, [serverScript, '--no-browser'], {
    cwd: path.join(__dirname, '..'),
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[python] ${data.toString().trim()}`);
  });
  pythonProcess.stderr.on('data', (data) => {
    console.error(`[python:err] ${data.toString().trim()}`);
  });
  pythonProcess.on('close', (code) => {
    console.log(`[python] exited with code ${code}`);
    pythonProcess = null;
  });

  return new Promise((resolve, reject) => {
    const maxRetries = 60;
    let attempts = 0;
    const check = () => {
      attempts++;
      http.get(`${SERVER_URL}/api/health`, (res) => {
        if (res.statusCode === 200) {
          console.log('[python] backend ready');
          resolve();
        } else if (attempts < maxRetries) {
          setTimeout(check, 500);
        } else {
          reject(new Error('backend health check failed'));
        }
      }).on('error', () => {
        if (attempts < maxRetries) {
          setTimeout(check, 500);
        } else {
          reject(new Error('backend not reachable after 30s'));
        }
      });
    };
    setTimeout(check, 1000);
  });
}

// ---- Create window ----
async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1000,
    minHeight: 650,
    title: 'QTrade',
    icon: path.join(__dirname, '..', 'qtrade.ico'),
    backgroundColor: '#0B0E14',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.setMenuBarVisibility(false);

  // 清除缓存，确保加载最新 CSS/JS
  await mainWindow.webContents.session.clearCache();
  mainWindow.loadURL(SERVER_URL, { extraHeaders: 'pragma: no-cache\ncache-control: no-cache' });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ---- App lifecycle ----
app.whenReady().then(async () => {
  try {
    await startPythonBackend();
    await createWindow();
  } catch (err) {
    console.error('Failed to start:', err.message);
    app.quit();
  }
});

app.on('window-all-closed', () => {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }
  app.quit();
});

app.on('before-quit', () => {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});
