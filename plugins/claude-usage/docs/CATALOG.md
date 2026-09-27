# Claude Code session metadata — extraction catalog

Built by scanning `~/.claude/projects` on 2026-09-24: 10 project dirs, 91 main session transcripts,
64 subagent transcripts, 26,507 JSONL lines, 116 MB, CLI versions 2.1.236–2.1.281.

Every block below is a self-contained bash command that uses only `jq`, `find`, `awk`, `sort` and the like
(no LLM). Run `. ./lib.sh` first; it defines `ALL`, `MAIN`, `SUB`, `EACH`, `PERSESSION`, `ASST`, `TOOLS`
and `$JQDEFS`. Every metric has an ID (`### X00 — name`), followed by one ```bash block, so a
runner script can pull the blocks out by ID.

---

## 0. Things to know before you parse

| # | Gotcha | What to do |
|---|---|---|
| G1 | Project dir names start with `-` (`-Users-yuvalsosman-Dev-claudepit`), which breaks `ls`/`cat` globs | Use `find` or prefix `./` |
| G2 | **Each assistant content block is written as its own line** and repeats `message.usage`. 8,939 assistant lines map to only 4,647 real API calls. Intermediate lines carry partial `output_tokens` | Dedup on `message.id + requestId` and keep max `output_tokens` (`ASST` does this). Summing raw lines roughly doubles the token counts |
| G3 | The same subagent transcript can be copied into two sessions' `subagents/` dirs (seen once) | Dedup key G2 covers it |
| G4 | `message.model == "<synthetic>"` = client-generated error messages (not logged in, sleep), with zero usage | Exclude from token stats, count as errors |
| G5 | Subagent lines live in `<session>/subagents/agent-<agentId>.jsonl`, have `isSidechain:true` and `agentId`, and share the **parent's** `sessionId` | Group by `agentId` for per-agent, by `sessionId` to roll into the parent |
| G6 | Some fields exist only on newer CLI versions (`message.usage.speed`, `iterations`, `output_tokens_details`, `diagnostics`) | Always use `//0` / `//null` defaults |
| G7 | **The system prompt text, CLAUDE.md contents and tool JSON schemas are NOT logged** (except the rare `prompt_snapshot` / `deferred_tools_record` attachments) | Use H02 (tokens of the first API call) as the "fixed context" proxy, and H03–H08 for the pieces that are logged |
| G8 | Dollar cost is **not** logged (the only 2 `cost-state` rows are zeros) | Compute it from tokens × a price table you supply (B16) |
| G9 | No compaction (`compact_boundary`) records appear in this data yet | Add a check if they show up later |
| G10 | Timestamps are ISO UTC with ms (`2026-09-10T10:38:02.346Z`) | `ms` in `$JQDEFS` converts them to epoch ms |

---

## 1. Data sources (file inventory)

| Path | Format | What it holds |
|---|---|---|
| `projects/<proj>/<sessionId>.jsonl` | JSONL | Main transcript for one session: everything below |
| `projects/<proj>/<sessionId>/subagents/agent-<id>.jsonl` | JSONL | Transcript of one subagent (same schema, `isSidechain:true`) |
| `projects/<proj>/<sessionId>/subagents/agent-<id>.meta.json` | JSON | `agentType, description, toolUseId, spawnDepth, model?, spawnedWithWorktree?, worktreeBranch?, worktreePath?` |
| `projects/<proj>/<sessionId>/tool-results/*.txt` | text | Large tool outputs persisted to disk (`toolUseResult.persistedOutputPath`) |
| `projects/<proj>/<sessionId>/auto-mode-classifier-error.txt` | text | Auto-mode classifier failures |
| `projects/<proj>/memory/*.md`, `memory/log.json` | md/JSON | Auto-memory files; `log.json` = memory write log (written by your claudepit hook) |
| `projects/<proj>/summary/<sessionId>.json` | JSON | `{version, bullets[], updatedAt}`, written by claudepit |
| `projects/<proj>/tasks/<id>/{task.json,spec.md,review.md,brainstorm.yaml,…}` | mixed | claudepit task pipeline artifacts |

### JSONL record types (`.type`) and their fields

| type | count | Key fields |
|---|---|---|
| `assistant` | 8939 | `message.{id,model,stop_reason,content[],usage{…},diagnostics}`, `requestId, effort, perTurnEffort, advisorModel, attributionSkill, attributionAgent, isApiErrorMessage, error, agentId, isSidechain` + common envelope |
| `user` | 5360 | `message.content` (string or blocks: text/image/tool_result), `toolUseResult{…}`, `promptId, promptSource, origin.kind, permissionMode, isMeta, imagePasteIds, toolDenialKind, userFeedback, interruptedMessageId, sourceToolAssistantUUID, turnCompanion, classifierMetaLines` |
| `attachment` | 4566 | `attachment.type` = one of the 29 injected-context kinds below |
| `system` | 663 | `subtype` = `stop_hook_summary`, `turn_duration`, `away_summary`, `local_command`, `informational` |
| `permission-mode` | 899 | `permissionMode` (default/auto/plan/acceptEdits) |
| `mode` | 1093 | `mode` (normal) |
| `last-prompt` | 1093 | `lastPrompt, leafUuid` |
| `ai-title` | 841 | `aiTitle` (auto session title) |
| `atis-latch` | 1112 | `atis` |
| `bridge-session` | 1080 | `bridgeSessionId, lastSequenceNum, ownerAccountUuid, ownerOrganizationUuid` (Remote Control / web bridge) |
| `queue-operation` | 274 | `operation` (enqueue/dequeue/remove), `content` (queued prompt or task-notification) |
| `agent-name` | 208 | `agentName` (named sessions, e.g. `auto-run-code-review`) |
| `file-history-snapshot` | 191 | `snapshot.trackedFileBackups{<path>:{backupFileName,version,backupTime}}` |
| `file-history-delta` | 181 | `trackingPath, backup{version,backupTime}` |
| `pr-link` | 5 | `prNumber, prUrl, prRepository` |
| `cost-state` | 2 | `totalCostUSD, totalAPIDuration, totalToolDuration, totalLinesAdded/Removed, modelUsage` (rare, mostly empty) |

