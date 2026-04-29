import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { appendRowsToGoogleSheet, checkGoogleSheetHealth } from './src/server/appendRowsToGoogleSheet';
import { SHEET_UPLOAD_CONFIG } from './src/server/sheetUploadConfig';
import type { GoogleSheetRow } from './src/server/buildGoogleSheetRows';
import { appendFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

type UploadRequestBody = {
  rows?: GoogleSheetRow[];
  selectedPlatforms?: string[];
  originalFile?: {
    name?: string;
    mimeType?: string;
    size?: number;
    contentBase64?: string;
  };
};

function sanitizeFileName(fileName: string) {
  const illegalChars = new Set(['<', '>', ':', '"', '/', '\\', '|', '?', '*']);
  return Array.from(fileName)
    .map((char) => {
      const code = char.charCodeAt(0);
      if (illegalChars.has(char) || code <= 31) return '_';
      return char;
    })
    .join('');
}

function compactTimestamp(date = new Date()) {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  const hh = String(date.getHours()).padStart(2, '0');
  const mi = String(date.getMinutes()).padStart(2, '0');
  const ss = String(date.getSeconds()).padStart(2, '0');
  return `${yyyy}${mm}${dd}-${hh}${mi}${ss}`;
}

function toDateFolder(date = new Date()) {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function normalizeRemoteIp(ip?: string | null) {
  if (!ip) return '';
  if (ip.startsWith('::ffff:')) return ip.slice(7);
  return ip;
}

function requestIp(req: import('node:http').IncomingMessage) {
  const forwarded = req.headers['x-forwarded-for'];
  if (typeof forwarded === 'string' && forwarded.trim()) {
    return normalizeRemoteIp(forwarded.split(',')[0]?.trim() ?? '');
  }
  return normalizeRemoteIp(req.socket.remoteAddress ?? '');
}

let sheetHealthChecked = false;

function readJsonBody(req: import('node:http').IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => {
      data += String(chunk);
    });
    req.on('end', () => {
      try {
        resolve(JSON.parse(data || '{}'));
      } catch (error) {
        reject(error);
      }
    });
    req.on('error', reject);
  });
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    {
      name: 'sheet-upload-api',
      configureServer(server) {
        if (!sheetHealthChecked) {
          sheetHealthChecked = true;
          void (async () => {
            try {
              const health = await checkGoogleSheetHealth(SHEET_UPLOAD_CONFIG);
              console.info(
                `[sheet-health] OK worksheet=${health.worksheet} headers=${health.headerCount} sample=${health.sampleHeaders.join(
                  ', ',
                )}`,
              );
            } catch (error) {
              console.error(
                `[sheet-health] FAIL ${
                  error instanceof Error ? error.message : '無法連線 Google Sheet'
                }`,
              );
            }
          })();
        }

        server.middlewares.use('/api/sheet-health', async (req, res) => {
          if (req.method !== 'GET') {
            res.statusCode = 405;
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ ok: false, message: 'Method Not Allowed' }));
            return;
          }

          try {
            const health = await checkGoogleSheetHealth(SHEET_UPLOAD_CONFIG);
            res.statusCode = 200;
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify(health));
          } catch (error) {
            res.statusCode = 500;
            res.setHeader('Content-Type', 'application/json');
            res.end(
              JSON.stringify({
                ok: false,
                message: error instanceof Error ? error.message : 'Google Sheet 連線檢查失敗',
              }),
            );
          }
        });

        server.middlewares.use('/api/upload-to-sheet', async (req, res) => {
          if (req.method !== 'POST') {
            res.statusCode = 405;
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ ok: false, message: 'Method Not Allowed' }));
            return;
          }

          try {
            const body = (await readJsonBody(req)) as UploadRequestBody;
            const rows = Array.isArray(body.rows) ? body.rows : [];
            if (!rows.length) {
              res.statusCode = 400;
              res.setHeader('Content-Type', 'application/json');
              res.end(JSON.stringify({ ok: false, message: 'rows 不可為空' }));
              return;
            }

            const filePayload = body.originalFile;
            if (!filePayload?.contentBase64 || !filePayload?.name) {
              res.statusCode = 400;
              res.setHeader('Content-Type', 'application/json');
              res.end(JSON.stringify({ ok: false, message: '缺少原始上傳檔案內容' }));
              return;
            }

            const now = new Date();
            const uploadRoot = resolve(process.cwd(), 'shared', 'uploads');
            const datedFolder = resolve(uploadRoot, toDateFolder(now));
            await mkdir(datedFolder, { recursive: true });
            const safeName = sanitizeFileName(filePayload.name);
            const storedName = `${compactTimestamp(now)}-${safeName}`;
            const storedPath = resolve(datedFolder, storedName);
            const binary = Buffer.from(filePayload.contentBase64, 'base64');
            await writeFile(storedPath, binary);

            await appendRowsToGoogleSheet(rows, SHEET_UPLOAD_CONFIG);

            const logRoot = resolve(process.cwd(), 'shared', 'logs');
            await mkdir(logRoot, { recursive: true });
            const logPath = resolve(logRoot, 'upload-log.jsonl');
            const logLine = JSON.stringify({
              timestamp: now.toISOString(),
              ip: requestIp(req),
              selectedPlatforms: Array.isArray(body.selectedPlatforms) ? body.selectedPlatforms : [],
              uploadedFileName: filePayload.name,
              uploadedFileMimeType: filePayload.mimeType ?? '',
              uploadedFileSize: Number(filePayload.size ?? binary.byteLength),
              storedFilePath: storedPath,
              rowCount: rows.length,
            });
            await appendFile(logPath, `${logLine}\n`, 'utf8');

            res.statusCode = 200;
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ ok: true, appendedCount: rows.length }));
          } catch (error) {
            res.statusCode = 500;
            res.setHeader('Content-Type', 'application/json');
            res.end(
              JSON.stringify({
                ok: false,
                message: error instanceof Error ? error.message : 'Google Sheet 寫入失敗',
              }),
            );
          }
        });
      },
    },
  ],
});
