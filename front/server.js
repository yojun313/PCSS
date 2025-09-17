const fix = true;
const express = require('express');
const app = express();
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ limit: '50mb', extended: true }));

const http = require('http');
const { Server } = require('socket.io');
const fs = require('fs');
const path = require('path');
const csv = require('csv-parser');
const { spawn } = require('child_process');
const PYTHON_BIN = path.join(__dirname, '..', 'venv', 'bin', 'python');

const server = http.createServer(app);
const io = new Server(server);
const port = 3000;

// 로그 파일을 저장할 디렉토리
const logDir = path.join(__dirname, 'log');
if (!fs.existsSync(logDir)) {
    fs.mkdirSync(logDir);
}

// 로그 기록 함수
function logError(errorMessage) {
    const now = new Date();
    const timestamp = now.toISOString().replace(/T/, ' ').replace(/\..+/, ''); // YYYY-MM-DD HH:MM:SS 형식
    const filename = `${now.toISOString().slice(0, 19).replace(/:/g, '-').replace('T', '_')}.txt`; // YYYY-MM-DD_HH-MM-SS.txt 형식
    const filePath = path.join(logDir, filename);

    const logEntry = `[${timestamp}] ${errorMessage}\n`;
    fs.appendFileSync(filePath, logEntry, 'utf8'); // 오류 내용을 파일에 추가

    //console.error(logEntry); // 콘솔에도 오류 출력
}

// CSV 파일 경로
const csvFilePath = path.join(__dirname, '..', 'back', 'data', 'conf.csv');
let globalInputData = null; // 단일 사용자용 글로벌 데이터

app.use(express.static(path.join(__dirname, 'public')));
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));


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


// 필요하면 별도 접근용 beta 라우트 유지
app.get('/beta', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'homepage.html'));
});

// GlobalInputData를 JSON으로 반환하는 라우트
app.get('/loading-data', (req, res) => {
    // globalInputData가 없을 때 처리 (필요 시)
    if (!globalInputData) {
        return res.json({ error: 'No input data found.' });
    }
    res.json(globalInputData);
});


app.post('/results', (req, res) => {
    const { isDictionary, data } = req.body;

    if (!data) {
        const errorMsg = '결과 데이터가 비어 있습니다.';
        logError(errorMsg);
        return res.status(400).send(errorMsg);
    }

    let pythonResult;
    if (isDictionary) {
        pythonResult = data;
    } else {
        pythonResult = { error: "Data is not in dictionary format", output: data };
    }

    // globalInputData에 저장된 옵션을 results.ejs에 전달
    res.render('results', { pythonResult, options: globalInputData, error: null });
});

const logRequest = require('./logRequest');
app.post('/submit', async (req, res) => {
    const ip = req.headers['x-forwarded-for'] || req.socket.remoteAddress;
    const body = req.body;

    await logRequest(ip, '/submit', body);

    globalInputData = body;
    res.sendFile(path.join(__dirname, 'public', 'loading.html'));
});

io.on('connection', (socket) => {
  socket.on('start-python', () => {
    let pythonOutput = '';

    // 스크립트/작업 디렉토리 경로
    const scriptPath = path.join(__dirname, '..', 'back', 'pcss_web.py');
    const cwdPath    = path.join(__dirname, '..', 'back');

    // 가상환경 파이썬으로 실행
    const pythonProcess = spawn(PYTHON_BIN, ['-u', scriptPath, JSON.stringify(globalInputData || {})], {
      cwd: cwdPath,
      env: { ...process.env }, // 필요 시 venv 전용 ENV 추가 가능
    });

    // (강력 추천) 시작 실패 캐치
    pythonProcess.on('error', (err) => {
      const msg = `[spawn error] ${err.message}`;
      logError(msg);
      socket.emit('redirect_to_results', { isDictionary: false, data: msg });
    });

    pythonProcess.stdout.on('data', (data) => {
      pythonOutput += data.toString();
      socket.emit('python_output', data.toString());
    });

    pythonProcess.stderr.on('data', (data) => {
      const errorMsg = `Python stderr: ${data}`;
      logError(errorMsg);
      pythonOutput += data.toString();
      socket.emit('python_error', data.toString());
    });

    pythonProcess.on('close', (code) => {
      try {
        const pathStartIndex = pythonOutput.indexOf('PATH=');
        if (pathStartIndex !== -1) {
          const pathEndIndex = pythonOutput.indexOf('\n', pathStartIndex);
          const jsonPath = pythonOutput.slice(
            pathStartIndex + 5,
            pathEndIndex !== -1 ? pathEndIndex : undefined
          ).trim();

          if (jsonPath === 'ERROR') {
            const errorMsg = 'Python 실행 중 오류 발생';
            logError(errorMsg);
            socket.emit('redirect_to_results', { isDictionary: false, data: errorMsg });
            return;
          }

          fs.readFile(jsonPath, 'utf-8', (err, fileData) => {
            if (err) {
              const errorMsg = `JSON 파일 읽기 오류: ${err.message}`;
              logError(errorMsg);
              socket.emit('redirect_to_results', { isDictionary: false, data: errorMsg });
              return;
            }
            try {
              const finalOutput = JSON.parse(fileData);
              socket.emit('redirect_to_results', { isDictionary: true, data: finalOutput });
              fs.unlink(jsonPath, (unlinkErr) => {
                if (unlinkErr) logError(`JSON 파일 삭제 오류: ${unlinkErr.message}`);
              });
            } catch (parseErr) {
              const errorMsg = `JSON 파싱 오류: ${parseErr.message}`;
              logError(errorMsg);
              socket.emit('redirect_to_results', { isDictionary: false, data: errorMsg });
            }
          });
        } else {
          // 기존 코드 버그: errorMsg 미정의 → 고정 메시지로 대체
          const msg = 'Python 출력에서 PATH=를 찾지 못했습니다. (pcss_web.py가 JSON 경로를 출력하지 않음)';
          logError(msg);
          socket.emit('redirect_to_results', { isDictionary: false, data: msg });
        }
      } catch (err) {
        logError(`후처리 오류: ${err.message}`);
        socket.emit('redirect_to_results', { isDictionary: false, data: pythonOutput.trim() });
      }
    });

    socket.on('disconnect', () => {
      pythonProcess.kill();
    });
  });
});


server.listen(port, () => {
    console.log(`서버 실행 중: http://localhost:${port}`);
});
