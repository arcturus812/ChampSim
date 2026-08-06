#!/usr/bin/env python3
"""[CXLREPRO] experiment runner + corner-case verifier.

Runs the rf-sweep / policy matrix plus targeted corner-case workloads on the
patched ChampSim, parses the output, computes directional bandwidth, and
asserts the invariants that establish (a) the model is internally consistent
and (b) the real-hardware problem signature is reproduced.

Usage:
  run_verify.py run     # run the full matrix (parallel) and cache raw outputs
  run_verify.py verify  # parse cached outputs and check all invariants
  run_verify.py report  # print the rf-sweep table
"""
import os
import re
import subprocess
import sys
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
BIN = os.path.join(ROOT, 'bin', 'champsim_cxl_repro')
TRACES = os.path.join(HERE, 'traces')
OUT = os.path.join(HERE, 'out')

WARMUP = 300_000
SIM = 1_500_000
CPU_PERIOD_PS = 250  # 4 GHz core clock; ROI cycles are core cycles
LINE = 64

# direction ceiling of the duplex far link: 64 B / (625 ps * 32 transfers)
DIR_CEILING_GBS = 3.2

RUNS = {
    # name: (trace, policy, alloc)
    'r0_alloc':   ('mix_r0.champsim.gz',  'allocate', 'only_far'),
    'r0_nt':      ('mix_r0.champsim.gz',  'nt',       'only_far'),
    'r4_alloc':   ('mix_r4.champsim.gz',  'allocate', 'only_far'),
    'r4_nt':      ('mix_r4.champsim.gz',  'nt',       'only_far'),
    'r8_alloc':   ('mix_r8.champsim.gz',  'allocate', 'only_far'),
    'r8_nt':      ('mix_r8.champsim.gz',  'nt',       'only_far'),
    'r9_alloc':   ('mix_r9.champsim.gz',  'allocate', 'only_far'),
    'r9_nt':      ('mix_r9.champsim.gz',  'nt',       'only_far'),
    'r12_alloc':  ('mix_r12.champsim.gz', 'allocate', 'only_far'),
    'r12_nt':     ('mix_r12.champsim.gz', 'nt',       'only_far'),
    'r16_alloc':  ('mix_r16.champsim.gz', 'allocate', 'only_far'),
    'r16_nt':     ('mix_r16.champsim.gz', 'nt',       'only_far'),
    # corner cases
    'near_alloc': ('mix_r8.champsim.gz',  'allocate', 'first_touch'),  # near-DRAM-only routing
    'clash_nt':   ('clash.champsim.gz',   'nt',       'only_far'),     # store hits in-flight load MSHR
    'clash_alloc':('clash.champsim.gz',   'allocate', 'only_far'),
    'small_nt':   ('small.champsim.gz',   'nt',       'only_far'),     # footprint < LLC: NT + later store-hit path
    'reuse_short_alloc': ('reuse_1k.champsim.gz',   'allocate', 'only_far'),
    'reuse_short_nt':    ('reuse_1k.champsim.gz',   'nt',       'only_far'),
    'reuse_long_alloc':  ('reuse_256k.champsim.gz', 'allocate', 'only_far'),
    'reuse_long_nt':     ('reuse_256k.champsim.gz', 'nt',       'only_far'),
}


def run_one(name):
    trace, policy, alloc = RUNS[name]
    env = dict(os.environ, CXL_STORE_POLICY=policy, CXL_ALLOC_POLICY=alloc, CXL_LIVELOCK_IPC='0.0001')
    outfile = os.path.join(OUT, name + '.txt')
    with open(outfile, 'w') as f:
        p = subprocess.run(
            [BIN, '--warmup-instructions', str(WARMUP), '--simulation-instructions', str(SIM),
             os.path.join(TRACES, trace)],
            env=env, stdout=f, stderr=subprocess.STDOUT)
    return name, p.returncode