Common envelope on user/assistant/attachment/system: `uuid, parentUuid, sessionId, timestamp, cwd, gitBranch, version, entrypoint (cli/sdk-cli/sdk-ts), userType, slug, isSidechain, agentId`.

### `message.usage` (per API call)
`input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, cache_creation.{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}, output_tokens_details.thinking_tokens, server_tool_use.{web_search_requests, web_fetch_requests}, service_tier, speed, inference_geo, iterations[]{type,input_tokens,output_tokens,cache_*}`
plus `message.diagnostics.cache_miss_reason.{type, cache_missed_input_tokens}` (types seen: `messages_changed`, `previous_message_not_found`, `tools_changed`).

### Attachment types (`attachment.type`)
`total_tokens_reminder, hook_additional_context, hook_success, hook_non_blocking_error, hook_cancelled, hook_system_message, skill_listing, deferred_tools_delta, deferred_tools_record, agent_listing_delta, mcp_instructions_delta, auto_mode, auto_mode_exit, diagnostics, queued_command, command_permissions, plan_mode, plan_mode_exit, plan_mode_reentry, edited_text_file, read_truncation_notice, prompt_snapshot, environment, session_context, remote_session_change, model, date, date_change, credential_org`

### `toolUseResult` shape per tool
| Tool | Fields |
|---|---|
| Bash | `stdout, stderr, interrupted, isImage, noOutputExpected, returnCodeInterpretation?, persistedOutputPath/Size?, backgroundTaskId?, dangerouslyDisableSandbox?, gitOperation{commit,push,branch,pr}?, timedOutAfterMs?` |
| Read | `type, file{filePath,content,numLines,startLine,totalLines,truncatedByTokenCap?}` or image `file{base64,type,originalSize,dimensions{…}}` |
| Edit | `filePath, oldString, newString, originalFile, replaceAll, structuredPatch[]{oldStart,oldLines,newStart,newLines,lines[]}, userModified, memdirStamped?` |
| Write | `type(create/update), filePath, content, originalFile, structuredPatch` |
| Agent | `agentId, status, resolvedModel, isAsync, description, prompt, outputFile` |
| Skill | `commandName, success, allowedTools?` |
| ToolSearch | `query, matches[], total_deferred_tools` |
| WebFetch | `url, code, codeText, bytes, durationMs, result` |
| WebSearch | `query, results[], searchCount, durationSeconds` |
| AskUserQuestion | `questions[], answers{}, annotations{}` |
| SendMessage | `message, pin, resumedAgentId, success` |
| ExitPlanMode | `plan, filePath, isAgent` |
| TaskOutput / TaskStop / Monitor | `task{…}`, `task_id`, `taskId, timeoutMs, persistent` |
| (error) | plain string, e.g. `"Error: Exit code 1"` |

---

## A. Inventory & sessions

### A01 — Session count per project (main vs subagent)
```bash
find "$P" -name '*.jsonl' | awk -F'/projects/' '{split($2,a,"/"); kind=($0~/subagents/)?"sub":"main"; c[a[1]"\t"kind]++} END{for(k in c) print c[k]"\t"k}' | sort -k2
```

### A02 — Transcript file sizes and line counts
```bash
find "$P" -name '*.jsonl' -exec wc -lc {} + | sort -k2 -n | tail -20
```

### A03 — Record type counts
```bash
ALL | jq -r '.type' | sort | uniq -c | sort -rn
```

### A04 — System subtypes and attachment types
```bash
ALL | jq -r 'if .type=="system" then "system/"+.subtype elif .type=="attachment" then "attachment/"+.attachment.type else empty end' | sort | uniq -c | sort -rn
```

### A05 — CLI version and entrypoint mix
```bash
ALL | jq -r 'select(.version)|[.version,.entrypoint]|@tsv' | sort | uniq -c
```

### A06 — Per-session summary (start, end, wall duration, titles, branch, cwd, counts)
```bash
PERSESSION "$JQDEFS"'{
  sid:(input_filename|split("/")|last|sub(".jsonl$";"")), proj:proj,
  start:(map(.timestamp//empty)|min), end:(map(.timestamp//empty)|max),
  wall_min:(((map(.timestamp//empty)|max|ms)-(map(.timestamp//empty)|min|ms))/60000|floor),
  title:(map(select(.type=="ai-title").aiTitle)|last), name:(map(select(.type=="agent-name").agentName)|last),
  cwd:(map(.cwd//empty)|unique), branches:(map(.gitBranch//empty)|unique), entrypoint:(map(.entrypoint//empty)|unique),
  lines:length, prompts:(map(select(.type=="user" and (.promptSource|IN("typed","queued"))))|length),
  api_calls:(map(select(.type=="assistant")|.message.id)|unique|length),
  models:(map(select(.type=="assistant")|.message.model)|unique),
  subagents:(map(select(.type=="assistant" and .message.content[]?.name=="Agent"))|length)}' 2>/dev/null
```

### A07 — Sessions per day
```bash
PERSESSION 'map(.timestamp//empty)|min|.[:10]' | sort | uniq -c
```

---

## B. Tokens & caching (all from `ASST`, deduped)

