#!/usr/bin/env python3
"""第 5 轮 C：核对复核者对 test_filter（旧版 pre-r4B4）报的问题是否能复现。
用外部假 pytest（PATH 里的脚本，贴近真实），比较原命令和 rewrite() 的 bash -n、退出码、stdout（去掉包装提示行）。
用法：python3 -I C_verify_tf.py <tf_old.py> <假pytest所在bin目录> <logdir>"""
import importlib.machinery, importlib.util, subprocess, sys, os
ldr = importlib.machinery.SourceFileLoader("tf", sys.argv[1]); spec = importlib.util.spec_from_loader("tf", ldr)
M = importlib.util.module_from_spec(spec); ldr.exec_module(M)
env = dict(os.environ, PATH=sys.argv[2] + ":" + os.environ["PATH"], HOME=sys.argv[3])
CASES = [
 ("GT1 多行[[", "[[\n  x = x\n]]; pytest rc=7"),
 ("GT1 [[内||", "[[ x == y || pytest == foo ]]; echo $?"),
 ("GT2 \\ #", "pytest rc=0; echo foo\\ #literal; exit 7"),
 ("GT3 续行+注释", "echo foo \\\n#c\npytest rc=0"),
 ("GT3' 续行&&", "echo a \\\n&& pytest rc=0"),
 ("新 ${x:-a;b}", "echo ${x:-a;b}; pytest rc=0"),
 ("新 $(..\"..\")引号内", "echo \"start $(echo \"a; b\")\"; pytest rc=0"),
 ("新 $'..\\'..'", "echo $'a\\'; pytest rc=3; b'\\'"),
 ("新 $? 被覆盖", "false; pytest \"$?\""),
 ("新 __usrc 被覆盖", "__usrc=KEEP; pytest rc=0; echo \"VALUE=$__usrc\""),
 ("新 悬空 ||", "pytest rc=0 ||"),
 ("新 尾部转义空格", "pytest rc=0; printf '<%s>\\n' foo\\ "),
 ("新 \\r", "pytest rc=0\r"),
]
def run(c):
    n = subprocess.run(["bash", "-n", "-c", c], env=env, capture_output=True, text=True).returncode
    r = subprocess.run(["bash", "-c", c], env=env, capture_output=True, text=True)
    out = "\n".join(l for l in r.stdout.splitlines() if not l.startswith("[测试输出") and not l.startswith("[完整输出"))
    return n, r.returncode, out
for name, c in CASES:
    new = M.rewrite(c, sys.argv[3])
    if new is None:
        print(f"{name:22} 放行（未改写）→ 不复现"); continue
    a, b = run(c), run(new)
    same = a[0] == b[0] and a[1] == b[1] and a[2].split() == b[2].split() if False else a == b
    print(f"{name:22} {'一致 → 不复现' if same else '不同 → 复现'}  原(n,rc)={a[:2]} 改={b[:2]}" + ("" if same else f"  原out={a[2][:40]!r} 改out={b[2][:40]!r}"))
