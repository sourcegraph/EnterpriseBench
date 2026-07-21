#!/usr/bin/env bash
# check_severity.sh — verify agent correctly assessed severity
# Reads agent output from $WORKSPACE/agent_output/answer.json
# Requires both the correct severity level AND citation of a specific mechanism
set -euo pipefail

export ANSWER_FILE="${WORKSPACE:-/workspace}/agent_output/answer.json"
export GT_FILE="${TASK_DIR:-/task}/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}'
    exit 1
fi

python3 -c "
import json, sys, os, re

gt = json.load(open(os.environ['GT_FILE']))
answer = json.load(open(os.environ['ANSWER_FILE']))

expected_severity = gt.get('expected_severity', '').lower()

if not expected_severity:
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'No expected severity in ground truth'}))
    sys.exit(0)

# Implementation identifiers from the prometheus v2.48.0 / grafana v10.2.0
# sample-limit path that must accompany the severity rating. A hallucinating
# agent that just says 'high severity' without citing the actual mechanism
# should not pass. All are repo-derived and appear ZERO times in instruction.md
# (verified with grep -cioF); the prompt's 'max samples' / 'query timeout' are
# spaced, so they cannot substring-match these identifiers under this
# normalization. (This list previously carried Envoy connection-pool parameters
# copy-pasted from another task, which no honest grafana/prometheus answer
# could ever cite.)
MECHANISM_KEYWORDS = [
    'ErrTooManySamples',
    'MaxSamples',
    'maxSamplesPerQuery',
    'query.max-samples',
    'returnAPIError',
    'queryTimeout',
]

# Extract agent severity (flexible key names)
sev = answer.get('severity', answer.get('impact', answer.get('priority', '')))
if isinstance(sev, dict):
    agent_severity = sev.get('level', sev.get('severity', sev.get('rating', ''))).lower()
    agent_rationale = sev.get('rationale', sev.get('reason', '')).lower()
elif isinstance(sev, str):
    agent_severity = sev.lower()
    agent_rationale = ''
else:
    agent_severity = str(sev).lower()
    agent_rationale = ''

if not agent_severity.strip():
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'Agent provided no severity assessment'}))
    sys.exit(0)

# Severity level mapping for distance scoring
LEVELS = {'low': 0, 'medium': 1, 'high': 2, 'critical': 3}
expected_idx = LEVELS.get(expected_severity, -1)

agent_idx = -1
for level, idx in LEVELS.items():
    if level in agent_severity:
        agent_idx = idx
        break

if expected_idx < 0 or agent_idx < 0:
    severity_score = 1.0 if expected_severity in agent_severity else 0.0
else:
    distance = abs(expected_idx - agent_idx)
    severity_score = max(0.0, 1.0 - distance * 0.4)

# Check that the agent cites at least one specific mechanism keyword
# Search the full answer text, not just the severity field
full_text = json.dumps(answer).lower()
combined_text = agent_rationale + ' ' + full_text

def mechanism_present(kw, text):
    return kw.lower().replace('-', '_') in text.replace('-', '_')

matched_mechanisms = [kw for kw in MECHANISM_KEYWORDS if mechanism_present(kw, combined_text)]
mechanism_cited = len(matched_mechanisms) >= 1

# Final score: 60% for correct severity level + 40% for mechanism citation
score = round(0.6 * severity_score + 0.4 * (1.0 if mechanism_cited else 0.0), 2)

# Must have correct severity AND cite at least one mechanism to pass
passed = severity_score >= 0.6 and mechanism_cited

detail = (
    f'Expected: {expected_severity}, Agent: {agent_severity} '
    f'(severity_score={severity_score:.2f}); '
    f'mechanism_cited={mechanism_cited} ({matched_mechanisms})'
)
print(json.dumps({'score': score, 'passed': passed, 'detail': detail}))
"