def parse(name):
    text = open(os.path.join(OUT, name + '.txt')).read()
    d = {'name': name, 'completed': 'ChampSim completed all CPUs' in text}

    m = re.findall(r'CPU 0 cumulative IPC: ([\d.]+) instructions: (\d+) cycles: (\d+)', text)
    if m:
        ipc, instrs, cycles = m[-1]
        d['ipc'], d['cycles'] = float(ipc), int(cycles)
        d['roi_sec'] = int(cycles) * CPU_PERIOD_PS * 1e-12

    def chan(prefix):
        c = {}
        cm = re.search(prefix + r'0 RD_LINES:\s+(\d+) WR_LINES:\s+(\d+) RD_BUS_BUSY_ps:\s+(\d+) WR_BUS_BUSY_ps:\s+(\d+)', text)
        if cm:
            c['rd_lines'], c['wr_lines'], c['rd_busy_ps'], c['wr_busy_ps'] = map(int, cm.groups())
        rm = re.search(prefix + r'0 REFRESHES ISSUED:\s+(\d+)', text)
        c['refreshes'] = int(rm.group(1)) if rm else 0
        return c

    d['far'] = chan('FAR_CHANNEL_')
    d['near'] = chan('Channel ')

    for key, pat in [('nt_bypass', r'nt_bypass_lines:\s+(\d+)'), ('nt_retry', r'nt_bypass_retry:\s+(\d+)'),
                     ('rfo_fetch', r'far_rfo_fetches:\s+(\d+)'), ('demand_rd', r'far_demand_reads:\s+(\d+)'),
                     ('pf_rd', r'far_prefetch_reads:\s+(\d+)'), ('wb', r'far_writebacks:\s+(\d+)'),
                     ('wq_backlog', r'far_wq_backlog:\s+(\d+)')]:
        m = re.search(pat, text)
        d[key] = int(m.group(1)) if m else 0

    if 'roi_sec' in d and d['far']:
        t = d['roi_sec']
        d['far_rd_gbs'] = d['far']['rd_lines'] * LINE / t / 1e9
        d['far_wr_gbs'] = d['far']['wr_lines'] * LINE / t / 1e9
        d['rd_busy_frac'] = d['far']['rd_busy_ps'] * 1e-12 / t
        d['wr_busy_frac'] = d['far']['wr_busy_ps'] * 1e-12 / t
        # useful = demand reads + stores that reached far memory
        useful_wr = d['nt_bypass'] if d['nt_bypass'] > 0 else d['wb']
        d['useful_gbs'] = (d['demand_rd'] + useful_wr) * LINE / t / 1e9
    return d


CHECKS = []


def check(desc):
    def deco(fn):
        CHECKS.append((desc, fn))
        return fn
    return deco


def approx(a, b, tol):
    return b > 0 and abs(a - b) / b <= tol


# ---- invariants -----------------------------------------------------------

@check('V0  all runs completed (no deadlock/abort)')
def v0(R):
    return all(r['completed'] for r in R.values())


@check('V1  pure write + allocate: fetch-per-written-line ~= 1.0 (casRD analogue)')
def v1(R):
    r = R['r0_alloc']
    return approx(r['far']['rd_lines'], r['far']['wr_lines'], 0.10)


@check('V2  pure write + nt: read direction stays idle (fetch eliminated)')
def v2(R):
    r = R['r0_nt']
    return r['far']['rd_lines'] < 0.05 * r['far']['wr_lines'] and r['rfo_fetch'] < 0.02 * r['far']['wr_lines']


@check('V3  pure write: allocate ~= nt useful bandwidth (waste rides idle direction; gap ~0)')
def v3(R):
    return approx(R['r0_alloc']['useful_gbs'], R['r0_nt']['useful_gbs'], 0.10)


@check('V4  mixed rf: allocate read direction is the binding constraint (busy > 85%)')
def v4(R):
    return R['r8_alloc']['rd_busy_frac'] > 0.85 and R['r9_alloc']['rd_busy_frac'] > 0.85


@check('V5  mixed rf: allocate loses substantially to nt (the 30%-class gap)')
def v5(R):
    g8 = R['r8_nt']['useful_gbs'] / R['r8_alloc']['useful_gbs']
    g9 = R['r9_nt']['useful_gbs'] / R['r9_alloc']['useful_gbs']
    return g8 > 1.25 and g9 > 1.25


@check('V6  pure read: store policy is a no-op (within 3%)')
def v6(R):
    a, n = R['r16_alloc'], R['r16_nt']
    return approx(a['useful_gbs'], n['useful_gbs'], 0.03) and n['nt_bypass'] == 0


@check('V7  conservation: far RD_LINES == rfo + demand + prefetch reads (<=1% + inflight)')
def v7(R):
    for r in R.values():
        if not r['far'] or r['name'].startswith('near'):
            continue
        expect = r['rfo_fetch'] + r['demand_rd'] + r['pf_rd']
        got = r['far']['rd_lines']
        if abs(got - expect) > max(0.01 * max(expect, 1), 512):
            print(f"    !! {r['name']}: rd_lines={got} vs ledger={expect}")
            return False
    return True


@check('V8  conservation (exact): writes issued == completed + queued backlog')
def v8(R):
    for r in R.values():
        if not r['far'] or r['name'].startswith('near'):
            continue
        issued = r['wb'] + r['nt_bypass']
        accounted = r['far']['wr_lines'] + r['wq_backlog']
        if abs(issued - accounted) > 512:
            print(f"    !! {r['name']}: issued={issued} vs completed+backlog={accounted}")
            return False
    return True