### B01 — Tokens by model (input, output, cache read, 5m/1h cache write, thinking, cache hit rate)
```bash
ASST | jq -sc "$JQDEFS"'group_by(.m)|map({model:.[0].m, calls:length,
  input:(map(.u.input_tokens)|add), output:(map(.u.output_tokens)|add),
  cache_read:(map(.u.cache_read_input_tokens//0)|add), cache_write_5m:(map(.u|cw5)|add), cache_write_1h:(map(.u|cw1)|add),
  thinking:(map(.u.output_tokens_details.thinking_tokens//0)|add)}
  | .+{hit_pct:(if (.input+.cache_read+.cache_write_5m+.cache_write_1h)>0 then (1000*.cache_read/(.input+.cache_read+.cache_write_5m+.cache_write_1h)|round/10) else 0 end)})|.[]'
```

### B02 — Tokens per session (main + its subagents rolled up)
```bash
ASST | jq -sc 'group_by(.sid)|map({sid:.[0].sid, proj:.[0].proj, calls:length, sub_calls:(map(select(.side))|length),
  input:(map(.u.input_tokens)|add), output:(map(.u.output_tokens)|add),
  cache_read:(map(.u.cache_read_input_tokens//0)|add), cache_write:(map(.u.cache_creation_input_tokens//0)|add)})|sort_by(-.cache_read)|.[]'
```

### B03 — Tokens per day and per model
```bash
ASST | jq -sc 'group_by([.ts[:10],.m])|map({day:.[0].ts[:10], model:.[0].m, calls:length, out:(map(.u.output_tokens)|add),
  cr:(map(.u.cache_read_input_tokens//0)|add), cw:(map(.u.cache_creation_input_tokens//0)|add)})|.[]'
```

### B04 — Tokens per hour of day (local time)
```bash
ASST | jq -r '(.ts[:19]+"Z"|fromdate|localtime|strftime("%H")) + "\t" + (.u.output_tokens|tostring)' | awk '{c[$1]++; o[$1]+=$2} END{for(h in c) print h"\t"c[h]" calls\t"o[h]" out"}' | sort
```

### B05 — Context size per call (timeline) and peak context per session
```bash
ASST | jq -r "$JQDEFS"'[.sid,.ts,.m,(.u|ctx),.u.output_tokens,(.side|tostring)]|@tsv' | sort -k1,1 -k2,2 > /tmp/ctx_timeline.tsv
awk -F'\t' '$6=="false"{if($4>p[$1])p[$1]=$4} END{for(s in p) print p[s]"\t"s}' /tmp/ctx_timeline.tsv | sort -rn | head
```

### B06 — Cache misses: calls that re-wrote cache after the first call of a session/agent
```bash
ASST | jq -sc 'group_by([.sid,.agent])|map(sort_by(.ts)|.[1:][]|select((.u.cache_creation_input_tokens//0) > 20000)
  |{sid,agent,ts,m,cache_write:.u.cache_creation_input_tokens,cache_read:.u.cache_read_input_tokens,reason:.diag.cache_miss_reason.type})|.[]'
```
A large write straight after a `/compact` is not a miss. Compaction replaces the conversation, so skip calls with a `system` record of subtype `compact_boundary` between them and the previous call:
```bash
ALL | jq -r 'select(.type=="system" and .subtype=="compact_boundary")|[.sessionId,.timestamp,.compactMetadata.preTokens,.compactMetadata.postTokens]|@tsv'
```

### B07 — Explicit cache-miss diagnostics (reason, tokens missed)
```bash
ASST | jq -r 'select(.diag.cache_miss_reason)|[.diag.cache_miss_reason.type,(.diag.cache_miss_reason.cache_missed_input_tokens//0)]|@tsv' | awk '{c[$1]++; t[$1]+=$2} END{for(k in c) print k"\t"c[k]" calls\t"t[k]" tokens"}'
```

### B08 — Idle gaps that likely expired the 5-minute cache (> 5 min between consecutive calls)
```bash
ASST | jq -sc "$JQDEFS"'group_by([.sid,.agent])|map(sort_by(.ts)|[range(1;length) as $i|{sid:.[$i].sid, gap_s:(((.[$i].ts|ms)-(.[$i-1].ts|ms))/1000|floor), cw:.[$i].u.cache_creation_input_tokens}]|.[]|select(.gap_s>300))|.[]'
```

### B09 — Cache-write TTL split (5m vs 1h) by model
```bash
ASST | jq -sc "$JQDEFS"'group_by(.m)|map({m:.[0].m, w5m:(map(.u|cw5)|add), w1h:(map(.u|cw1)|add)})|.[]'
```

### B10 — Output tokens by stop_reason, and share that is thinking
```bash
ASST | jq -sc 'group_by(.stop)|map({stop:.[0].stop, calls:length, out:(map(.u.output_tokens)|add), thinking:(map(.u.output_tokens_details.thinking_tokens//0)|add)})|.[]'
```

### B11 — Main thread vs subagent token split
```bash
ASST | jq -sc 'group_by(.side)|map({subagent:.[0].side, calls:length, out:(map(.u.output_tokens)|add), cr:(map(.u.cache_read_input_tokens//0)|add), cw:(map(.u.cache_creation_input_tokens//0)|add)})|.[]'
```

### B12 — Tokens attributed to a skill (`attributionSkill`) / agent type (`attributionAgent`)
```bash
ASST | jq -sc '[group_by(.skill)[]|{by:"skill",key:.[0].skill,calls:length,out:(map(.u.output_tokens)|add),cr:(map(.u.cache_read_input_tokens//0)|add)}]
  + [group_by(.agt)[]|{by:"agent",key:.[0].agt,calls:length,out:(map(.u.output_tokens)|add),cr:(map(.u.cache_read_input_tokens//0)|add)}]|.[]'
```

