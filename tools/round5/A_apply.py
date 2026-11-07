#!/usr/bin/env python3
"""第 5 轮 A：压缩每个会话 / 每个子 agent 都要带的固定开销（工具定义、skill 列表等）。

做法：只往 settings.json 里加几个官方设置项，不改任何 SKILL.md、插件或二进制。
  --dry-run   只打印会改什么（默认）
  --apply     备份后写入（目标是真实 ~/.claude/settings.json 时还必须加 --yes）
  --undo      按上次 --apply 记下的"原值"逐项还原（只还原本脚本动过的键，之后别人改的其它键不受影响）
  --status    看目标文件里这些开关现在是什么状态
  --target <路径>   默认 ~/.claude/settings.json；测试时指向临时副本
  --profile <名字>  core（默认）| core+tools | core+tools+git | all，见下面 PROFILES
  --print-lean-settings  打印一份等价的 --settings JSON（"别名模式"：alias claude='claude --settings <文件>'，
                         想要完整功能时用 `command claude`，不动 settings.json）

按需临时恢复（详见 reports/round5_A.md 第 4 节）：
  --restore artifact|skills|tools|git|connectors|agentsmd   只把某一组开关撤掉（新开的会话生效），用完再 --apply 一次
备份：每次 --apply 前把目标文件 cp 到 claude-usage-saver/backups/<文件名>.pre-r5A-<时间戳>，
      原值记录在 backups/A_apply_state.<目标哈希>.json。

第 5 轮红线：本轮只在临时副本上验证，绝不对真实 settings 执行 --apply（由主会话问所有者后再跑）。
"""
import json, os, sys, time, shutil, hashlib, copy

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # claude-usage-saver
BACKUPS = os.path.join(BASE, 'backups')
REAL = os.path.realpath(os.path.expanduser('~/.claude/settings.json'))
ABSENT = '<<absent>>'

# 13 个 claude.ai 同步来的 anthropic-skills:*（实测在 skill 列表里约 10k 字符）
ANTH = ['built-in-browser', 'chrome-browser', 'computer-use', 'deep-research', 'docs', 'docx', 'google-workspace',
        'import-memory', 'morning', 'pdf', 'pptx', 'skill-creator', 'xlsx']
# "user-invocable-only"：模型看不到，但所有者照样能手动输入 /anthropic-skills:pdf 调用
SKILL_MODE = 'user-invocable-only'
# 主会话里历史上 0 次调用的工具（实测 tool_use 计数：AskUserQuestion 39 次、ScheduleWakeup 3 次（/loop 用）都保留）
UNUSED_TOOLS = ['SendFeedback', 'ReportFindings']

GROUPS = {
    'artifact': {'set': {'enableArtifact': False}},
    'skills': {'skillOverrides': {'anthropic-skills:' + n: SKILL_MODE for n in ANTH}},
    'tools': {'deny': UNUSED_TOOLS},
    'git': {'set': {'includeGitInstructions': False}},
    'connectors': {'set': {'disableClaudeAiConnectors': True}},
    # 内置插件 cc-plugin-agents-md 的选项：只读 CLAUDE.md，不再把 Projects/AGENTS.md 注入 Claude（Codex 照旧读）
    'agentsmd': {'pluginopt': ('cc-plugin-agents-md@builtin', 'instructionFiles', 'claude-md')},
}
PROFILES = {
    'artifact-only': ['artifact'],  # 第 6 轮讨论（Claude×Codex）后的推荐方案
    'core': ['artifact', 'skills'],
    'core+tools': ['artifact', 'skills', 'tools'],
    'core+tools+git': ['artifact', 'skills', 'tools', 'git'],
    'all': ['artifact', 'skills', 'tools', 'git', 'connectors', 'agentsmd'],
}


def load(p):
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def save_atomic(p, obj):
    # 第 6 轮修（Codex 复核 A 打回 #1）：保留原文件权限；新文件默认 0600，避免把 settings 放宽成别人可读
    try:
        mode = os.stat(p).st_mode & 0o7777
    except FileNotFoundError:
        mode = 0o600
    tmp = p + '.tmp-r5A'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')
    json.load(open(tmp))  # 写完再读一遍，确认是合法 JSON
    os.chmod(tmp, mode)
    os.replace(tmp, p)


def bdir(target):
    # 真实 settings 的备份放项目 backups/；临时副本的备份放在副本旁边，免得和真实备份混在一起
    if os.path.realpath(target) == REAL:
        return BACKUPS
    return os.path.join(os.path.dirname(os.path.realpath(target)), '_r5A_backups')


def state_path(target):
    h = hashlib.sha1(os.path.realpath(target).encode()).hexdigest()[:10]
    return os.path.join(bdir(target), f'A_apply_state.{h}.json')


