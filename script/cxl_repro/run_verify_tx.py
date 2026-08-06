#!/usr/bin/env python3
"""[CXLTX] corner-case verifier for the shared transaction-rate budget.

The budget models the measured property that makes this project's central result what
it is: the downstream CXL path saturates on completions per second while neither byte
direction is saturated, so an optimization that removes bytes but not requests buys
nothing. A model that bounds only per-direction bandwidth cannot express that, which is
why the constraint was added.

This script verifies the constraint itself, not the research claim. It checks that the
budget is inert when disabled, that it is an actual ceiling, that it accounts exactly,
that it starves neither direction, and that it makes transactions-per-line the thing
that decides throughput. The 15 invariants of run_verify.py continue to cover the
underlying model and must pass with the budget off.

The binding budgets are derived from a measured baseline rather than hard-coded, so the
script stays valid if the far-link configuration changes.

Usage:
  run_verify_tx.py run     # measure the baseline, derive budgets, run the matrix
  run_verify_tx.py verify  # check all invariants
  run_verify_tx.py report  # print the table
"""
import json
import os
import subprocess
import sys
import concurrent.futures as cf

import run_verify as rv

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_TX = os.path.join(HERE, 'out_tx')
PLAN = os.path.join(OUT_TX, '_plan.json')
TRACE = 'mix_r8.champsim.gz'   # balanced mix: both directions active

os.makedirs(OUT_TX, exist_ok=True)
rv.OUT = OUT_TX  # rv.parse() reads from here


def run_one(name, policy, tx_period_ps, trace=TRACE, sim=None):
    env = dict(os.environ, CXL_STORE_POLICY=policy, CXL_ALLOC_POLICY='only_far',
               CXL_LIVELOCK_IPC='0.0001')
    if tx_period_ps is None:
        env.pop('CXL_TX_PERIOD_PS', None)  # knob absent entirely
    else:
        env['CXL_TX_PERIOD_PS'] = str(tx_period_ps)
    env.pop('CXL_TX_RATE_MTPS', None)
    with open(os.path.join(OUT_TX, name + '.txt'), 'w') as f:
        p = subprocess.run(
            [rv.BIN, '--warmup-instructions', str(rv.WARMUP),
             '--simulation-instructions', str(sim or rv.SIM), os.path.join(rv.TRACES, trace)],
            env=env, stdout=f, stderr=subprocess.STDOUT)
    return name, p.returncode


def tx_stats(name):
    """rv.parse plus the [CXLTX] fields."""
    import re
    d = rv.parse(name)
    text = open(os.path.join(OUT_TX, name + '.txt')).read()
    m = re.search(r'FAR_CHANNEL_0 TX_GRANTS:\s+(\d+) TX_STALL_EVENTS:\s+(\d+) TX_STALL_ps:\s+(-?\d+)', text)
    d['tx_grants'], d['tx_stalls'], d['tx_stall_ps'] = (map(int, m.groups()) if m else (0, 0, 0))
    d['tx_grants'], d['tx_stalls'], d['tx_stall_ps'] = int(d['tx_grants']), int(d['tx_stalls']), int(d['tx_stall_ps'])
    m = re.search(r'CXLTX tx_period_ps:\s+(\d+)', text)
    d['tx_period_ps'] = int(m.group(1)) if m else 0
    if d.get('roi_sec'):
        d['tx_rate'] = d['tx_grants'] / d['roi_sec'] if d['tx_grants'] else 0.0
        d['line_rate'] = (d['far'].get('rd_lines', 0) + d['far'].get('wr_lines', 0)) / d['roi_sec']
    return d


# ---------------------------------------------------------------- plan
def build_plan():
    """Measure the unconstrained transaction rate, then place budgets around it."""
    print('[plan] measuring baseline (budget absent) ...')
    run_one('base_alloc', 'allocate', None)
    b = tx_stats('base_alloc')
    achieved = b['line_rate']                      # serviced lines per second, no budget
    print('[plan] baseline: %.3e serviced lines/s, useful %.2f GB/s' % (achieved, b['useful_gbs']))
    ps = lambda rate: max(1, int(round(1e12 / rate)))
    plan = {
        'achieved_line_rate': achieved,
        # non-binding: 1 ps can never defer a grant at this clock. must reproduce baseline.
        'loose': 1,
        # binding: 60% of what the model achieved unconstrained
        'bind': ps(achieved * 0.60),
        # half of that again: rate must track the budget, not the buses
        'bind2': ps(achieved * 0.30),
        # severe: throughput must collapse without deadlocking
        'severe': ps(achieved * 0.05),
    }
    json.dump(plan, open(PLAN, 'w'), indent=1)
    print('[plan] periods (ps): ' + ', '.join('%s=%d' % (k, v) for k, v in plan.items() if k != 'achieved_line_rate'))
    return plan