### B13 — Tokens by effort level
```bash
ASST | jq -sc 'group_by(.effort)|map({effort:.[0].effort, calls:length, out:(map(.u.output_tokens)|add), thinking:(map(.u.output_tokens_details.thinking_tokens//0)|add)})|.[]'
```

### B14 — Service tier / speed / inference geo / server tool use
```bash
ASST | jq -r '[.u.service_tier,.u.speed,.u.inference_geo,(.u.server_tool_use.web_search_requests//0),(.u.server_tool_use.web_fetch_requests//0)]|@tsv' | sort | uniq -c
```

### B15 — Multi-iteration calls (`usage.iterations` length > 1)
```bash
ASST | jq -c 'select((.u.iterations|length)>1)|{sid,ts,m,iters:(.u.iterations|map({type,input_tokens,output_tokens}))}'
```

### B16 — Estimated cost (you supply prices.json, USD per million tokens)
```bash
# prices.json example shape (fill with current prices, don't guess):
# {"claude-opus-5":{"in":0,"out":0,"cr":0,"cw5m":0,"cw1h":0}, ...}
[ -f prices.json ] && ASST | jq -sc --slurpfile p prices.json "$JQDEFS"'map(. as $c | ($p[0][$c.m] // null) as $r | select($r)
  | {m, sid, usd:(($c.u.input_tokens*$r.in + $c.u.output_tokens*$r.out + ($c.u.cache_read_input_tokens//0)*$r.cr + ($c.u|cw5)*$r.cw5m + ($c.u|cw1)*$r.cw1h)/1e6)})
  | group_by(.m)|map({m:.[0].m, usd:(map(.usd)|add)})|.[]'
```

---

## C. Models & effort

### C01 — API calls per model (deduped) and per session
```bash
ASST | jq -r '[.sid,.m]|@tsv' | sort | uniq -c | sort -k2
```

### C02 — `/model` and `/effort` switches (slash commands in user text)
```bash
ALL | jq -r 'select(.type=="user")|.timestamp as $t|.sessionId as $s|(.message.content|if type=="string" then . else ([.[]?|select(.type=="text")|.text]|join(" ")) end)
  | select(test("<command-name>/(model|effort)"))|[$t,$s,(capture("<command-name>(?<c>[^<]*)").c),((try capture("<command-args>(?<a>[^<]*)").a catch "")//"")]|@tsv'
```

### C03 — perTurnEffort / advisorModel / serverClassifierRequest usage
```bash
ALL | jq -c 'select(.type=="assistant" and (.perTurnEffort or .advisorModel or .serverClassifierRequest))|{ts:.timestamp,perTurnEffort,advisorModel,cls:(.serverClassifierRequest!=null)}'
```

### C04 — Model identity attachments (marketing name, cutoff) per session
```bash
ALL | jq -r 'select(.attachment.type=="model")|[.sessionId,.attachment.identity.modelId,.attachment.identity.marketingName]|@tsv'
```

---

## D. Tool calls (all from `TOOLS`)

### D01 — Calls per tool
```bash
TOOLS | jq -r '.name' | sort | uniq -c | sort -rn
```

### D02 — Calls per tool per session
```bash
TOOLS | jq -r '[.sid,.name]|@tsv' | sort | uniq -c | sort -k2,2 -k1,1rn
```

### D03 — Input and output size per tool (bytes; ≈ /4 for tokens)
```bash
TOOLS | jq -r '[.name,.in_bytes,(.out_bytes//0)]|@tsv' | awk -F'\t' '{c[$1]++; i[$1]+=$2; o[$1]+=$3; if($3>m[$1])m[$1]=$3} END{for(k in c) printf "%s\t%d calls\tin=%d\tout=%d\tavg_out=%d\tmax_out=%d\n",k,c[k],i[k],o[k],o[k]/c[k],m[k]}' | sort -t= -k3 -rn
```

### D04 — Tool latency (tool_use timestamp → tool_result timestamp; AskUserQuestion/ExitPlanMode/EnterPlanMode excluded — their latency is your reply time, see I08)
```bash
TOOLS | jq -r 'select(.ms and (.name|IN("AskUserQuestion","ExitPlanMode","EnterPlanMode")|not))|[.name,.ms]|@tsv' | sort -k1,1 -k2,2n | awk -F'\t' '{a[$1]=a[$1]" "$2; c[$1]++; s[$1]+=$2} END{for(k in a){n=split(a[k],v," "); printf "%s\tn=%d\tavg=%dms\tp50=%dms\tmax=%dms\n",k,c[k],s[k]/c[k],v[int(n/2)+1],v[n]}}'
```

### D05 — Tool errors (is_error) by tool, with the error text
```bash
TOOLS | jq -r 'select(.is_error==true)|[.name,(.result_meta.text//(.result_meta|tostring))[:120]]|@tsv' | sort | uniq -c | sort -rn | head -40
```

### D06 — Parallel tool calls per API call
```bash
ASST | jq -r '.ntools' | sort -n | uniq -c
```

### D07 — Bash: command head-word frequency
```bash
TOOLS | jq -r 'select(.name=="Bash")|.input.command|split("\n")[0]|ltrimstr("cd ")|split(" ")[0]' | sort | uniq -c | sort -rn | head -40
```

