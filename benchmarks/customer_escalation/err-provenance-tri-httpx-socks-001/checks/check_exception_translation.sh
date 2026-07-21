#!/usr/bin/env bash
# check_exception_translation.sh -- agent named the in-place
# LocalProtocolError -> RemoteProtocolError re-raise and both cross-repo
# exception mapping layers. Score = matched groups / total groups.
#
# Echo re-anchor (EnterpriseBench-jn73.2.7.3.1.4): every graded token must be
# absent from instruction.md. The former 'local_protocol_error' group graded
# a class name the prompt hands over verbatim, so a prompt echo scored 0.2;
# the exception-class naming is already covered by the reraise groups.

set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

python3 -c "
import json, os

answer_text = json.dumps(json.load(open(os.environ['ANSWER_FILE']))).lower()

groups = {
    'reraise_mechanism': ['_reraise_as_remote_protocol_error'],
    'reraise_site': ['next_event', '_connection.py'],
    'httpcore_map': ['map_exceptions'],
    'httpx_map': ['httpcore_exc_map', 'map_httpcore_exceptions'],
}

matched = [name for name, alts in groups.items()
           if any(alt in answer_text for alt in alts)]
score = len(matched) / len(groups)
detail = f'Matched {len(matched)}/{len(groups)} translation groups: {sorted(matched)}'
print(json.dumps({'score': round(score, 2), 'passed': score >= 0.6, 'detail': detail}))
"
