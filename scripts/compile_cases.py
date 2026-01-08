#!/usr/bin/env python3
"""
ChampSim 바이너리 생성 스크립트 (One-factor-at-a-time)
각 파라미터를 개별적으로 변경하며 바이너리를 생성합니다.
"""

import json
import subprocess
import os
import sys
from pathlib import Path

# 프로젝트 루트 디렉토리
ROOT_DIR = Path(__file__).parent.parent
CONFIG_FILE = ROOT_DIR / "champsim_config.json"
OUTPUT_DIR = ROOT_DIR / "bin_experiments"

# 기본값 (통제 값)
DEFAULTS = {
    "ROB": 32,
    "LQ": 10,
    "SQ": 16,
    "MSHR_L1": 8,
    "MSHR_L2": 16,
    "MSHR_LLC": 32
}

# 각 요소의 값 범위
VARIATIONS = {
    "ROB": [32, 128, 192, 224, 352],
    "LQ": [10, 48, 72, 128],
    "SQ": [16, 36, 42, 56, 72],
    "MSHR_L1": [8, 16, 32],
    "MSHR_L2": [16, 32, 64],
    "MSHR_LLC": [32, 64, 128]
}


def update_config(config, rob, lq, sq, mshr_l1, mshr_l2, mshr_llc):
    """champsim_config.json의 파라미터 값을 업데이트"""
    config["ooo_cpu"][0]["rob_size"] = rob
    config["ooo_cpu"][0]["lq_size"] = lq
    config["ooo_cpu"][0]["sq_size"] = sq
    config["L1D"]["mshr_size"] = mshr_l1
    config["L2C"]["mshr_size"] = mshr_l2
    config["LLC"]["mshr_size"] = mshr_llc
    
    # 바이너리 네이밍
    name = f"ROB{rob}_LQ{lq}_SQ{sq}_M1{mshr_l1}_M2{mshr_l2}_MLLC{mshr_llc}"
    config["executable_name"] = name
    return name


def save_config(config):
    """설정을 champsim_config.json에 저장"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def compile_champsim():
    """ChampSim 컴파일 실행"""
    print("  Compiling...")
    result = subprocess.run(
        ["./config.sh", str(CONFIG_FILE)],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print(f"  ❌ Compilation failed!")
        print(result.stderr)
        return False
    
    # make로 실제 빌드
    result = subprocess.run(
        ["make", "-j"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print(f"  ❌ Make failed!")
        print(result.stderr)
        return False
    
    return True


def move_binary(binary_name):
    """생성된 바이너리를 출력 디렉토리로 이동"""
    src = ROOT_DIR / "bin" / binary_name
    dst = OUTPUT_DIR / binary_name
    
    if src.exists():
        OUTPUT_DIR.mkdir(exist_ok=True)
        subprocess.run(["cp", str(src), str(dst)])
        print(f"  ✓ Binary saved: {dst}")
        return True
    else:
        print(f"  ❌ Binary not found: {src}")
        return False


def main():
    """메인 실행 함수"""
    print("=" * 70)
    print("ChampSim Binary Generation (One-Factor-at-a-Time)")
    print("=" * 70)
    
    # 원본 설정 로드
    with open(CONFIG_FILE, 'r') as f:
        original_config = json.load(f)
    
    generated_binaries = []
    
    # One-factor-at-a-time: 각 요소별로 순회
    for factor, values in VARIATIONS.items():
        print(f"\n[{factor}] Varying {factor} (fixing others at defaults)")
        print("-" * 70)
        
        for value in values:
            # 현재 설정 준비 (기본값 + 현재 요소만 변경)
            config = json.loads(json.dumps(original_config))  # deep copy
            
            params = DEFAULTS.copy()
            params[factor] = value
            
            # 설정 업데이트
            binary_name = update_config(
                config,
                params["ROB"],
                params["LQ"],
                params["SQ"],
                params["MSHR_L1"],
                params["MSHR_L2"],
                params["MSHR_LLC"]
            )
            
            print(f"\n{factor}={value}: {binary_name}")
            
            # 설정 저장
            save_config(config)
            
            # 컴파일
            if not compile_champsim():
                print(f"  Skipping due to compilation error")
                continue
            
            # 바이너리 이동
            if move_binary(binary_name):
                generated_binaries.append(binary_name)
    
    # 원본 설정 복원
    save_config(original_config)
    
    # 결과 출력
    print("\n" + "=" * 70)
    print(f"Generation Complete: {len(generated_binaries)} binaries created")
    print("=" * 70)
    print(f"\nOutput directory: {OUTPUT_DIR}")
    print("\nGenerated binaries:")
    for i, binary in enumerate(generated_binaries, 1):
        print(f"  {i:2d}. {binary}")
    
    print(f"\nTotal: {len(generated_binaries)} binaries")


if __name__ == "__main__":
    main()

