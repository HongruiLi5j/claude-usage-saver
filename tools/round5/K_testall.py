#!/usr/bin/env python3
"""一条命令跑套件的全部测试，并给一张汇总表（不改任何测试文件）。

测试现在散在 tools/tests_r4*.py、tools/scorecard.py、tools/round5/*_tests.py / K_test_*.py、adapters/tests/ 里，
各自的输出格式不一样。本脚本自动发现它们，逐个在子进程里跑（带超时），从输出里认出"通过数/总数"，
最后汇总。新加的测试文件只要名字符合下面的规则就会被自动带上。

发现规则（相对套件根目录）：
  tools/tests_*.py                 第 4 轮起的单元测试
  tools/round5/*_tests.py、tools/round5/K_test_*.py、tools/round5/*_test.py
  adapters/tests/test_*.py
  tools/scorecard.py --version current     固定场景评分表
  tools/round5/I_adapt_matrix.py          适应性矩阵
跳过：tools/heldout/（盲测，实施者不看）、名字含 live_ 的（会真的调用外部 CLI、消耗额度）。

用法：python3 tools/round5/K_testall.py [--only 关键词] [--timeout 秒] [--json out.json] [--list]
退出码：全部通过 0；有失败/超时 1。
"""
import argparse, glob, json, os, re, subprocess, sys, time

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATTERNS = ['tools/tests_*.py', 'tools/round5/*_tests.py', 'tools/round5/K_test_*.py', 'tools/round5/*_test.py',
            'adapters/tests/test_*.py']
EXTRA = [('tools/scorecard.py', ['--version', 'current']), ('tools/round5/I_adapt_matrix.py', [])]
SKIP = ('/heldout/', 'live_')


def discover(kit=KIT):
    out = []
    for pat in PATTERNS:
        for p in sorted(glob.glob(os.path.join(kit, pat))):
            rel = os.path.relpath(p, kit)
            if any(s in '/' + rel for s in SKIP):
                continue
            out.append((rel, []))
    for rel, args in EXTRA:
        if os.path.exists(os.path.join(kit, rel)):
            out.append((rel, args))
    seen, uniq = set(), []
    for x in out:
        if x[0] not in seen:
            seen.add(x[0]); uniq.append(x)
    return uniq


# 识别"通过数/总数"的几种写法（取输出里最后一个匹配）
PAT_FRAC = [
    re.compile(r'合计\s*(?P<b>\d+)\s*[：:]\s*通过\s*(?P<a>\d+)'),
    re.compile(r'(\d+)\s*/\s*(\d+)\s*(?:通过|passed|pass|PASS|ok|OK)'),
    re.compile(r'(?:通过|passed|pass|PASS|总计|合计|total)[^\d\n]{0,12}(\d+)\s*/\s*(\d+)'),
    re.compile(r'\((\d+)\s*/\s*(\d+)\)'),
]
PAT_UNITTEST = re.compile(r'Ran (\d+) tests?.*?\n\s*\n?(OK|FAILED \((?:failures|errors)=(\d+)(?:, (?:errors|failures)=(\d+))?\))', re.S)


def _frac_in(lines):
    best = None
    for ln in lines:
        for pat in PAT_FRAC:
            for g in pat.finditer(ln):
                gd = g.groupdict()
                a, b = (int(gd['a']), int(gd['b'])) if gd.get('a') else (int(g.group(1)), int(g.group(2)))
                if 0 < b and a <= b:
                    best = (a, b)          # 越靠后的行越优先（逐行顺序覆盖）
    return best


def parse(out):
    """返回 (passed, total) 或 None。优先级：unittest 摘要 → 最后 3 行里的 N/M → pytest 摘要行 → 最后 15 行里的 N/M。"""
    m = list(PAT_UNITTEST.finditer(out))
    if m and re.search(r'(?m)^Ran \d+ tests?', out):
        g = m[-1]
        n = int(g.group(1))
        bad = 0 if g.group(2) == 'OK' else int(g.group(3) or 0) + int(g.group(4) or 0)
        return n - bad, n
    lines = [l for l in out.strip().splitlines() if l.strip()]
    r = _frac_in(lines[-3:])
    if r:
        return r
    m = list(re.finditer(r'(?m)^=+ (?:(\d+) failed, )?(\d+) passed.*? in [\d.]+s', out))
    if m:
        f, p = int(m[-1].group(1) or 0), int(m[-1].group(2))
        return p, p + f
    return _frac_in(lines[-15:])


def run(rel, args, timeout, kit=KIT):
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, os.path.join(kit, rel), *args], cwd=kit, capture_output=True, text=True,
                           timeout=timeout, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        out = p.stdout + ('\n' + p.stderr if p.stderr else '')
        rc, to = p.returncode, False
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b'').decode(errors='ignore') if isinstance(e.stdout, bytes) else (e.stdout or '')
        rc, to = None, True
    sec = time.time() - t0
    frac = parse(out)
    ok = (not to) and rc == 0 and (frac is None or frac[0] == frac[1])
    status = 'TIMEOUT' if to else ('PASS' if ok else 'FAIL')
    return dict(suite=rel + (' ' + ' '.join(args) if args else ''), rc=rc, status=status, passed=frac[0] if frac else None,
                total=frac[1] if frac else None, sec=round(sec, 1), tail='\n'.join(out.strip().splitlines()[-6:]) if not ok else '')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--only')
    ap.add_argument('--timeout', type=float, default=300)
    ap.add_argument('--json')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--kit', default=KIT)
    a = ap.parse_args(argv)
    suites = [s for s in discover(a.kit) if not a.only or a.only in s[0]]
    if a.list:
        for rel, args in suites:
            print(rel, *args)
        return 0
    res = []
    for rel, args in suites:
        r = run(rel, args, a.timeout, a.kit)
        res.append(r)
        frac = f'{r["passed"]}/{r["total"]}' if r['total'] else '-'
        print(f'{r["status"]:7s} {frac:>9s} {r["sec"]:6.1f}s  {r["suite"]}', flush=True)
        if r['tail']:
            print('        ' + r['tail'].replace('\n', '\n        ')[:1500])
    tp = sum(r['passed'] or 0 for r in res); tt = sum(r['total'] or 0 for r in res)
    nf = sum(r['status'] != 'PASS' for r in res)
    print(f'\n{len(res)} 个测试套件，{len(res) - nf} 个通过、{nf} 个失败/超时；可识别的用例 {tp}/{tt}；共 {sum(r["sec"] for r in res):.0f}s')
    if a.json:
        with open(a.json, 'w') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0 if nf == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
