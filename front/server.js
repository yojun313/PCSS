const fix = false;

const express = require('express');
const app = express();

app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ limit: '50mb', extended: true }));

const fs = require('fs');
const path = require('path');
const csv = require('csv-parser');

// (선택) FastAPI로 프록시하려면 필요
// Node 18+면 fetch 내장. Node 16 이하면 node-fetch 설치 필요.
// const fetch = global.fetch || require('node-fetch');

const port = 3000;

// 로그 파일을 저장할 디렉토리
const logDir = path.join(__dirname, 'log');
if (!fs.existsSync(logDir)) {
  fs.mkdirSync(logDir);
}

// 로그 기록 함수
function logError(errorMessage) {
  const now = new Date();
  const timestamp = now.toISOString().replace(/T/, ' ').replace(/\..+/, '');
  const filename = `${now.toISOString().slice(0, 19).replace(/:/g, '-').replace('T', '_')}.txt`;
  const filePath = path.join(logDir, filename);

  const logEntry = `[${timestamp}] ${errorMessage}\n`;
  fs.appendFileSync(filePath, logEntry, 'utf8');
}

// CSV 파일 경로
const csvFilePath = path.join(__dirname, '..', 'back', 'data', 'conf.csv');

// 단일 사용자용 글로벌 데이터(기존 그대로)
let globalInputData = null;

// 정적/뷰 설정
app.use(express.static(path.join(__dirname, 'public')));
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

// pages
app.get('/loading', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'loading.html'));
});

app.get('/conferences', (req, res) => {
  const data = [];
  fs.createReadStream(csvFilePath)
    .pipe(csv())
    .on('data', (row) => data.push(row))
    .on('end', () => res.json(data))
    .on('error', (err) => logError(`CSV 파일 읽기 오류: ${err.message}`));
});

app.get('/', (req, res) => {
  if (fix) {
    res.sendFile(path.join(__dirname, 'public', 'fix.html'));
  } else {
    res.sendFile(path.join(__dirname, 'public', 'homepage.html'));
  }
});

app.get('/beta', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'homepage.html'));
});

// GlobalInputData를 JSON으로 반환
app.get('/loading-data', (req, res) => {
  if (!globalInputData) return res.json({ error: 'No input data found.' });
  res.json(globalInputData);
});

// 결과 페이지 렌더 (기존 유지)
app.post('/results', (req, res) => {
  const { isDictionary, data } = req.body;

  if (!data) {
    const errorMsg = '결과 데이터가 비어 있습니다.';
    logError(errorMsg);
    return res.status(400).send(errorMsg);
  }

  let pythonResult;
  if (isDictionary) pythonResult = data;
  else pythonResult = { error: "Data is not in dictionary format", output: data };

  res.render('results', { pythonResult, options: globalInputData, error: null });
});

// author-stats 렌더(기존 유지)
app.post('/author-stats', (req, res) => {
  let { name, url, stats, total, papers } = req.body;

  let statArray = [];
  try {
    if (Array.isArray(stats)) {
      statArray = stats.map(Number);
    } else if (typeof stats === 'string') {
      if (stats.trim().startsWith('[')) statArray = JSON.parse(stats).map(Number);
      else statArray = stats.replace(/[()"']/g, '').split(',').map(s => Number(s.trim()));
    }
  } catch (e) {
    statArray = [];
  }

  try {
    if (typeof papers === 'string') papers = JSON.parse(papers);
  } catch (e) {
    papers = [];
  }

  res.render('author-stats', {
    name: name || '',
    url: url || '#',
    total: Number(total) || 0,
    stats: statArray,
    papers: papers || []
  });
});

// 요청 로그 + submit (기존 유지)
const logRequest = require('./logRequest');
app.post('/submit', async (req, res) => {
  const ip = req.headers['x-forwarded-for'] || req.socket.remoteAddress;
  const body = req.body;

  try {
    await logRequest(ip, '/submit', body);
  } catch (e) {
    logError(`logRequest error: ${e?.message || e}`);
  }

  globalInputData = body;
  res.sendFile(path.join(__dirname, 'public', 'loading.html'));
});

/**
 * (선택) FastAPI로 프록시가 필요하면 사용:
 * - 프론트에서 FastAPI_BASE를 몰라도 /api/...로 호출하게 만들 수 있음
 * - SSE(EventSource)는 프록시에서 처리 까다롭고(스트리밍 유지), 보통은 브라우저가 FastAPI에 직접 붙게 하는게 편함.
 *
 * 아래는 "start/result/author-stats" 같은 일반 JSON 요청만 프록시하는 예시.
 */

// const FASTAPI_BASE = 'http://pcss.r-e.kr:8000';
//
// app.post('/api/search/start', async (req, res) => {
//   try {
//     const r = await fetch(`${FASTAPI_BASE}/api/search/start`, {
//       method: 'POST',
//       headers: { 'Content-Type': 'application/json' },
//       body: JSON.stringify(req.body)
//     });
//     const text = await r.text();
//     res.status(r.status).send(text);
//   } catch (e) {
//     logError(`proxy /api/search/start error: ${e?.message || e}`);
//     res.status(502).json({ error: 'Bad Gateway', detail: String(e?.message || e) });
//   }
// });
//
// app.get('/api/search/result/:job_id', async (req, res) => {
//   try {
//     const r = await fetch(`${FASTAPI_BASE}/api/search/result/${encodeURIComponent(req.params.job_id)}`);
//     const text = await r.text();
//     res.status(r.status).send(text);
//   } catch (e) {
//     logError(`proxy /api/search/result error: ${e?.message || e}`);
//     res.status(502).json({ error: 'Bad Gateway', detail: String(e?.message || e) });
//   }
// });

app.listen(port, () => {
  console.log(`서버 실행 중: http://localhost:${port}`);
});
