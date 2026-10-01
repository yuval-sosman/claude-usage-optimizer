#!/usr/bin/env python3
"""Mock Claude Code transcripts for the README screenshots: a made-up developer, no real data.

    python3 make_demo.py DEMO_HOME        # writes DEMO_HOME/.claude/{projects,settings.json,.claude.json}

Regenerating docs/images/ (from the plugin folder; DEMO_HOME stands in for $HOME, so every path shows as ~/…):

    D=$(mktemp -d); python3 docs/screenshots/make_demo.py $D/home
    rsync -a --exclude .claude/ ./ $D/home/.claude/skills/claude-usage/
    run() { env -u CLAUDE_CONFIG_DIR -u CLAUDE_USAGE_OUT HOME=$D/home TZ=America/Los_Angeles python3 "$@"; }
    run $D/home/.claude/skills/claude-usage/scripts/usage_report.py
    # have Claude write $D/home/.claude-usage/insights.json and optimizations.json by the report and optimize skills'
    # steps (reading only that folder), validate them, then:
    run $D/home/.claude/skills/claude-usage/scripts/usage_report.py --render
    node docs/screenshots/shoot.mjs $D/home/.claude-usage/report.html docs/images docs/screenshots/shots.json
"""
import datetime as dt
import json
import os
import random
import shutil
import sys

R = random.Random(11)
HOME = os.path.abspath(sys.argv[1])
CLAUDE = os.path.join(HOME, '.claude')
TZ = dt.timedelta(hours=-7)                 # timestamps are written in UTC; the day plan is Pacific time
FIRST = dt.datetime(2026, 7, 28)
DAYS = 60
_n = [0]


def uid(prefix=''):
    _n[0] += 1
    return prefix + '%08x-%04x-4%03x-a%03x-%012x' % (R.getrandbits(32), R.getrandbits(16), R.getrandbits(12),
                                                     R.getrandbits(12), R.getrandbits(48) ^ _n[0])