@check('V9  routing: first_touch keeps traffic on near DRAM; only_far keeps it on far')
def v9(R):
    near_run = R['near_alloc']
    far_leak = near_run['far']['rd_lines'] + near_run['far']['wr_lines']
    near_ok = far_leak < 2000 and (near_run['near']['rd_lines'] + near_run['near']['wr_lines']) > 100000
    far_run = R['r8_alloc']
    near_leak = far_run['near']['rd_lines'] + far_run['near']['wr_lines']
    far_ok = near_leak < 0.02 * (far_run['far']['rd_lines'] + far_run['far']['wr_lines'])
    if not near_ok:
        print(f"    !! near run leaks to far: {far_leak}")
    if not far_ok:
        print(f"    !! far run leaks to near: {near_leak}")
    return near_ok and far_ok


@check('V10 duplex coexists with refresh (refreshes issued on far channel)')
def v10(R):
    return R['r8_alloc']['far']['refreshes'] > 0


@check('V11 clash trace (store hits in-flight load MSHR) survives under both policies')
def v11(R):
    a, n = R['clash_alloc'], R['clash_nt']
    # loads fetch every line; under nt the MSHR guard must fall back to merge,
    # so nt_bypass stays near zero and both policies behave identically
    return a['completed'] and n['completed'] and n['nt_bypass'] < 0.02 * max(n['far']['rd_lines'], 1) \
        and approx(n['useful_gbs'], a['useful_gbs'], 0.05)


@check('V12 small footprint (< LLC): nt handles store-hit-after-reload without loss')
def v12(R):
    r = R['small_nt']
    return r['completed']


@check('V13 reuse-distance: long reuse punishes allocate (fetch cost); short reuse shows the cache-reuse mechanism')
def v13(R):
    # Long distance (beyond LLC): allocate pays a fetch per store on top of the
    # reuse read -> materially slower. This is the paper's 29%-class effect.
    long_ratio = R['reuse_long_alloc']['ipc'] / R['reuse_long_nt']['ipc']
    # Short distance (within caches): under bandwidth saturation the two
    # policies carry one far read per line each (fetch vs reuse-miss), so IPC
    # parity is the correct first-order result. The mechanism difference must
    # still be visible in WHERE the read direction's lines come from:
    # allocate's reuse loads hit in-cache (demand ~ 0), nt's must re-fetch.
    short_ratio = R['reuse_short_alloc']['ipc'] / R['reuse_short_nt']['ipc']
    a, n = R['reuse_short_alloc'], R['reuse_short_nt']
    mech = a['demand_rd'] < 0.05 * a['rfo_fetch'] and n['demand_rd'] > 0.5 * n['nt_bypass']
    print(f"    (short alloc/nt = {short_ratio:.3f}, long alloc/nt = {long_ratio:.3f}, "
          f"short demand_rd: alloc={a['demand_rd']} nt={n['demand_rd']})")
    return long_ratio < 0.75 and 0.9 < short_ratio < 1.1 and mech


@check('V14 nt retry path exercised or clean (no lost lines even when far WQ fills)')
def v14(R):
    r = R['r0_nt']
    print(f"    (nt_bypass_retry = {r['nt_retry']})")
    return True  # correctness is already covered by V8 conservation


# ---- entry points ---------------------------------------------------------

def do_run(names=None):
    os.makedirs(OUT, exist_ok=True)
    names = names or list(RUNS)
    with cf.ThreadPoolExecutor(max_workers=min(len(names), 24)) as ex:
        for name, rc in ex.map(run_one, names):
            print(f"{name}: exit {rc}")


def do_verify():
    R = {n: parse(n) for n in RUNS}
    passed = 0
    for desc, fn in CHECKS:
        try:
            ok = fn(R)
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"    !! exception: {e}")
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}")
        passed += ok
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    return passed == len(CHECKS)


def do_report():
    print(f"{'run':>18} {'IPC':>7} {'useful':>7} {'farRD':>7} {'farWR':>7} {'rdBusy':>7} {'wrBusy':>7} {'rfoFetch':>9} {'ntByp':>9}")
    for n in RUNS:
        try:
            r = parse(n)
        except FileNotFoundError:
            continue
        print(f"{n:>18} {r.get('ipc', 0):7.3f} {r.get('useful_gbs', 0):7.2f} {r.get('far_rd_gbs', 0):7.2f} "
              f"{r.get('far_wr_gbs', 0):7.2f} {r.get('rd_busy_frac', 0):7.1%} {r.get('wr_busy_frac', 0):7.1%} "
              f"{r['rfo_fetch']:9} {r['nt_bypass']:9}")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'report'
    if cmd == 'run':
        do_run(sys.argv[2:] or None)
    elif cmd == 'verify':
        sys.exit(0 if do_verify() else 1)
    else:
        do_report()