### D08 — Bash: interrupted, background, sandbox-disabled, timed out, persisted (large) output, run_in_background
```bash
TOOLS | jq -r 'select(.name=="Bash")|.result_meta as $r|[(if $r.interrupted then "interrupted" else empty end),(if $r.backgroundTaskId then "background" else empty end),(if $r.dangerouslyDisableSandbox then "sandbox_off" else empty end),(if $r.timedOutAfterMs then "timed_out" else empty end),(if $r.persistedOutputPath then "persisted_output" else empty end),(if $r.returnCodeInterpretation then "rc:"+$r.returnCodeInterpretation else empty end)][]' | sort | uniq -c
```

### D09 — Bash: git operations detected by the harness (commit/push/branch/pr)
```bash
TOOLS | jq -c 'select(.result_meta.gitOperation)|{sid,ts,git:.result_meta.gitOperation}'
```

### D10 — Read: files read most, re-reads, truncations, images
```bash
TOOLS | jq -r 'select(.name=="Read")|.input.file_path' | sort | uniq -c | sort -rn | head -25
ALL | jq -r 'select((.toolUseResult|objects|.file.truncatedByTokenCap) or .attachment.type=="read_truncation_notice")|.timestamp' | wc -l
ALL | jq -r 'select(.toolUseResult|objects|.file.base64)|[.toolUseResult.file.type,.toolUseResult.file.originalSize,.toolUseResult.file.dimensions.originalWidth,.toolUseResult.file.dimensions.originalHeight]|@tsv'
```

### D11 — Edit/Write: files changed, lines added/removed (from structuredPatch)
```bash
ALL | jq -r 'select(.type=="user" and (.toolUseResult|objects|.structuredPatch))|.toolUseResult as $r|[$r.filePath,([$r.structuredPatch[].lines[]|select(startswith("+"))]|length),([$r.structuredPatch[].lines[]|select(startswith("-"))]|length)]|@tsv' \
 | awk -F'\t' '{n[$1]++; a[$1]+=$2; r[$1]+=$3; A+=$2; R+=$3} END{for(f in n) print n[f]" edits\t+"a[f]"\t-"r[f]"\t"f; print "TOTAL\t+"A"\t-"R > "/dev/stderr"}' | sort -rn | head -30
```

### D12 — MCP tool calls by server
```bash
TOOLS | jq -r 'select(.name|startswith("mcp__"))|.name|split("__")[1]' | sort | uniq -c
```

### D13 — ToolSearch (deferred-tool loading): queries, matches, catalog size
```bash
TOOLS | jq -r 'select(.name=="ToolSearch")|[.sid,.input.query,(.result_meta.matches|length),.result_meta.total_deferred_tools]|@tsv'
```

### D14 — WebFetch / WebSearch details
```bash
TOOLS | jq -r 'select(.name=="WebFetch" or .name=="WebSearch")|[.name,(.input.url//.input.query),(.result_meta.code//""),(.result_meta.bytes//""),(.result_meta.durationMs//.result_meta.durationSeconds//"")]|@tsv'
```

### D15 — AskUserQuestion: questions asked and answers picked
```bash
ALL | jq -c 'select(.toolUseResult|objects|.questions)|{ts:.timestamp, q:[.toolUseResult.questions[].question[:80]], a:.toolUseResult.answers}'
```

### D16 — Permission denials (kind + user feedback)
```bash
ALL | jq -r 'select(.toolDenialKind)|[.timestamp,.toolDenialKind,(.userFeedback//"")[:120]]|@tsv'
TOOLS | jq -r 'select((.result_meta.text//"")|test("rejected|Blocked:"))|[.name,.result_meta.text[:100]]|@tsv' | sort | uniq -c
```

### D17 — Persisted large tool outputs on disk
```bash
find "$P" -path '*tool-results*' -type f -exec stat -f '%z %N' {} + | sort -rn | head
```

---

## E. Subagents

### E01 — Agent tool invocations (type, model, background, description)
```bash
TOOLS | jq -r 'select(.name=="Agent")|[.sid,(.input.subagent_type//"general-purpose"),(.input.model//""),(.input.run_in_background//false),(.input.isolation//""),.result_meta.resolvedModel,.input.description]|@tsv'
```

### E02 — Subagent meta files (agentType, spawnDepth, model, worktree)
```bash
find "$P" -name '*.meta.json' -exec jq -rc '[(input_filename|split("/")|last|sub(".meta.json$";"")), .agentType, .spawnDepth, (.model//""), (.worktreeBranch//""), .description]|@tsv' {} +
```

### E03 — Per-subagent tokens, duration and tool calls
```bash
ASST | jq -sc "$JQDEFS"'map(select(.agent))|group_by(.agent)|map({agent:.[0].agent, sid:.[0].sid, type:.[0].agt, models:(map(.m)|unique), calls:length,
  dur_s:((((map(.ts)|max|ms)-(map(.ts)|min|ms))/1000)|floor), tools:(map(.ntools)|add),
  out:(map(.u.output_tokens)|add), cr:(map(.u.cache_read_input_tokens//0)|add), cw:(map(.u.cache_creation_input_tokens//0)|add)})|sort_by(-.cr)|.[]'
```

### E04 — Subagent totals by agent type
```bash
ASST | jq -sc 'map(select(.side))|group_by(.agt)|map({type:.[0].agt, agents:(map(.agent)|unique|length), calls:length, out:(map(.u.output_tokens)|add), cr:(map(.u.cache_read_input_tokens//0)|add)})|.[]'
```

### E05 — Agent coordination tools (SendMessage, ListAgents, TaskOutput, TaskStop, Monitor)
```bash
TOOLS | jq -r 'select(.name|IN("SendMessage","ListAgents","TaskOutput","TaskStop","Monitor"))|[.sid,.name,(.input.to//.input.task_id//"")]|@tsv' | sort | uniq -c
```

