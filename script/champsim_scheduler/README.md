# ChampSim Scheduler with TUI Controller

ChampSim 작업을 관리하는 스케줄러와 터미널 기반 사용자 인터페이스입니다.

## 기능

- **실시간 작업 모니터링**: 실행 중인 작업과 대기 중인 작업을 실시간으로 표시
- **작업 제어**: 개별 작업 또는 범위로 작업 중단/일시정지/재개
- **우선순위 기반 스케줄링**: 시뮬레이션 시간을 기반으로 한 우선순위 큐
- **리소스 모니터링**: 메모리 사용량에 따른 자동 작업 관리
- **클라이언트-서버 구조**: 이미 실행 중인 서비스에 연결하여 제어 가능

## 설치

```bash
pip install -r requirements.txt
```

## 사용법

### 1. 서비스 모드로 실행 (기본)

```bash
python run_tui.py
```

### 2. 독립 실행 모드

```bash
python run_tui.py --no-tui
```

### 3. 클라이언트 모드 (이미 실행 중인 서비스에 연결)

```bash
python tui_client.py
```

### 4. 커스텀 설정으로 실행

```bash
# 서비스 실행
python run_tui.py --cores 32 --memory 64 --pause-threshold 0.15 --kill-threshold 0.05

# 클라이언트로 연결
python tui_client.py --host localhost --port 5555
```

## 실행 모드 비교

### 서비스 모드 (`run_tui.py`)
- 스케줄러와 TUI가 같은 프로세스에서 실행
- 새로운 스케줄러 인스턴스 생성
- 독립적인 환경에서 실행

### 클라이언트 모드 (`tui_client.py`)
- 이미 실행 중인 스케줄러 서비스에 연결
- 네트워크를 통한 원격 제어
- 여러 클라이언트가 동시에 연결 가능

## TUI 명령어

### 기본 명령어
- `h` 또는 `help`: 도움말 표시
- `q` 또는 `quit`: TUI 종료
- `c` 또는 `clear`: 화면 지우기
- `status`: 상세 상태 정보 표시
- `r` 또는 `reconnect`: 서비스에 재연결 (클라이언트 모드)

### 작업 제어 명령어
- `kill <job_id>`: 특정 작업 중단
- `kill <start>-<end>`: 범위의 작업 중단 (예: `kill 1-5`)
- `pause <job_id>`: 특정 작업 일시정지
- `resume <job_id>`: 일시정지된 작업 재개

### 예시
```
kill 3          # 작업 #3 중단
kill 1-5        # 작업 #1부터 #5까지 중단
pause 2         # 작업 #2 일시정지
resume 2        # 작업 #2 재개
```

## 인터페이스 구성

### 실행 중인 작업 테이블
- ID: 작업 일련번호
- Workload: 워크로드 이름
- Status: 작업 상태 (running, paused, completed, killed)
- Progress: 진행률
- Start Time: 시작 시간

### 대기 중인 작업 테이블
- 큐에 대기 중인 작업들의 목록
- 우선순위 순으로 정렬

### 연결 상태 표시 (클라이언트 모드)
- 서비스 연결 상태 실시간 표시
- 연결 끊김 시 자동 재연결 시도

## 설정 파일

### simulation_time.txt
워크로드별 예상 시뮬레이션 시간을 설정할 수 있습니다.

```
605.mcf_s-782B: 120.5
607.cactuBSSN_s-2421B: 85.2
619.lbm_s-2676B: 95.8
```

## 시스템 요구사항

- Python 3.7+
- rich 라이브러리 (TUI용)
- psutil 라이브러리 (리소스 모니터링용)

## 문제 해결

### rich 라이브러리 설치 오류
```bash
pip install rich
```

### 권한 오류
```bash
chmod +x run_tui.py
chmod +x tui_client.py
```

### 포트 충돌
다른 포트를 사용하세요:
```bash
# 서비스 실행
python run_tui.py --port 5556

# 클라이언트 연결
python tui_client.py --port 5556
```

### 서비스 연결 실패
1. 서비스가 실행 중인지 확인
2. 포트 번호가 올바른지 확인
3. 방화벽 설정 확인

### 클라이언트 연결 문제
```bash
# 연결 상태 확인
telnet localhost 5555

# 서비스 재시작
pkill -f "python.*run_tui.py"
python run_tui.py
```

## 고급 사용법

### 여러 클라이언트 동시 연결
```bash
# 터미널 1에서 서비스 실행
python run_tui.py

# 터미널 2에서 클라이언트 1
python tui_client.py

# 터미널 3에서 클라이언트 2
python tui_client.py
```

### 원격 서버 연결
```bash
# 원격 서버의 스케줄러에 연결
python tui_client.py --host 192.168.1.100 --port 5555
```

### 자동 재연결
클라이언트는 연결이 끊어지면 자동으로 재연결을 시도합니다.
`r` 명령어로 수동 재연결도 가능합니다. 