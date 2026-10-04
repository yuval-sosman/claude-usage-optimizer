#!/usr/bin/env bash
# Shared helpers for every extraction in CATALOG.md. Source this first:  . ./lib.sh
# Requires: jq, find, xargs, awk (all non-LLM).

C="${CLAUDE_HOME:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}"      # the folder Claude Code keeps its data in
P="${CLAUDE_PROJECTS:-$C/projects}"

# Raw line streams (project dirs start with "-", so always go through find, never bare globs/ls)
ALL()  { find "$P" -name '*.jsonl' -print0 | xargs -0 cat; }
MAIN() { find "$P" -name '*.jsonl' -not -path '*/subagents/*' -print0 | xargs -0 cat; }
SUB()  { find "$P" -path '*/subagents/*' -name '*.jsonl' -print0 | xargs -0 cat; }

# Run jq per line with the source file available as `input_filename`
EACH() { find "$P" -name '*.jsonl' -print0 | xargs -0 jq -c "$@"; }
# Run jq -s once per main-session file (whole session in one array; input_filename works)
PERSESSION() { find "$P" -name '*.jsonl' -not -path '*/subagents/*' -exec jq -sc "$@" {} \; ; }
PERFILE()    { find "$P" -name '*.jsonl'                            -exec jq -sc "$@" {} \; ; }

# jq definitions reused by many metrics
JQDEFS='
def ms: (.[:19]+"Z"|fromdate)*1000 + (.[20:23]|tonumber);
def ctx: (.input_tokens + (.cache_read_input_tokens//0) + (.cache_creation_input_tokens//0));
def cw5: (.cache_creation.ephemeral_5m_input_tokens//0);
def cw1: (.cache_creation.ephemeral_1h_input_tokens//0);
def proj: (input_filename | split("/projects/")[1] | split("/")[0]);
def sid_of_file: (input_filename | split("/") | last | sub("\\.jsonl$";""));
'

# ONE LINE PER REAL API CALL.
# Claude Code writes every content block (thinking/text/tool_use) as its own line with the same
# message.id + requestId and repeats usage; intermediate lines carry partial output_tokens.
# Dedup key = message.id|requestId, keep the line with max output_tokens. Also dedups the same
# subagent transcript copied into two sessions.
ASST() {
  EACH "$JQDEFS"'select(.type=="assistant" and .message.usage) | {
      k:(.message.id+"|"+(.requestId//"")), file:(input_filename), proj:proj,
      sid:.sessionId, agent:(.agentId//null), side:(.isSidechain//false),
      m:.message.model, ts:.timestamp, effort:.effort, skill:.attributionSkill, agt:.attributionAgent,
      stop:.message.stop_reason, u:.message.usage, diag:.message.diagnostics,
      ntools:([.message.content[]?|select(.type=="tool_use")]|length) }' \
  | jq -sc 'group_by(.k) | map(max_by(.u.output_tokens) + {ntools:(map(.ntools)|add)}) | .[]'
}

# tool_use <-> tool_result join: one line per tool call
# {id,name,sid,agent,ts_use,ts_res,ms,is_error,in_bytes,out_bytes,input,result_meta}
TOOLS() {
  ALL | jq -c "$JQDEFS"'
    if .type=="assistant" then
      (.timestamp as $t | .sessionId as $s | (.agentId//null) as $a | .message.content[]? | select(.type=="tool_use")
       | {k:"U", id, name, sid:$s, agent:$a, ts:$t, input, in_bytes:(.input|tojson|length)})
    elif .type=="user" then
      (.timestamp as $t | .toolUseResult as $r | .message.content | arrays | .[] | select(.type=="tool_result")
       | {k:"R", id:.tool_use_id, ts:$t, is_error:(.is_error//false), out_bytes:(.content|tojson|length),
          result_meta:($r | if type=="object" then with_entries(select(.value|type!="string" or length<300)) else {text:(.|tostring|.[:300])} end)})
    else empty end' \
  | jq -sc "$JQDEFS"'group_by(.id) | map( (map(select(.k=="U"))|first) as $u | (map(select(.k=="R"))|first) as $r
      | select($u) | $u + {ts_res:$r.ts, is_error:(if $r then $r.is_error else null end), out_bytes:($r.out_bytes//null), result_meta:($r.result_meta//null),
                          ms:(if $r then ($r.ts|ms)-($u.ts|ms) else null end)} | del(.k)) | .[]'
}
