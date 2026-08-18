/**
 * 사내망처럼 PC와 폰이 다른 대역에 있을 때 쓰는 실행 스크립트.
 *
 *   npm run tunnel
 *
 * 하는 일
 *   1) cloudflared 로 백엔드(8000)를 외부 주소로 노출
 *   2) 그 주소를 .env 의 EXPO_PUBLIC_API_URL 에 자동 기록
 *   3) expo start --tunnel 실행
 *
 * 터널 주소는 실행할 때마다 바뀐다. 손으로 옮겨 적는 대신 여기서 자동으로 맞춘다.
 */
import { spawn } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const ENV_PATH = join(ROOT, '.env');
const API_PORT = process.env.API_PORT ?? '8000';
const EXPO_PORT = process.env.EXPO_PORT ?? '19000';
const URL_RE = /https:\/\/[a-z0-9-]+\.trycloudflare\.com/i;

const log = (msg) => console.log(`\x1b[36m[tunnel]\x1b[0m ${msg}`);
const warn = (msg) => console.log(`\x1b[33m[tunnel]\x1b[0m ${msg}`);

function setEnv(key, value) {
  const text = existsSync(ENV_PATH) ? readFileSync(ENV_PATH, 'utf8') : '';
  const line = `${key}=${value}`;
  let replaced = false;

  // 줄 단위로 처리한다. 정규식 치환만 쓰면 이미 여러 줄이 있을 때
  // 첫 줄만 바뀌고 나머지가 남아, .env 는 '마지막 값이 이기므로' 죽은 주소가 쓰인다.
  const lines = text.split(/\r?\n/).filter((l) => {
    if (!l.startsWith(`${key}=`)) return true;
    if (replaced) return false;      // 중복 줄은 버린다
    replaced = true;
    return true;
  }).map((l) => (l.startsWith(`${key}=`) ? line : l));

  if (!replaced) lines.push(line);
  writeFileSync(ENV_PATH, `${lines.join('\n').trimEnd()}\n`, 'utf8');
}

function startExpo() {
  log('Expo 터널을 시작합니다. QR 코드가 뜨면 Expo Go 로 스캔하세요.');
  // 포트를 고정한다. Expo 기본값 8081 은 Windows 의 예약 포트 구간(8075~8174)에
  // 걸려 바인딩이 거부된다 — 'Port 8081 is reserved by the OS' 로 죽는다.
  const expo = spawn('npx', ['expo', 'start', '--tunnel', '--port', EXPO_PORT], {
    cwd: ROOT, stdio: 'inherit', shell: true,
  });
  expo.on('exit', (code) => {
    cloudflared?.kill();
    process.exit(code ?? 0);
  });
  return expo;
}

let cloudflared = null;

function main() {
  log(`백엔드(localhost:${API_PORT}) 터널을 엽니다…`);
  cloudflared = spawn('cloudflared', ['tunnel', '--url', `http://localhost:${API_PORT}`], {
    shell: true,
  });

  let resolved = false;
  const onChunk = (buf) => {
    const text = buf.toString();
    const found = text.match(URL_RE);
    if (found && !resolved) {
      resolved = true;
      const url = found[0];
      setEnv('EXPO_PUBLIC_API_URL', url);
      log(`백엔드 주소: ${url}`);
      log('.env 에 기록했습니다.');
      startExpo();
    }
  };
  cloudflared.stdout.on('data', onChunk);
  cloudflared.stderr.on('data', onChunk);   // cloudflared 는 주소를 stderr 로도 낸다

  cloudflared.on('error', (err) => {
    warn(`cloudflared 를 실행할 수 없습니다: ${err.message}`);
    warn('설치: winget install --id Cloudflare.cloudflared');
    warn('백엔드 없이 목 모드로 앱만 띄웁니다.');
    setEnv('EXPO_PUBLIC_API_URL', '');
    startExpo();
  });

  // 주소가 안 나오면 무한 대기하지 않는다
  setTimeout(() => {
    if (!resolved) {
      warn('터널 주소를 받지 못했습니다. 목 모드로 진행합니다.');
      setEnv('EXPO_PUBLIC_API_URL', '');
      startExpo();
    }
  }, 30_000);
}

process.on('SIGINT', () => { cloudflared?.kill(); process.exit(0); });
main();