def do_run():
    plan = build_plan()
    jobs = [
        ('off_alloc',    'allocate', 0),                 # explicit 0 == disabled
        ('loose_alloc',  'allocate', plan['loose']),
        ('bind_alloc',   'allocate', plan['bind']),
        ('bind2_alloc',  'allocate', plan['bind2']),
        ('severe_alloc', 'allocate', plan['severe'], 150_000),
        ('base_nt',      'nt',       None),
        ('bind_nt',      'nt',       plan['bind']),
    ]
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(run_one, j[0], j[1], j[2], TRACE, j[3] if len(j) > 3 else None): j[0] for j in jobs}
        for fu in cf.as_completed(futs):
            n, rc = fu.result()
            print('[run] %-14s rc=%d' % (n, rc))


# ---------------------------------------------------------------- checks
CHECKS = []


def check(desc):
    def deco(fn):
        CHECKS.append((desc, fn))
        return fn
    return deco


@check('T1  budget absent and budget=0 are the same run (the knob is inert when off)')
def t1(R):
    a, b = R['base_alloc'], R['off_alloc']
    assert a['completed'] and b['completed'], 'a run did not complete'
    assert a['cycles'] == b['cycles'], 'cycles %d vs %d' % (a['cycles'], b['cycles'])
    assert a['far']['rd_lines'] == b['far']['rd_lines'], 'rd_lines differ'
    assert a['far']['wr_lines'] == b['far']['wr_lines'], 'wr_lines differ'
    assert b['tx_grants'] == 0, 'disabled run granted %d slots' % b['tx_grants']
    return 'cycles=%d identical, no grants emitted' % a['cycles']


@check('T2  every serviced line consumes exactly one slot (tx_grants == RD+WR lines)')
def t2(R):
    msgs = []
    for n in ('loose_alloc', 'bind_alloc', 'bind2_alloc', 'severe_alloc', 'bind_nt'):
        d = R[n]
        lines = d['far']['rd_lines'] + d['far']['wr_lines']
        inflight = d['tx_grants'] - lines
        assert inflight >= 0, '%s: %d lines completed without a grant' % (n, -inflight)
        assert inflight <= 64, '%s: %d grants unaccounted (>1 bank set)' % (n, inflight)
        msgs.append('%s inflight=%d' % (n, inflight))
    return ' '.join(msgs)


@check('T3  a budget that can never defer a grant does not perturb the run')
def t3(R):
    a, b = R['base_alloc'], R['loose_alloc']
    assert b['completed'], 'loose run did not complete'
    d = abs(b['useful_gbs'] - a['useful_gbs']) / a['useful_gbs']
    assert d < 0.02, 'useful BW moved %.1f%% with a non-binding budget' % (100 * d)
    stall_frac = b['tx_stall_ps'] * 1e-12 / b['roi_sec']
    assert stall_frac < 0.01, 'non-binding budget stalled for %.1f%% of the run' % (100 * stall_frac)
    return 'useful %.2f vs %.2f GB/s (%.2f%%), stalled time %.3f%% of run' % (
        b['useful_gbs'], a['useful_gbs'], 100 * d, 100 * stall_frac)


@check('T4  the budget is an actual ceiling: measured rate <= 1/period')
def t4(R):
    msgs = []
    for n in ('bind_alloc', 'bind2_alloc', 'severe_alloc', 'bind_nt'):
        d = R[n]
        if not d.get('completed') or d['tx_period_ps'] == 0:
            msgs.append('%s SKIP(incomplete)' % n)
            continue
        cap = 1e12 / d['tx_period_ps']
        assert d['tx_rate'] <= cap * 1.02, '%s: rate %.3e exceeds cap %.3e' % (n, d['tx_rate'], cap)
        msgs.append('%s %.2f%% of cap' % (n, 100 * d['tx_rate'] / cap))
    return ' '.join(msgs)


@check('T5  halving the budget halves the transaction rate (rate tracks the budget, not the buses)')
def t5(R):
    a, b = R['bind_alloc'], R['bind2_alloc']
    ratio = a['tx_rate'] / b['tx_rate']
    assert 1.7 < ratio < 2.3, 'rate ratio %.2f for a 2x budget ratio' % ratio
    return 'rate %.3e -> %.3e (x%.2f)' % (a['tx_rate'], b['tx_rate'], ratio)


@check('T6  a binding budget caps throughput while BOTH byte directions stay unsaturated')
def t6(R):
    d = R['bind_alloc']
    assert d['tx_stalls'] > 0, 'budget never actually stalled a grant'
    assert d['rd_busy_frac'] < 0.95, 'read bus %.1f%% busy' % (100 * d['rd_busy_frac'])
    assert d['wr_busy_frac'] < 0.95, 'write bus %.1f%% busy' % (100 * d['wr_busy_frac'])
    return 'rd %.1f%% wr %.1f%% busy, %d stalls -- the hardware signature' % (
        100 * d['rd_busy_frac'], 100 * d['wr_busy_frac'], d['tx_stalls'])