def plan(cfg, groups):
    """返回 (新配置, 原值记录)。原值记录格式：{group: {...}}，undo 用。"""
    new = copy.deepcopy(cfg)
    rec = {}
    for g in groups:
        spec = GROUPS[g]
        r = {}
        for k, v in spec.get('set', {}).items():
            r.setdefault('set', {})[k] = cfg.get(k, ABSENT)
            new[k] = v
        if 'skillOverrides' in spec:
            cur = dict(new.get('skillOverrides') or {})
            r['skillOverrides'] = {}
            for k, v in spec['skillOverrides'].items():
                r['skillOverrides'][k] = cur.get(k, ABSENT)
                cur[k] = v
            r['skillOverrides_key_absent'] = 'skillOverrides' not in cfg
            new['skillOverrides'] = cur
        if 'deny' in spec:
            perms = dict(new.get('permissions') or {})
            deny = list(perms.get('deny') or [])
            added = [t for t in spec['deny'] if t not in deny]
            r['deny_added'] = added
            r['permissions_key_absent'] = 'permissions' not in cfg
            r['deny_key_absent'] = 'deny' not in (cfg.get('permissions') or {})
            perms['deny'] = deny + added
            new['permissions'] = perms
        if 'pluginopt' in spec:
            pid, key, val = spec['pluginopt']
            pc = copy.deepcopy(new.get('pluginConfigs') or {})
            ent = pc.setdefault(pid, {})
            opts = ent.setdefault('options', {})
            r['pluginopt'] = {'pid': pid, 'key': key, 'old': opts.get(key, ABSENT),
                              'pc_absent': 'pluginConfigs' not in cfg,
                              'ent_absent': pid not in (cfg.get('pluginConfigs') or {}),
                              'opts_absent': 'options' not in ((cfg.get('pluginConfigs') or {}).get(pid) or {})}
            opts[key] = val
            new['pluginConfigs'] = pc
        rec[g] = r
    return new, rec


def unplan(cfg, rec, only=None):
    new = copy.deepcopy(cfg)
    for g, r in rec.items():
        if only and g not in only:
            continue
        for k, old in r.get('set', {}).items():
            if old == ABSENT:
                new.pop(k, None)
            else:
                new[k] = old
        if 'skillOverrides' in r:
            cur = dict(new.get('skillOverrides') or {})
            for k, old in r['skillOverrides'].items():
                if old == ABSENT:
                    cur.pop(k, None)
                else:
                    cur[k] = old
            if cur or not r.get('skillOverrides_key_absent'):
                new['skillOverrides'] = cur
            else:
                new.pop('skillOverrides', None)
        if 'deny_added' in r:
            perms = dict(new.get('permissions') or {})
            deny = [t for t in (perms.get('deny') or []) if t not in r['deny_added']]
            if deny or not r.get('deny_key_absent'):
                perms['deny'] = deny
            else:
                perms.pop('deny', None)
            if perms or not r.get('permissions_key_absent'):
                new['permissions'] = perms
            else:
                new.pop('permissions', None)
        if 'pluginopt' in r:
            o = r['pluginopt']
            pc = copy.deepcopy(new.get('pluginConfigs') or {})
            ent = pc.get(o['pid']) or {}
            opts = ent.get('options') or {}
            if o['old'] == ABSENT:
                opts.pop(o['key'], None)
            else:
                opts[o['key']] = o['old']
            if opts or not o['opts_absent']:
                ent['options'] = opts
            else:
                ent.pop('options', None)
            if ent or not o['ent_absent']:
                pc[o['pid']] = ent
            else:
                pc.pop(o['pid'], None)
            if pc or not o['pc_absent']:
                new['pluginConfigs'] = pc
            else:
                new.pop('pluginConfigs', None)
    return new


def diff(a, b):
    lines = []
    for k in sorted(set(a) | set(b)):
        if a.get(k, ABSENT) != b.get(k, ABSENT):
            lines.append(f'  {k}:\n    前：{json.dumps(a.get(k, ABSENT), ensure_ascii=False)}\n    后：{json.dumps(b.get(k, ABSENT), ensure_ascii=False)}')
    return '\n'.join(lines) or '  （无变化）'


def backup(target):
    os.makedirs(bdir(target), exist_ok=True)
    if os.path.exists(target):
        # 第 6 轮修（Codex 复核 A 打回 #2）：文件名带微秒，并且绝不覆盖已有备份（同名就加序号）
        t = time.time()
        base = os.path.join(bdir(target), os.path.basename(target) + '.pre-r5A-'
                            + time.strftime('%Y%m%d-%H%M%S', time.localtime(t)) + '-%06d' % int((t % 1) * 1e6))
        dst, i = base, 1
        while True:
            try:
                fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                dst = '%s.%d' % (base, i); i += 1
        os.close(fd)
        shutil.copy2(target, dst)
        return dst
    return None