### E06 — Background task notifications (queue-operation content)
```bash
ALL | jq -r 'select(.type=="queue-operation")|[.operation,((.content//"")|if test("<task-notification>") then "task-notification" else "user-prompt" end)]|@tsv' | sort | uniq -c
```

---

## F. Skills & slash commands

### F01 — Skill tool invocations
```bash
TOOLS | jq -r 'select(.name=="Skill")|[.input.skill,(.result_meta.success|tostring)]|@tsv' | sort | uniq -c | sort -rn
```

### F02 — Slash commands typed
```bash
ALL | jq -r 'select(.type=="user")|.message.content|if type=="string" then . else ([.[]?|select(.type=="text")|.text]|join(" ")) end|scan("<command-name>([^<]*)")[0]' | sort | uniq -c | sort -rn
```

### F03 — Skill listing injected into context: count, names, size (chars)
```bash
ALL | jq -r 'select(.attachment.type=="skill_listing")|[.sessionId,.attachment.skillCount,(.attachment.content|length),.attachment.isInitial]|@tsv' | sort -u
ALL | jq -r 'select(.attachment.type=="skill_listing")|.attachment.names[]' | sort | uniq -c | sort -rn
```

### F04 — Plugins seen (plugin-namespaced skill names contain ":")
```bash
ALL | jq -r 'select(.attachment.type=="skill_listing")|.attachment.names[]|select(contains(":"))|split(":")[0]' | sort | uniq -c
```

---

## G. Hooks

### G01 — Hook executions by event and command: count, avg/max duration, exit codes
```bash
ALL | jq -r 'select(.attachment.type|IN("hook_success","hook_non_blocking_error","hook_cancelled")?)|.attachment|[.hookEvent,.type,(.exitCode//""),.durationMs,.command]|@tsv' \
 | awk -F'\t' '{k=$1"\t"$2"\t"$3"\t"$5; c[k]++; s[k]+=$4; if($4>m[k])m[k]=$4} END{for(k in c) printf "%d\tavg=%dms\tmax=%dms\t%s\n",c[k],s[k]/c[k],m[k],k}' | sort -rn
```

### G02 — Stop-hook summaries (hook count, durations, prevented continuation, errors)
```bash
ALL | jq -r 'select(.subtype=="stop_hook_summary")|[.timestamp,.hookCount,([.hookInfos[].durationMs]|add),.preventedContinuation,(.hookErrors|length),.level]|@tsv'
```

### G03 — Context injected by hooks (chars), per hook
```bash
ALL | jq -r 'select(.attachment.type=="hook_additional_context")|[.attachment.hookName,(.attachment.content|tostring|length)]|@tsv' | awk '{c[$1]++; s[$1]+=$2} END{for(k in c) print k"\t"c[k]" times\t"s[k]" chars total"}'
ALL | jq -r 'select(.attachment.type=="hook_system_message")|.attachment.content' | sort | uniq -c
```

### G04 — Hook failures
```bash
ALL | jq -r 'select(.attachment.type=="hook_non_blocking_error" or .attachment.type=="hook_cancelled")|[.timestamp,.attachment.hookName,(.attachment.exitCode//"cancelled"),.attachment.stderr[:120]]|@tsv'
```

### G05 — Hooks configured (from settings files)
```bash
for f in "$C/settings.json" "$C/settings.local.json" $(find "$HOME/Dev" -maxdepth 3 -path '*/.claude/settings*.json' 2>/dev/null); do [ -f "$f" ] && jq -r --arg f "$f" '.hooks//{}|to_entries[]|.key as $e|.value[]|.matcher as $m|.hooks[]|[$f,$e,($m//""),.command]|@tsv' "$f"; done
```

---

## H. Context composition (system prompt, tools, skills, MCP, memory)

### H01 — Full system prompt snapshot, when logged (size per section)
```bash
ALL | jq -r 'select(.attachment.type=="prompt_snapshot")|.sessionId as $s|.attachment.systemPrompt|to_entries[]|[$s,.key,(.value|length),(.value|split("\n")[0][:60])]|@tsv'
```

### H02 — Fixed-context baseline: total input tokens of the FIRST API call per session/agent (system prompt + tools + CLAUDE.md + first message)
```bash
ASST | jq -sc "$JQDEFS"'group_by([.sid,.agent])|map(sort_by(.ts)|first|{sid,agent,type:.agt,m,ts,baseline:(.u|ctx)})|sort_by(.ts)|.[]'
```

### H03 — Deferred tools: names announced, count, full schema records
```bash
ALL | jq -r 'select(.attachment.type=="deferred_tools_delta")|[.sessionId,(.attachment.addedNames|length),(.attachment.removedNames//[]|length),(.attachment.pendingMcpServers//[]|join(","))]|@tsv' | sort -u
ALL | jq -r 'select(.attachment.type=="deferred_tools_record")|.attachment.entries[]|[.name,(.input_schema|tojson|length),(.description|length)]|@tsv' | sort -u
```

### H04 — MCP server instructions injected (chars per server)
```bash
ALL | jq -r 'select(.attachment.type=="mcp_instructions_delta")|.attachment as $a|range(0;$a.addedNames|length) as $i|[$a.addedNames[$i],($a.addedBlocks[$i]|length)]|@tsv' | sort | uniq -c
```

### H05 — Agent listing injected (agent types + chars)
```bash
ALL | jq -r 'select(.attachment.type=="agent_listing_delta")|[.sessionId,(.attachment.addedTypes|join(",")),(.attachment.addedLines|join("")|length)]|@tsv' | sort -u
```

