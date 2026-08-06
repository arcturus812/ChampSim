#!/usr/bin/env python3
"""[CXLELIDE] verifier for the local-ownership-completion policy.

The policy under test: CXL_STORE_POLICY=elide grants a far store's ownership at
the LLC without issuing any far transaction; only the writeback crosses the
link. It is the simulator counterfactual of the paper's proposal, and its
predicted effects are exact:

  - every far RFO becomes a local grant           (E2)
  - the read direction loses exactly the fetches  (E3)
  - the write direction is untouched              (E4)
  - the [CXLTX] slot invariant still holds        (E5)
  - transactions per store line drop to ~1, so under a binding budget elide
    matches nt's throughput on a no-reuse mix     (E6)
  - unlike nt it keeps residency, so with write-to-read reuse it beats nt
    by not re-fetching what it just wrote         (E7)
  - the latency channel: IPC(elide) > IPC(alloc) even with no budget (E8)

E1 (inertness with the knob unset) is delegated to the existing suites, which
run with the policy off and must stay green.

Usage: run_verify_elide.py run | verify | report
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys

import run_verify as rv

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out_elide')
PLAN = os.path.join(OUT, '_plan.json')
os.makedirs(OUT, exist_ok=True)
rv.OUT = OUT

MIX = 'mix_r8.champsim.gz'        # rf=0.5, no reuse: the accounting testbed
REUSE = 'reuse_1k.champsim.gz'    # short write-to-reread distance: the residency testbed


def run_one(name, trace, policy, period_ps):
    env = dict(os.environ, CXL_STORE_POLICY=policy, CXL_ALLOC_POLICY='only_far',
               CXL_LIVELOCK_IPC='0.0001')
    if period_ps:
        env['CXL_TX_PERIOD_PS'] = str(period_ps)
    else:
        env.pop('CXL_TX_PERIOD_PS', None)
    with open(os.path.join(OUT, name + '.txt'), 'w') as f:
        p = subprocess.run(
            [rv.BIN, '--warmup-instructions', str(rv.WARMUP),
             '--simulation-instructions', str(rv.SIM), os.path.join(rv.TRACES, trace)],
            env=env, stdout=f, stderr=subprocess.STDOUT)
    return name, p.returncode


def stats(name):
    d = rv.parse(name)
    text = open(os.path.join(OUT, name + '.txt')).read()
    m = re.search(r'CXLELIDE elided_grants:\s+(\d+)', text)
    d['elided'] = int(m.group(1)) if m else 0
    m = re.search(r'FAR_CHANNEL_0 TX_GRANTS:\s+(\d+)', text)
    d['tx_grants'] = int(m.group(1)) if m else 0
    return d


def do_run():
    base = [('mix_alloc_off', MIX, 'allocate', 0),
            ('mix_nt_off', MIX, 'nt', 0),
            ('mix_elide_off', MIX, 'elide', 0),
            ('reuse_alloc_off', REUSE, 'allocate', 0),
            ('reuse_nt_off', REUSE, 'nt', 0),
            ('reuse_elide_off', REUSE, 'elide', 0)]
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for fu in cf.as_completed({ex.submit(run_one, *j) for j in base}):
            print('[run] %-18s rc=%d' % fu.result(), flush=True)
    a = stats('mix_alloc_off')
    rate = (a['far']['rd_lines'] + a['far']['wr_lines']) / a['roi_sec']
    plan = {'bind': max(1, int(round(1e12 / (0.6 * rate))))}
    json.dump(plan, open(PLAN, 'w'), indent=1)
    print('[plan] bind period %d ps' % plan['bind'], flush=True)
    binds = [('mix_alloc_bind', MIX, 'allocate', plan['bind']),
             ('mix_nt_bind', MIX, 'nt', plan['bind']),
             ('mix_elide_bind', MIX, 'elide', plan['bind'])]
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for fu in cf.as_completed({ex.submit(run_one, *j) for j in binds}):
            print('[run] %-18s rc=%d' % fu.result(), flush=True)
    print('RUN_DONE', flush=True)


CHECKS = []


def check(desc):
    def deco(fn):
        CHECKS.append((desc, fn))
        return fn
    return deco


@check('E2 every far RFO becomes a local grant (fetches==0, grants ~= alloc fetches)')
def e2(R):
    e, a = R['mix_elide_off'], R['mix_alloc_off']
    assert e['rfo_fetch'] == 0, 'elide run still fetched %d RFOs' % e['rfo_fetch']
    assert e['elided'] > 0, 'no grants recorded'
    ratio = e['elided'] / a['rfo_fetch']
    assert 0.9 <= ratio <= 1.1, 'grants/alloc-fetches = %.3f' % ratio
    return 'grants %d vs alloc fetches %d (%.3f)' % (e['elided'], a['rfo_fetch'], ratio)


@check('E3 read direction loses exactly the fetches')
def e3(R):
    e, a = R['mix_elide_off'], R['mix_alloc_off']
    expect = a['far']['rd_lines'] - a['rfo_fetch']
    assert rv.approx(e['far']['rd_lines'], expect, 0.05), \
        'rd_lines %d vs expected %d' % (e['far']['rd_lines'], expect)
    return 'rd_lines %d ~= %d' % (e['far']['rd_lines'], expect)


@check('E4 write direction untouched')
def e4(R):
    e, a = R['mix_elide_off'], R['mix_alloc_off']
    assert rv.approx(e['far']['wr_lines'], a['far']['wr_lines'], 0.05)
    return 'wr_lines %d vs %d' % (e['far']['wr_lines'], a['far']['wr_lines'])


@check('E5 [CXLTX] slot invariant holds under elide (grants == RD+WR lines)')
def e5(R):
    e = R['mix_elide_bind']
    lines = e['far']['rd_lines'] + e['far']['wr_lines']
    assert abs(e['tx_grants'] - lines) <= 4, '%d vs %d' % (e['tx_grants'], lines)
    return 'tx %d == lines %d' % (e['tx_grants'], lines)


@check('E6 under a binding budget on a no-reuse mix, elide matches nt (tx parity)')
def e6(R):
    e, n = R['mix_elide_bind'], R['mix_nt_bind']
    assert rv.approx(e['useful_gbs'], n['useful_gbs'], 0.05), \
        'useful %f vs %f' % (e['useful_gbs'], n['useful_gbs'])
    return 'useful %.2f vs nt %.2f GB/s' % (e['useful_gbs'], n['useful_gbs'])


@check('E7 with write-to-read reuse, residency makes elide beat nt')
def e7(R):
    e, n = R['reuse_elide_off'], R['reuse_nt_off']
    assert e['ipc'] > n['ipc'] * 1.02, 'ipc %f vs nt %f' % (e['ipc'], n['ipc'])
    assert e['far']['rd_lines'] < n['far']['rd_lines'] * 0.9, \
        'rd_lines %d vs nt %d (no re-fetch saving)' % (e['far']['rd_lines'], n['far']['rd_lines'])
    return 'ipc %.4f vs nt %.4f; rd_lines %d vs %d' % \
        (e['ipc'], n['ipc'], e['far']['rd_lines'], n['far']['rd_lines'])


@check('E8 the latency channel alone helps: IPC(elide) > IPC(alloc), budget off')
def e8(R):
    e, a = R['mix_elide_off'], R['mix_alloc_off']
    assert e['ipc'] > a['ipc'], 'ipc %f vs %f' % (e['ipc'], a['ipc'])
    return 'ipc %.4f vs alloc %.4f (%+.1f%%)' % (e['ipc'], a['ipc'], 100 * (e['ipc'] / a['ipc'] - 1))


def do_verify():
    names = ['mix_alloc_off', 'mix_nt_off', 'mix_elide_off',
             'reuse_alloc_off', 'reuse_nt_off', 'reuse_elide_off',
             'mix_alloc_bind', 'mix_nt_bind', 'mix_elide_bind']
    R = {n: stats(n) for n in names}
    for n in names:
        assert R[n]['completed'], n + ' did not complete'
    passed = 0
    for desc, fn in CHECKS:
        try:
            msg = fn(R)
            print('PASS %s\n     %s' % (desc, msg))
            passed += 1
        except AssertionError as ex:
            print('FAIL %s\n     %s' % (desc, ex))
    print('\n[CXLELIDE] %d/%d checks pass' % (passed, len(CHECKS)))
    return passed == len(CHECKS)


def do_report():
    names = ['mix_alloc_off', 'mix_nt_off', 'mix_elide_off',
             'mix_alloc_bind', 'mix_nt_bind', 'mix_elide_bind',
             'reuse_alloc_off', 'reuse_nt_off', 'reuse_elide_off']
    print('%-18s %7s %8s %10s %10s %10s %10s' %
          ('run', 'IPC', 'useful', 'rd_lines', 'wr_lines', 'rfo_fetch', 'elided'))
    for n in names:
        try:
            r = stats(n)
        except FileNotFoundError:
            continue
        print('%-18s %7.4f %8.2f %10d %10d %10d %10d' %
              (n, r.get('ipc', 0), r.get('useful_gbs', 0), r['far'].get('rd_lines', 0),
               r['far'].get('wr_lines', 0), r['rfo_fetch'], r['elided']))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'report'
    {'run': do_run, 'verify': lambda: sys.exit(0 if do_verify() else 1), 'report': do_report}[cmd]()
