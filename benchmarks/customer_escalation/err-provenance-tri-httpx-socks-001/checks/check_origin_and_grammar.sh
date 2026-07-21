#!/usr/bin/env bash
# check_origin_and_grammar.sh -- agent located the 'illegal header line' origin
# in h11's reader, the validate() helper, the grammar regex, and the
# whitespace-before-colon violation. Each group lists accepted phrasing
# variants; score = matched groups / total groups.
#
# Echo re-anchor (EnterpriseBench-jn73.2.7.3.1.4): every graded token must be
# absent from instruction.md. 'illegal header line' is quoted in the prompt's
# error output, so the origin_message group now requires the raise-site
# format string or the raising function; free-phrasing variants not present
# in ground truth / expected solution are pruned.

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
    'origin_file': ['_readers.py', 'h11._readers'],
    'origin_message': ['illegal header line: {!r}', '_decode_header_lines'],
    'grammar_regex': ['header_field_re', '_abnf'],
    'validate_helper': ['validate(', '_util.py', 'h11._util'],
    'whitespace_violation': ['before the colon', 'space before', 'whitespace before'],
}

matched = [name for name, alts in groups.items()
           if any(alt in answer_text for alt in alts)]
score = len(matched) / len(groups)
detail = f'Matched {len(matched)}/{len(groups)} origin/grammar groups: {sorted(matched)}'
print(json.dumps({'score': round(score, 2), 'passed': score >= 0.6, 'detail': detail}))
"