### H06 — Every injected attachment: count and total chars per type (the "hidden context" bill)
```bash
ALL | jq -r 'select(.type=="attachment")|[.attachment.type,(.attachment|tojson|length)]|@tsv' | awk -F'\t' '{c[$1]++; s[$1]+=$2} END{for(k in c) printf "%s\t%d\t%d chars\t~%d tok\n",k,c[k],s[k],s[k]/4}' | sort -t$'\t' -k3 -rn
```

### H07 — Environment / session context snapshots
```bash
ALL | jq -c 'select(.attachment.type|IN("environment","session_context","credential_org","date","date_change","remote_session_change")?)|{sid:.sessionId,t:.attachment.type,a:(.attachment|del(.type))}'
```

### H08 — Files re-injected after edits (edited_text_file) and LSP diagnostics injected
```bash
ALL | jq -r 'select(.attachment.type=="edited_text_file")|[.attachment.filename,(.attachment.snippet|length)]|@tsv' | awk '{c[$1]++; s[$1]+=$2} END{for(k in c) print c[k]"\t"s[k]" chars\t"k}' | sort -rn
ALL | jq -r 'select(.attachment.type=="diagnostics")|.attachment.files[]|.diagnostics[]|[.severity,.source,.message[:80]]|@tsv' | sort | uniq -c | sort -rn | head
```

### H09 — Memory files (auto-memory) size per project
```bash
find "$P" -path '*/memory/*' -type f -exec wc -c {} + | sort -n
```

---

## I. User behaviour

### I01 — Prompts typed per session, avg/max length
```bash
ALL | jq -r 'select(.type=="user" and (.promptSource|IN("typed","queued")))|[.sessionId,(.message.content|if type=="string" then length else ([.[]?|select(.type=="text")|.text]|join("")|length) end)]|@tsv' \
 | awk '{c[$1]++; s[$1]+=$2; if($2>m[$1])m[$1]=$2} END{for(k in c) print c[k]"\t"int(s[k]/c[k])" avg\t"m[k]" max\t"k}' | sort -rn
```

### I02 — Prompt source / origin mix (typed, sdk, queued, system, task-notification, coordinator)
```bash
ALL | jq -r 'select(.type=="user" and (.promptSource or .origin))|[(.promptSource//"-"),(.origin.kind//"-")]|@tsv' | sort | uniq -c
```

### I03 — Prompts by weekday and hour (local)
```bash
ALL | jq -r 'select(.type=="user" and (.promptSource|IN("typed","queued")))|.timestamp[:19]+"Z"|fromdate|localtime|strftime("%a %H")' | sort | uniq -c
```

### I04 — Image pastes
```bash
ALL | jq -r 'select(.type=="user")|[.message.content|arrays|.[]|select(.type=="image")|.source.media_type]|select(length>0)|.[]' | sort | uniq -c
```

### I05 — User interrupts
```bash
ALL | jq -r 'select(.type=="user")|.message.content|if type=="string" then . else ([.[]?|select(.type=="text")|.text]|join(" ")) end|select(test("\\[Request interrupted by user"))' | sort | uniq -c
```

### I06 — Queued prompts (typed while Claude was busy)
```bash
ALL | jq -r 'select(.attachment.type=="queued_command")|[.timestamp,.attachment.commandMode,(.attachment.origin.kind//"")]|@tsv' | wc -l
```

### I07 — Away summaries (recaps shown when you return)
```bash
ALL | jq -r 'select(.subtype=="away_summary")|[.timestamp,.sessionId,.content[:150]]|@tsv'
```

### I08 — Think time: gap between the end of Claude's turn and your next prompt
```bash
MAIN | jq -sc "$JQDEFS"'map(select(.timestamp and (.isSidechain|not)))|group_by(.sessionId)|map(sort_by(.timestamp)|. as $a
  |[range(1;length) as $i|select($a[$i].type=="user" and ($a[$i].promptSource|IN("typed","queued")))|(($a[$i].timestamp|ms)-($a[$i-1].timestamp|ms))/1000|floor])|add|.[]' 2>/dev/null \
 | sort -n | awk '{v[NR]=$1; s+=$1} END{print "n="NR" avg="int(s/NR)"s p50="v[int(NR/2)+1]"s p90="v[int(NR*0.9)]"s"}'
```

---

## J. Modes & permissions

### J01 — Permission mode per prompt and mode-change records
```bash
ALL | jq -r 'select(.type=="user" and .permissionMode)|.permissionMode' | sort | uniq -c
ALL | jq -r 'select(.type=="permission-mode")|[.sessionId,.permissionMode]|@tsv' | uniq | awk '{print $2}' | sort | uniq -c
```

### J02 — Plan mode: entries, exits, plans written
```bash
ALL | jq -r 'select(.attachment.type|IN("plan_mode","plan_mode_exit","plan_mode_reentry","auto_mode","auto_mode_exit")?)|.attachment.type' | sort | uniq -c
ALL | jq -r 'select(.attachment.type=="plan_mode")|.attachment.planFilePath' | sort -u
```

### J03 — Auto-mode classifier: flags and errors
```bash
ALL | jq -r 'select(.attachment.type=="auto_mode")|.attachment|[.bashFirst,.steerOnly,.bypass]|@tsv' | sort | uniq -c
find "$P" -name 'auto-mode-classifier-error.txt' -exec wc -l {} +
```

### J04 — Allowed-tools grants per command (command_permissions)
```bash
ALL | jq -r 'select(.attachment.type=="command_permissions")|.attachment.allowedTools|join(",")' | sort | uniq -c
```

---

## K. Turns & timing