def iso(t):
    return (t - TZ).strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (t.microsecond // 1000)


def filler(kind, n):
    line = {'code': '  const total = items.reduce((s, i) => s + i.price * i.qty, 0);\n',
            'go': 'ok  \tgithub.com/acme/billing/internal/invoice\t0.412s\n',
            'test': 'PASS  tests/checkout.spec.ts > applies discount codes (38 ms)\n',
            'grep': 'src/lib/cart.ts:42:export function cartTotal(items: CartItem[]) {\n',
            'plain': 'Updated the handler and added a regression test for the edge case.\n'}[kind]
    return (line * (n // len(line) + 1))[:max(n, 1)]


P = [   # name, weight, files, test/build commands, tasks (title, first prompt)
    ('acme-web', 38, ['src/app/checkout/page.tsx', 'src/lib/cart.ts', 'src/components/ProductGrid.tsx', 'src/app/api/orders/route.ts',
                      'tests/checkout.spec.ts', 'src/lib/search.ts', 'src/styles/globals.css', 'package.json', 'src/lib/pricing.ts'],
     ['pnpm test', 'pnpm build', 'pnpm lint', 'npx playwright test tests/checkout.spec.ts'],
     [('Fix flaky checkout e2e test', 'the checkout e2e test fails about 1 in 5 runs on CI, find out why and fix it'),
      ('Add product search with filters', 'add a search box to the catalog page with category and price filters, server side'),
      ('Fix cart total rounding', 'cart totals are off by a cent with some discount codes, fix the rounding'),
      ('Move API routes to app router', 'move the remaining pages/api routes to the app router'),
      ('Dark theme for storefront', 'add a dark theme with CSS variables, respect prefers-color-scheme'),
      ('Speed up product grid', 'the product grid janks when scrolling on mobile, profile it and fix the worst offender'),
      ('Address review comments on PR 482', 'address the review comments on PR 482'),
      ('Checkout v2 payment step', 'build the payment step for checkout v2 behind the checkout_v2 flag')]),
    ('billing-api', 24, ['internal/invoice/invoice.go', 'internal/stripe/webhook.go', 'cmd/server/main.go', 'internal/invoice/invoice_test.go',
                         'internal/db/migrations/0042_add_credits.sql', 'internal/credits/ledger.go', 'go.mod'],
     ['go test ./...', 'go build ./...', 'make lint', 'go test ./internal/stripe/ -run TestWebhook'],
     [('Stripe webhook retries', 'stripe webhooks sometimes get processed twice, make the handler idempotent'),
      ('Usage-based credits ledger', 'add a credits ledger so customers can prepay usage, with a migration'),
      ('Invoice PDF timezone bug', 'invoice PDFs show the wrong date for customers in Asia, fix the timezone handling'),
      ('Tune Postgres pool', 'we see connection timeouts under load, look at the pgx pool settings'),
      ('Idempotency keys for charges', 'add idempotency keys to the charge endpoint')]),
    ('mobile-app', 14, ['App/Views/HomeView.swift', 'App/Models/Order.swift', 'App/Services/APIClient.swift', 'AppTests/APIClientTests.swift',
                        'App/Views/OrderDetailView.swift'],
     ['xcodebuild test -scheme App -destination "platform=iOS Simulator,name=iPhone 17"', 'swift build'],
     [('Offline order cache', 'cache the last 50 orders so the orders tab works offline'),
      ('Push notification deep links', 'tapping an order-shipped push should open that order'),
      ('Fix iPad split view crash', 'the app crashes when entering split view on iPad, here is the stack trace')]),
    ('infra', 10, ['modules/db/main.tf', 'envs/prod/main.tf', '.github/workflows/deploy.yml', 'modules/cdn/main.tf'],
     ['terraform plan -out plan.bin', 'tflint'],
     [('Read replica for billing DB', 'add a read replica for the billing database in prod'),
      ('Tighten S3 bucket policies', 'audit the S3 bucket policies and remove public access where it is not needed'),
      ('Cache deps in deploy workflow', 'the deploy workflow takes 14 minutes, cache the dependencies')]),
    ('dotfiles', 6, ['.zshrc', '.config/nvim/init.lua', '.gitconfig'],
     ['zsh -i -c exit', 'nvim --headless +qa'],
     [('Faster zsh startup', 'zsh takes 900 ms to start, find what is slow'),
      ('Neovim LSP setup', 'set up the TypeScript and Go language servers in my nvim config')]),
]
FOLLOW = ['run the tests again', 'looks good, now update the docs', 'can you also handle the empty state?',
          'that broke the build, check the error', 'commit this with a short message', 'why did you change the cache setting?',
          'ok push it and open a PR', 'use the existing Button component instead', 'add a test for that edge case',
          'try a simpler approach', 'what else calls this function?', 'the CI run failed, have a look',
          'rename it to something clearer', 'now do the same for the admin page', 'explain the trade-off in two lines']
MAIN_MODEL = lambda day: 'claude-opus-5' if day < 21 else R.choices(['claude-opus-5-5', 'claude-sonnet-5', 'claude-fable-5-1'], [80, 15, 5])[0]
VERSION = lambda day: '2.3.%d' % (4 + day // 12) if day < 45 else '2.4.%d' % ((day - 45) // 6)


class Session:
    def __init__(self, proj, day, start, cwd, pdir, entry='cli'):
        self.proj, self.day, self.cwd, self.entry = proj, day, cwd, entry
        self.sid, self.pdir = uid(), pdir
        self.t = start
        self.lines, self.subs = [], {}
        self.branch = R.choice(['main', 'main', 'feat/checkout-v2', 'fix/%d' % R.randint(400, 520)])
        self.model = MAIN_MODEL(day)
        self.effort = R.choices(['high', 'xhigh', 'medium', 'max'], [60, 20, 15, 5])[0]
        self.mode = R.choices(['default', 'acceptEdits', 'plan'], [50, 38, 12])[0]
        self.last = None
        self.th = dict(ctx=0, out=0, pending=0, n=0, t1=None, base=R.randint(17000, 24000), ttl=3600, force=False, compacted=False)

    def rec(self, typ, t, lines=None, agent=None, **kw):
        d = dict(parentUuid=self.last, isSidechain=agent is not None, type=typ, uuid=uid(), timestamp=iso(t),
                 userType='external', entrypoint=self.entry, cwd=self.cwd, sessionId=self.sid,
                 version=VERSION(self.day), gitBranch=self.branch)
        if agent:
            d['agentId'] = agent
        d.update(kw)
        (lines if lines is not None else self.lines).append(d)
        if lines is None:
            self.last = d['uuid']
        return d

    # one API call on a thread: usage follows the cache (hit, expiry, compaction)
    def call(self, th, t0, gen, out, blocks, stop, model, lines=None, agent=None):
        inp = R.randint(3, 14)
        if th['n'] == 0:
            ctx = th['base'] + th['pending']
            cr = R.choice([0, 0, th['base'] - 6000]) if agent is None else 0
        elif th['compacted']:
            ctx = th['base'] + R.randint(14000, 21000) + th['pending']
            cr, th['compacted'] = th['base'] - 6000, False
        else:
            ctx = th['ctx'] + th['out'] + th['pending']
            expired = th['force'] or (t0 - th['t1']).total_seconds() > th['ttl']
            cr = 0 if expired else th['ctx']
            th['force'] = False
        cw = ctx - cr - inp
        think = int(out * R.uniform(0.1, 0.5)) if R.random() < 0.6 else 0
        usage = dict(input_tokens=inp, cache_creation_input_tokens=cw, cache_read_input_tokens=cr, output_tokens=out,
                     output_tokens_details=dict(thinking_tokens=think), service_tier='standard',
                     cache_creation=dict(ephemeral_5m_input_tokens=cw if th['ttl'] == 300 else 0,
                                         ephemeral_1h_input_tokens=cw if th['ttl'] == 3600 else 0))
        mid, rid = 'msg_' + uid().replace('-', '')[:24], 'req_' + uid().replace('-', '')[:24]
        t1 = t0 + dt.timedelta(seconds=gen)
        for i, b in enumerate(blocks):
            tb = t0 + dt.timedelta(seconds=gen * (0.55 + 0.45 * (i + 1) / len(blocks)))
            self.rec('assistant', tb, lines, agent, requestId=rid, effort=self.effort,
                     message=dict(model=model, id=mid, type='message', role='assistant', content=[b],
                                  stop_reason=stop, stop_sequence=None, usage=usage))
        th.update(ctx=ctx, out=out, pending=0, n=th['n'] + 1, t1=t1)
        return t1

    def result(self, t, tu, text, tur=None, lines=None, agent=None, err=False):
        self.rec('user', t, lines, agent, toolUseResult=tur or {},
                 message=dict(role='user', content=[dict(type='tool_result', tool_use_id=tu['id'], content=text, is_error=err)]))


def tool(name, **inp):
    return dict(type='tool_use', id='toolu_' + uid().replace('-', '')[:22], name=name, input=inp)


def text(s):
    return dict(type='text', text=s)


def pick_tool(s, files, cmds, in_sub=False):
    """One tool call and its result: (block, result text, toolUseResult, latency s, tokens added, error)."""
    r = R.random()
    f = R.choice(files)
    path = s.cwd + '/' + f
    if r < 0.34:
        n = int(min(90000, R.lognormvariate(8.9, 0.8)))
        lines = n // 42
        return tool('Read', file_path=path), filler('code', n), dict(type='text', file=dict(
            filePath=path, content='', numLines=lines, startLine=1, totalLines=lines)), R.uniform(0.05, 0.4), n / 4, False
    if r < 0.58:
        cmd = R.choice(cmds + ['git status', 'git diff --stat', 'ls ' + (os.path.dirname(f) or '.')])
        slow = any(k in cmd for k in ('test', 'build', 'plan', 'xcodebuild'))
        fail = slow and R.random() < 0.22
        n = int(R.uniform(3000, 22000) if slow else R.uniform(150, 2500))
        body = filler('go' if 'go ' in cmd else 'test', n) + ('\nFAIL\nExit code 1' if fail else '')
        lat = R.uniform(20, 140) if slow else R.uniform(0.2, 3)
        if in_sub and slow and R.random() < 0.03:
            lat = R.uniform(320, 520)                  # a slow test run in a subagent outlives its 5-minute cache
        return (tool('Bash', command=cmd, description='Run ' + cmd.split()[0]), body,
                dict(stdout=body[:200], stderr='', interrupted=False), lat, n / 4, False)
    if r < 0.78 and not in_sub:
        add = R.randint(2, 40)
        patch = [dict(oldStart=10, oldLines=3, newStart=10, newLines=3 + add,
                      lines=['-  old'] * R.randint(1, 6) + ['+  new'] * add)]
        return (tool('Edit', file_path=path, old_string='old', new_string='new'), 'The file %s has been updated.' % path,
                dict(filePath=path, structuredPatch=patch), R.uniform(0.05, 0.3), 180, False)
    if r < 0.9:
        n = int(R.uniform(400, 7000))
        return tool('Grep', pattern=R.choice(['cartTotal', 'func Charge', 'webhook', 'TODO', 'useSearch', 'retry']), path=s.cwd), \
            filler('grep', n), dict(mode='content', numFiles=R.randint(1, 30)), R.uniform(0.1, 0.8), n / 4, False
    if r < 0.96:
        n = int(R.uniform(200, 2400))
        return tool('Glob', pattern='**/*' + os.path.splitext(f)[1]), filler('grep', n), {}, R.uniform(0.05, 0.3), n / 4, False
    if in_sub:
        return pick_tool(s, files, cmds, in_sub)
    return (tool('TodoWrite', todos=[dict(content='step', status='in_progress')]), 'Todos have been modified successfully.',
            {}, 0.02, 60, False)


def subagent(s, launch, t, files, cmds):
    """A Task subagent: its own thread (5-minute cache), file and meta; returns when it finishes."""
    aid = 'a' + uid().replace('-', '')[:16]
    typ = launch['input']['subagent_type']
    model = 'claude-haiku-4-5-20251001' if typ == 'Explore' else s.model
    lines = []
    th = dict(ctx=0, out=0, pending=len(launch['input']['prompt']) / 4, n=0, t1=None, base=R.randint(7000, 11000),
              ttl=300, force=False, compacted=False)
    s.rec('user', t, lines, aid, message=dict(role='user', content=launch['input']['prompt']))
    n = R.randint(4, 22)
    for i in range(n):
        last = i == n - 1
        blocks, tus = [], []
        if not last:
            for _ in range(R.choice([1, 1, 2, 3])):
                tus.append(pick_tool(s, files, cmds, in_sub=True))
            blocks = [x[0] for x in tus]
        else:
            blocks = [text(filler('plain', R.randint(1500, 9000)))]
        t = s.call(th, t + dt.timedelta(seconds=R.uniform(0.5, 2)), R.uniform(2, 9), R.randint(90, 900 if not last else 2400),
                   blocks, 'end_turn' if last else 'tool_use', model, lines, aid)
        for b, body, tur, lat, tok, err in tus:
            s.result(t + dt.timedelta(seconds=lat), b, body, tur, lines, aid)
            th['pending'] += tok
        if tus:
            t = t + dt.timedelta(seconds=max(x[3] for x in tus))
    s.subs[aid] = (lines, dict(agentType=typ, description=launch['input']['description'], toolUseId=launch['id'],
                               spawnDepth=1, model=model))
    return t, R.randint(1500, 9000)


def turn(s, prompt, files, cmds, marathon=False, kind='typed'):
    th = s.th
    if kind == 'typed':
        content = prompt
        if R.random() < 0.05 and s.proj in ('acme-web', 'mobile-app'):
            content = [dict(type='image', source=dict(type='base64', media_type='image/png', data='iVBORw0KGgo=')), text(prompt)]
            th['pending'] += 1600
        s.rec('user', s.t, promptSource='typed', permissionMode=s.mode, message=dict(role='user', content=content))
    elif kind == 'sdk':
        s.rec('user', s.t, promptSource='sdk', message=dict(role='user', content=prompt))
    th['pending'] += len(prompt) / 4
    if R.random() < 0.08:
        snip = filler('code', R.randint(1500, 6000))
        s.rec('attachment', s.t, attachment=dict(type='edited_text_file', filename=s.cwd + '/' + R.choice(files), snippet=snip))
        th['pending'] += len(snip) / 4
    t0 = s.t
    n = R.randint(3, 14) if not marathon else R.randint(5, 15)
    t = s.t
    for i in range(n):
        last = i == n - 1
        tus = []
        if not last:
            if R.random() < 0.06 and s.entry == 'cli':
                d = R.choice(['Map the checkout flow', 'Find every caller of cartTotal', 'Survey the webhook handlers',
                              'Look for N+1 queries', 'Check test coverage gaps'])
                k = R.choice([1, 1, 1, 2, 3])
                for j in range(k):
                    tus.append((tool('Task', description=d if k == 1 else '%s (part %d)' % (d, j + 1),
                                     prompt='Research task: ' + d + '. Report file paths and a short summary. ' * 8,
                                     subagent_type=R.choice(['Explore', 'Explore', 'general-purpose'])), None, None, 0, 0, False))
            elif R.random() < 0.025 and s.entry == 'cli':
                q = 'Which approach do you prefer?'
                tus.append((tool('AskUserQuestion', questions=[dict(question=q, options=[dict(label='A'), dict(label='B')])]),
                            'User answered.', dict(questions=[dict(question=q)], answers={q: 'A'}), 'ask', 40, False))
            else:
                for _ in range(R.choice([1, 1, 1, 2, 2, 3])):
                    tus.append(pick_tool(s, files, cmds))
        blocks = [x[0] for x in tus] if tus else [text(filler('plain', R.randint(300, 2500)))]
        if tus and R.random() < 0.4:
            blocks.insert(0, text('Let me look at that.'))
        gen = R.uniform(3, 11) + (R.uniform(10, 40) if last else 0)
        t = s.call(th, t + dt.timedelta(seconds=R.uniform(0.3, 1.5)), gen, R.randint(120, 1400) if not last else R.randint(400, 3200),
                   blocks, 'tool_use' if tus else 'end_turn', s.model)
        done = t
        for b, body, tur, lat, tok, err in tus:
            if b['name'] == 'Task':
                end, ret = subagent(s, b, t + dt.timedelta(seconds=1), files, cmds)
                s.result(end, b, filler('plain', ret), dict(status='completed', agentId='x', totalDurationMs=int((end - t).total_seconds() * 1000)))
                th['pending'] += ret / 4
                done = max(done, end)
            elif lat == 'ask':
                wait = think_gap()
                s.result(t + dt.timedelta(seconds=wait), b, body, tur)
                done = max(done, t + dt.timedelta(seconds=wait))
                th['pending'] += tok
            else:
                s.result(t + dt.timedelta(seconds=lat), b, body, tur)
                th['pending'] += tok
                done = max(done, t + dt.timedelta(seconds=lat))
                if b['name'] == 'Edit':
                    ms = R.randint(250, 1900)
                    bad = R.random() < 0.04
                    s.rec('attachment', t + dt.timedelta(seconds=lat + ms / 1000), attachment=dict(
                        type='hook_non_blocking_error' if bad else 'hook_success', hookName='PostToolUse:Edit', toolUseID=b['id'],
                        hookEvent='PostToolUse', content='', stdout='', stderr='prettier: SyntaxError: Unexpected token (42:7)' if bad else '',
                        exitCode=1 if bad else 0, command='~/.claude/hooks/format.sh', durationMs=ms))
        t = done
        if not last and R.random() < 0.012:
            s.rec('user', t + dt.timedelta(seconds=4), message=dict(role='user', content=[text('[Request interrupted by user]')]))
            break
        if th['ctx'] > 185000:
            s.rec('system', t + dt.timedelta(seconds=2), subtype='compact_boundary', content='Conversation compacted', level='info',
                  compactMetadata=dict(trigger='auto', preTokens=th['ctx'], postTokens=th['base'] + 18000))
            th['compacted'] = True
    s.rec('system', t + dt.timedelta(seconds=1), subtype='turn_duration', durationMs=int((t - t0).total_seconds() * 1000), messageCount=n * 2)
    s.rec('system', t + dt.timedelta(seconds=1.2), subtype='stop_hook_summary', hookCount=1, level='suggestion',
          hookInfos=[dict(command='~/.claude/hooks/notify.sh', durationMs=R.randint(60, 240))], hookErrors=[],
          hookAdditionalContext=[], preventedContinuation=False, stopReason='', hasOutput=False)
    s.t = t + dt.timedelta(seconds=2)


def think_gap():
    r = R.random()
    if r < 0.55:
        return R.uniform(15, 120)
    if r < 0.80:
        return R.uniform(120, 600)
    if r < 0.93:
        return R.uniform(600, 3300)
    return R.uniform(3700, 10800)          # a meeting or lunch: past the 1-hour cache


def command(s, name, args=''):
    s.rec('user', s.t, message=dict(role='user', content='<command-name>/%s</command-name>\n<command-message>%s</command-message>\n'
                                                           '<command-args>%s</command-args>' % (name, name, args)))


def run_session(proj, day, start, cwd, pdir):
    name, _, files, cmds, tasks = proj
    s = Session(name, day, start, cwd, pdir)
    title, first = R.choice(tasks)
    s.rec('attachment', s.t, attachment=dict(type='skill_listing', content=filler('plain', 3800), skillCount=14, isInitial=True, names=[]))
    if name == 'acme-web':
        ctx = filler('plain', 1400)
        s.rec('attachment', s.t, attachment=dict(type='hook_additional_context', content=[ctx], hookName='SessionStart',
                                                 toolUseID='SessionStart', hookEvent='SessionStart'))
        s.th['pending'] += len(ctx) / 4
    s.th['pending'] += 950
    r = R.random()
    kind = 'quick' if r < 0.22 or (name == 'dotfiles' and r < 0.5) else 'marathon' if r < 0.34 and name != 'dotfiles' else 'work'
    if R.random() < 0.08:
        command(s, 'review', str(R.randint(470, 520)))
        s.rec('user', s.t, isMeta=True, message=dict(role='user', content=filler('plain', 2600)))
        turns = 1
    else:
        turns = R.randint(1, 2) if kind == 'quick' else R.randint(10, 22) if kind == 'marathon' else R.randint(3, 11)
    switch_at = R.randint(2, turns) if turns > 3 and R.random() < 0.06 else None
    mcp_at = R.randint(2, turns) if turns > 3 and R.random() < 0.07 else None
    resume = kind != 'quick' and R.random() < 0.18
    for i in range(turns):
        if i:
            s.t += dt.timedelta(seconds=think_gap())
        if resume and i == turns // 2:
            s.t = s.t.replace(hour=9, minute=R.randint(5, 55)) + dt.timedelta(days=1 if s.t.weekday() < 4 else 3)
            s.rec('user', s.t, message=dict(role='user', content='<command-name>/resume</command-name>'))
        if i == switch_at:
            s.model = 'claude-sonnet-5' if 'opus' in s.model else 'claude-opus-5-5'
            command(s, 'model', s.model.split('-')[1])
            s.th['force'] = True
        if i == mcp_at:
            s.rec('attachment', s.t, attachment=dict(type='deferred_tools_delta', addedNames=['mcp__linear__create_issue', 'mcp__linear__search'],
                                                     addedLines=['mcp__linear__create_issue', 'mcp__linear__search'], removedNames=[]))
            s.th['force'] = True
        if i > 4 and s.th['ctx'] > 90000 and R.random() < 0.08:
            command(s, 'compact')
            s.rec('system', s.t, subtype='compact_boundary', content='Conversation compacted', level='info',
                  compactMetadata=dict(trigger='manual', preTokens=s.th['ctx'], postTokens=s.th['base'] + 16000))
            s.th['compacted'] = True
        prompt = first if i == 0 else R.choice(FOLLOW)
        turn(s, prompt, files, cmds, marathon=kind == 'marathon')
        if s.t.hour >= 21 and not resume:
            break
    s.rec('ai-title', s.t, aiTitle=title)
    return s


def run_sdk(day, start, cwd, pdir):
    s = Session('billing-api', day, start, cwd, pdir, entry='sdk-cli')
    s.model, s.effort, s.branch = 'claude-sonnet-5', 'medium', 'main'
    turn(s, "Summarize last night's CI failures on main and draft an issue for each new one", P[1][2], ['gh run list --branch main', 'go test ./...'],
         kind='sdk')
    s.rec('ai-title', s.t, aiTitle='Nightly CI triage')
    return s


def write(s):
    d = os.path.join(CLAUDE, 'projects', s.pdir)
    os.makedirs(d, exist_ok=True)
    # Claude Code writes ai-title and similar records without a timestamp
    with open(os.path.join(d, s.sid + '.jsonl'), 'w') as fh:
        for x in s.lines:
            if x['type'] == 'ai-title':
                x = dict(type='ai-title', aiTitle=x['aiTitle'], sessionId=s.sid)
            fh.write(json.dumps(x) + '\n')
    for aid, (lines, meta) in s.subs.items():
        sd = os.path.join(d, s.sid, 'subagents')
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, 'agent-%s.jsonl' % aid), 'w') as fh:
            fh.writelines(json.dumps(x) + '\n' for x in lines)
        with open(os.path.join(sd, 'agent-%s.meta.json' % aid), 'w') as fh:
            json.dump(meta, fh)


def main():
    shutil.rmtree(os.path.join(CLAUDE, 'projects'), ignore_errors=True)
    os.makedirs(CLAUDE, exist_ok=True)
    wt = 'task-3fa9c1'
    n = 0
    for day in range(DAYS):
        date = FIRST + dt.timedelta(days=day)
        weekend = date.weekday() >= 5
        if not weekend:
            s = run_sdk(day, date.replace(hour=2, minute=R.randint(0, 20)), HOME + '/code/billing-api', '-Users-alex-code-billing-api')
            write(s)
        k = (R.choice([0, 0, 1]) if weekend else R.choice([1, 2, 2, 3, 3, 4]))
        t = date.replace(hour=R.choice([8, 9, 9, 10]), minute=R.randint(0, 59))
        for _ in range(k):
            proj = R.choices(P, [p[1] for p in P])[0]
            cwd, pdir = HOME + '/code/' + proj[0], '-Users-alex-code-' + proj[0]
            if proj[0] == 'dotfiles':
                cwd, pdir = HOME + '/dotfiles', '-Users-alex-dotfiles'
            if proj[0] == 'acme-web' and 30 <= day <= 40 and R.random() < 0.5:
                cwd, pdir = HOME + '/code/acme-web/.claude/worktrees/' + wt, '-Users-alex-code-acme-web--claude-worktrees-' + wt
            s = run_session(proj, day, t, cwd, pdir)
            write(s)
            n += 1
            t = max(t, s.t) + dt.timedelta(minutes=R.randint(5, 90))
            if t.hour >= 20:
                break
    json.dump({'model': 'opus', 'hooks': {
        'SessionStart': [{'hooks': [{'type': 'command', 'command': '~/.claude/hooks/git-context.sh'}]}],
        'PostToolUse': [{'matcher': 'Edit|Write|MultiEdit', 'hooks': [{'type': 'command', 'command': '~/.claude/hooks/format.sh'}]}],
        'Stop': [{'hooks': [{'type': 'command', 'command': '~/.claude/hooks/notify.sh'}]}]},
        'permissions': {'allow': ['Bash(pnpm test:*)', 'Bash(go test:*)', 'Bash(git status)', 'Bash(git diff:*)']}},
        open(os.path.join(CLAUDE, 'settings.json'), 'w'), indent=2)
    json.dump({'mcpServers': {'github': {'type': 'http', 'url': 'https://api.githubcopilot.com/mcp/'},
                              'linear': {'type': 'http', 'url': 'https://mcp.linear.app/mcp'}}, 'projects': {}},
              open(os.path.join(CLAUDE, '.claude.json'), 'w'), indent=2)
    print('%d sessions written under %s' % (n, CLAUDE))
    history()


def history():
    """Three made-up earlier reports in the report folder's score history, so the next report shows progress (SV9)."""
    out = os.path.join(HOME, '.claude-usage')
    os.makedirs(os.path.join(out, 'history'), exist_ok=True)
    with open(os.path.join(out, '.claude-usage'), 'w') as fh:            # the report folder's marker (layout.MARKER)
        fh.write('This folder holds claude-usage reports (private data: prompt snippets, paths, your setup).\n')
    runs = []
    for end, score, grade, spend, hit in (('2026-09-07', 72, 'B+', 275.0, 91.5), ('2026-09-14', 77, 'A-', 262.0, 92.2),
                                          ('2026-09-21', 81, 'A-', 255.0, 92.6)):
        start = (dt.date.fromisoformat(end) - dt.timedelta(days=DAYS - 1)).isoformat()
        runs.append({'generated': end + ' 09:30:00', 'start': max(start, FIRST.date().isoformat()), 'end': end, 'days': 60,
                     'score': score, 'grade': grade, 'spend_30d': spend, 'hit_rate': hit})
    with open(os.path.join(out, 'history', 'scores.json'), 'w') as fh:
        json.dump(runs, fh, indent=1)


main()