def lean_settings(groups):
    new, _ = plan({}, groups)
    # 别名模式下用 syncClaudeAiSkills:false 更彻底：--settings 里设它只对本次启动生效，不会移动任何文件
    if 'skills' in groups:
        new.pop('skillOverrides', None)
        new['syncClaudeAiSkills'] = False
    return new


def main():
    a = sys.argv[1:]
    mode = 'dry-run'; target = REAL; profile = 'core'; yes = False; restore = None
    while a:
        x = a.pop(0)
        if x in ('--dry-run', '--apply', '--undo', '--status', '--print-lean-settings'):
            mode = x[2:]
        elif x == '--restore':
            mode = 'restore'; restore = a.pop(0).split(',')
        elif x == '--target':
            target = os.path.realpath(os.path.expanduser(a.pop(0)))
        elif x == '--profile':
            profile = a.pop(0)
        elif x == '--yes':
            yes = True
        elif x in ('-h', '--help'):
            print(__doc__); return 0
        else:
            print('未知参数：', x); return 2
    if profile not in PROFILES:
        print('profile 只能是：', ', '.join(PROFILES)); return 2
    groups = PROFILES[profile]
    is_real = os.path.realpath(target) == REAL
    sp = state_path(target)
    cfg = load(target)

    if mode == 'print-lean-settings':
        print(json.dumps(lean_settings(groups), ensure_ascii=False, indent=2)); return 0

    if mode == 'status':
        so = cfg.get('skillOverrides') or {}
        print('目标：', target)
        print('  enableArtifact =', cfg.get('enableArtifact', '(未设，默认开)'))
        print('  anthropic-skills 覆盖：', sum(1 for n in ANTH if ('anthropic-skills:' + n) in so), '/', len(ANTH))
        print('  deny 里的未用工具：', [t for t in UNUSED_TOOLS if t in ((cfg.get('permissions') or {}).get('deny') or [])])
        print('  includeGitInstructions =', cfg.get('includeGitInstructions', '(未设，默认 true)'))
        print('  disableClaudeAiConnectors =', cfg.get('disableClaudeAiConnectors', '(未设)'))
        print('  AGENTS.md 模式 =', ((cfg.get('pluginConfigs') or {}).get('cc-plugin-agents-md@builtin') or {}).get('options', {}).get('instructionFiles', '(未设，默认 claude-md-or-agents-md)'))
        print('  记录文件：', sp if os.path.exists(sp) else '(无，未用本脚本应用过)')
        return 0

    if mode in ('apply', 'undo', 'restore') and is_real and not yes:
        print('目标是真实 ~/.claude/settings.json：要真的写入请加 --yes（第 5 轮红线：本轮不对真实 settings 执行）。')
        return 3

    if mode == 'dry-run':
        new, _ = plan(cfg, groups)
        print(f'[dry-run] 目标：{target}  profile={profile} 组={groups}')
        print(diff(cfg, new))
        return 0

    if mode == 'apply':
        if os.path.exists(sp):
            old_rec = json.load(open(sp))['rec']
            cfg_base = unplan(cfg, old_rec)  # 先还原上次的，再按新 profile 应用，避免记录被覆盖成"已改过的值"
        else:
            cfg_base = cfg
        new, rec = plan(cfg_base, groups)
        b = backup(target)
        save_atomic(target, new)
        json.dump({'target': target, 'profile': profile, 'time': time.strftime('%F %T'), 'backup': b, 'rec': rec},
                  open(sp, 'w'), ensure_ascii=False, indent=1)
        print(f'[apply] 已写入 {target}（备份：{b}；记录：{sp}）')
        print(diff(cfg, new))
        print('新开的会话生效（已打开的会话不变）。撤回：python3', os.path.abspath(__file__), '--undo' + (' --yes' if is_real else f' --target {target}'))
        return 0

    if mode in ('undo', 'restore'):
        if not os.path.exists(sp):
            print('没有找到应用记录：', sp); return 1
        st = json.load(open(sp))
        only = restore if mode == 'restore' else None
        if only:
            bad = [g for g in only if g not in GROUPS]
            if bad:
                print('未知分组：', bad, '可选：', list(GROUPS)); return 2
        new = unplan(cfg, st['rec'], only)
        b = backup(target)
        save_atomic(target, new)
        if mode == 'undo':
            os.replace(sp, sp + '.undone-' + time.strftime('%Y%m%d-%H%M%S'))
        else:
            for g in only:
                st['rec'].pop(g, None)
            json.dump(st, open(sp, 'w'), ensure_ascii=False, indent=1)
        print(f'[{mode}] 已还原 {target}（还原前的备份：{b}）')
        print(diff(cfg, new))
        return 0


if __name__ == '__main__':
    sys.exit(main())