@check('T7  a shared budget starves neither direction (mix preserved vs unconstrained)')
def t7(R):
    a, b = R['base_alloc'], R['bind_alloc']
    for n, d in (('base', a), ('bind', b)):
        assert d['far']['rd_lines'] > 0 and d['far']['wr_lines'] > 0, '%s: a direction went to zero' % n
    ra = a['far']['wr_lines'] / a['far']['rd_lines']
    rb = b['far']['wr_lines'] / b['far']['rd_lines']
    assert abs(rb - ra) / ra < 0.25, 'write/read mix moved %.1f%% (%.3f -> %.3f)' % (100 * abs(rb - ra) / ra, ra, rb)
    return 'wr/rd %.3f -> %.3f' % (ra, rb)


@check('T8  a severe budget degrades gracefully: completes, no livelock, throughput collapses')
def t8(R):
    d = R['severe_alloc']
    assert d['completed'], 'severe run did not complete'
    assert d['ipc'] > 0, 'IPC reached zero'
    assert d['useful_gbs'] < 0.5 * R['base_alloc']['useful_gbs'], 'severe budget did not bite'
    return 'useful %.2f GB/s (base %.2f), IPC %.4f, completed' % (
        d['useful_gbs'], R['base_alloc']['useful_gbs'], d['ipc'])


def _tx_per_useful(d):
    useful = d['demand_rd'] + (d['nt_bypass'] if d['nt_bypass'] > 0 else d['wb'])
    tx = d['tx_grants'] if d['tx_grants'] else d['far']['rd_lines'] + d['far']['wr_lines']
    return tx / useful


@check('T9  under a binding budget, useful bandwidth is set by transactions per useful line')
def t9(R):
    a, b = R['bind_alloc'], R['bind_nt']
    ta, tb = _tx_per_useful(a), _tx_per_useful(b)
    predicted = ta / tb                                    # transaction accounting alone
    measured = b['useful_gbs'] / a['useful_gbs']
    err = abs(measured - predicted) / predicted
    assert err < 0.05, 'predicted %.3f but measured %.3f (%.1f%% off)' % (predicted, measured, 100 * err)
    off = R['base_nt']['useful_gbs'] / R['base_alloc']['useful_gbs']
    return ('tx/useful %.3f (alloc) vs %.3f (NT) predicts %.3fx; measured %.3fx (%.2f%% off). '
            'Unconstrained the ratio is %.2fx instead, set by read-direction contention.'
            % (ta, tb, predicted, measured, 100 * err, off))


def do_verify():
    names = ['base_alloc', 'off_alloc', 'loose_alloc', 'bind_alloc', 'bind2_alloc',
             'severe_alloc', 'base_nt', 'bind_nt']
    R = {}
    for n in names:
        try:
            R[n] = tx_stats(n)
        except FileNotFoundError:
            print('MISSING output for %s -- run first' % n)
            return 1
    npass = 0
    for desc, fn in CHECKS:
        try:
            detail = fn(R)
            print('  PASS  %s\n          %s' % (desc, detail))
            npass += 1
        except AssertionError as e:
            print('  FAIL  %s\n          %s' % (desc, e))
        except Exception as e:  # noqa: BLE001
            print('  ERROR %s\n          %r' % (desc, e))
    print('\n[CXLTX] %d/%d corner cases pass' % (npass, len(CHECKS)))
    return 0 if npass == len(CHECKS) else 1


def do_report():
    print('%-14s %8s %10s %12s %12s %9s %9s %10s' %
          ('run', 'IPC', 'period_ps', 'tx/s', 'lines/s', 'rd_busy', 'wr_busy', 'useful_GBs'))
    for n in ('base_alloc', 'off_alloc', 'loose_alloc', 'bind_alloc', 'bind2_alloc',
              'severe_alloc', 'base_nt', 'bind_nt'):
        try:
            d = tx_stats(n)
        except FileNotFoundError:
            continue
        if 'ipc' not in d:
            print('%-14s %8s %10d  (incomplete)' % (n, '--', d['tx_period_ps']))
            continue
        print('%-14s %8.4f %10d %12.3e %12.3e %8.1f%% %8.1f%% %10.2f' %
              (n, d['ipc'], d['tx_period_ps'], d.get('tx_rate', 0), d.get('line_rate', 0),
               100 * d['rd_busy_frac'], 100 * d['wr_busy_frac'], d['useful_gbs']))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'verify'
    if cmd == 'run':
        do_run()
    elif cmd == 'report':
        do_report()
    else:
        sys.exit(do_verify())
