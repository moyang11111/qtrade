'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');

const {
  findPython,
  resolveRuntimePaths,
  startBackend,
} = require('../runtime');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const SYMBOL = '000001';

function writeCsv(dataDir) {
  fs.mkdirSync(dataDir, { recursive: true });
  const rows = ['date,open,high,low,close,volume'];
  for (let day = 0; day < 60; day += 1) {
    const date = new Date(Date.UTC(2024, 0, day + 1)).toISOString().slice(0, 10);
    const close = (10 + day / 100).toFixed(2);
    rows.push(`${date},${(Number(close) - 0.05).toFixed(2)},${(Number(close) + 0.1).toFixed(2)},${(Number(close) - 0.15).toFixed(2)},${close},${100000 + day * 100}`);
  }
  fs.writeFileSync(path.join(dataDir, `${SYMBOL}.csv`), `${rows.join('\n')}\n`, 'utf8');
}

function getText(url) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, { agent: false }, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => {
        if (response.statusCode !== 200) {
          reject(new Error(`${url} returned HTTP ${response.statusCode}: ${body}`));
          return;
        }
        resolve(body);
      });
    });
    request.setTimeout(5_000, () => request.destroy(new Error('request timed out')));
    request.on('error', reject);
  });
}

async function main() {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'qtrade-electron-smoke-'));
  let backend;
  try {
    const dataDir = path.join(tempRoot, 'data', 'cache');
    writeCsv(dataDir);
    const env = {
      ...process.env,
      QTRADE_NO_HARNESS: '1',
      QTRADE_NO_AUTOUPDATE: '1',
      QTRADE_BASE_DIR: path.join(tempRoot, 'missing-base'),
    };
    if (process.argv[2]) env.QTRADE_PYTHON = process.argv[2];

    const python = findPython({ env });
    backend = await startBackend({
      paths: resolveRuntimePaths({ rootOverride: PROJECT_ROOT }),
      python,
      env,
      cwd: tempRoot,
      dataDir,
      csvOnly: true,
    });

    const root = await getText(`${backend.url}/`);
    if (!root.includes('QTrade')) throw new Error('QTrade static page marker was missing');
    const health = JSON.parse(await getText(`${backend.url}/api/health`));
    if (health.status !== 'ok' || health.mode !== 'csv' || health.symbols < 1) {
      throw new Error(`unexpected QTrade health payload: ${JSON.stringify(health)}`);
    }
    const symbols = JSON.parse(await getText(`${backend.url}/api/symbols`));
    if (!symbols.includes(SYMBOL)) throw new Error(`symbol ${SYMBOL} was not returned`);
    const kline = JSON.parse(await getText(`${backend.url}/api/kline/${SYMBOL}?limit=3`));
    if (kline.length !== 3) throw new Error(`expected 3 K-line rows, got ${kline.length}`);
    console.log(`QTrade backend smoke passed on ${backend.url}`);
  } finally {
    if (backend) await backend.stop();
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