### K01 — Turn duration and messages per turn (system/turn_duration)
```bash
ALL | jq -r 'select(.subtype=="turn_duration")|[.sessionId,.durationMs,.messageCount,(.pendingBackgroundAgentCount//0)]|@tsv' \
 | awk -F'\t' '{n++; s+=$2; m+=$3; if($2>x)x=$2} END{print "turns="n" avg="int(s/n/1000)"s max="int(x/1000)"s avg_msgs="int(m/n)}'
```

### K02 — API wait time per call: previous line timestamp → assistant response timestamp (approximation)
```bash
ALL | jq -sc "$JQDEFS"'map(select(.timestamp and .uuid))|group_by(.sessionId+(.agentId//""))|map(sort_by(.timestamp)|. as $a
  |[range(1;length) as $i|select($a[$i].type=="assistant" and $a[$i-1].type!="assistant")|{m:$a[$i].message.model, s:((($a[$i].timestamp|ms)-($a[$i-1].timestamp|ms))/1000)}])|add|group_by(.m)|map({m:.[0].m,n:length,avg_s:((map(.s)|add/length)*10|round/10)})|.[]'
```

### K03 — Active time per day (sum of gaps < 5 min between events)
```bash
ALL | jq -r 'select(.timestamp)|.timestamp' | sort | jq -Rr "$JQDEFS"'[.[:10],(.|ms)]|@tsv' | awk -F'\t' 'NR>1 && $2-p<300000{a[$1]+=$2-p} {p=$2} END{for(d in a) printf "%s\t%.1f h\n",d,a[d]/3600000}' | sort
```

---

## L. Errors

### L01 — API errors (synthetic messages)
```bash
ALL | jq -r 'select(.isApiErrorMessage)|[.timestamp[:10],.error,(.message.content[0].text[:80])]|@tsv' | sort | uniq -c
```

### L02 — Tool errors by tool (see D05) and local-command / informational warnings
```bash
ALL | jq -r 'select(.subtype=="informational" or (.subtype=="local_command" and (.level//"")!="info"))|[.level,.content[:100]]|@tsv' | sort | uniq -c
```

---

## M. Files, git, PRs

### M01 — Files tracked by file-history (checkpoints / rewind)
```bash
ALL | jq -r 'select(.type=="file-history-delta")|.trackingPath' | sort | uniq -c | sort -rn | head -20
ALL | jq -r 'select(.type=="file-history-snapshot")|.snapshot.trackedFileBackups|keys|length' | awk '{s+=$1; if($1>m)m=$1} END{print NR" snapshots, max files="m}'
```

### M02 — Branches and working dirs per session
```bash
ALL | jq -r 'select(.gitBranch)|[.sessionId,.gitBranch,.cwd]|@tsv' | sort -u
```

### M03 — PRs linked to sessions
```bash
ALL | jq -r 'select(.type=="pr-link")|[.timestamp,.sessionId,.prUrl]|@tsv'
```

---

## N. Identity & infra

### N01 — Remote-control bridge sessions, org/account
```bash
ALL | jq -r 'select(.type=="bridge-session")|[.sessionId,.bridgeSessionId,.ownerOrganizationUuid]|@tsv' | sort -u
```

### N02 — Named sessions (agent-name) and AI titles
```bash
ALL | jq -r 'select(.type=="agent-name" or .type=="ai-title")|[.sessionId,.type,(.agentName//.aiTitle)]|@tsv' | sort -u
```

### N03 — Last prompt per session (resume pointer)
```bash
ALL | jq -r 'select(.type=="last-prompt")|[.sessionId,.lastPrompt[:100]]|@tsv' | sort -u -k1,1
```

---

## O. claudepit sidecar files (your app, in the projects dir)

### O01 — Session bullet summaries
```bash
find "$P" -path '*/summary/*.json' -exec jq -r '[(input_filename|split("/")|last), (.bullets|length), (.updatedAt|todate)]|@tsv' {} +
```

### O02 — Tasks (status, phases) and memory write log
```bash
find "$P" -path '*/tasks/*/task.json' -exec jq -rc '[(input_filename|split("/tasks/")[1]|split("/")[0]), .status, .phase, .priority, (.name//.description[:60])]|@tsv' {} +
find "$P" -path '*/memory/log.json' -exec jq -r '.[]|[(.ts|todate),.type,.sessionId,.title]|@tsv' {} +
```

---

## P. Related sources outside `projects/` (not session files, but useful for analysis)

### P01 — stats-cache.json (daily activity + tokens by model computed by Claude Code)
```bash
jq -c '.dailyActivity[]' "$C/stats-cache.json"; jq -c '.dailyModelTokens[]' "$C/stats-cache.json"
```

### P02 — history.jsonl (every prompt typed, across projects, including pasted content)
```bash
jq -r '[(.timestamp/1000|todate),.project,(.display[:80])]|@tsv' "$C/history.jsonl" | tail -20
```

### P03 — Installed/enabled plugins, marketplaces, user skills
```bash
jq -r '.plugins|to_entries[]|[.key,.value[0].version,.value[0].installedAt]|@tsv' "$C/plugins/installed_plugins.json"
jq -r '.enabledPlugins//{}|to_entries[]|[.key,.value]|@tsv' "$C/settings.json"
find "$C/skills" -name SKILL.md -exec wc -c {} +
```

### P04 — Plans written, file-history backups, debug logs
```bash
find "$C/plans" -name '*.md' -exec wc -c {} + | sort -n | tail; du -sh "$C/file-history" "$C/debug" "$C/paste-cache" 2>/dev/null
```

---

## Not available from local files
- Exact system prompt / CLAUDE.md / tool schema token counts per call (only proxies: H01 when present, H02 baseline, H06 attachment chars).
- Dollar cost (compute it with B16).
- Rate-limit / plan-quota state (not written to the transcripts).
- Per-request server latency (K02 is a client-side approximation).
