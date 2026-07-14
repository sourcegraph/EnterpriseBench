# Scoring-integrity corpus

The scorer trust boundary in one place: a benchmark score is valid **only** if
the pristine verifier ran on real agent output. Every infrastructure, verifier,
or judge failure must surface as a `verifier_infra_error` and route the run to
re-run — never a legitimate `0.0` (under-credit) or an un-capped grep score
(over-credit).

Enforced by `lib/eb_verify/scorer_guard.py`, applied at every scoring entry
point (`_run_scoring`, `_apply_llm_judge`, `code_patch.validate`). This corpus
is one closed test per known corruption vector, wired into CI as a named merge
blocker (`.github/workflows/ci.yml` → "Scoring-integrity corpus").

The `file_extraction` vectors are the under-credit direction, so they assert the
correct `1.0` rather than an infra sentinel: `guard_verifier_output` cannot catch
a scoring bug inside a verifier that ran cleanly and returned valid JSON.

They also **execute the shipped check scripts** — found by scanning
`benchmarks/*/*/checks/*.sh` for the ones that exec the scorer, then run through
the runner's own `checkpoint_env` — instead of restating what those scripts pass.
An earlier draft copied their `--keys` list into a constant here, so the gate
exercised the scorer's argument *parsing* (never broken) rather than the key list
the scripts actually ship (the bug), and stayed green when a key was dropped from
them. The key list therefore appears nowhere in this directory, and the scan picks
up whatever check scripts adopt the scorer next with no edit here.

## Vectors × tests

| Vector | Direction | Origin | Test |
|---|---|---|---|
| broken `test.sh` → false 0.0 | under-credit | apfp #2, s58f/c7wb | `test_empty_output_is_infra_not_zero` |
| malformed verifier output | under-credit | apfp #2 | `test_non_json_output_is_infra`, `test_non_object_json_is_infra` |
| top-level `error` key read by no caller | under-credit | apfp #2 | `test_top_level_error_key_is_infra` |
| docker-cp harness-import silent 0.0 | under-credit | hktt/pt0n | `test_docker_cp_module_not_found_is_infra` |
| explicit verifier infra sentinel | under-credit | apfp | `test_explicit_sentinel_in_checkpoint_detail_is_infra` |
| `code_patch` git error → false "no changes" | under-credit | apfp #4 | `test_git_probe_failure_surfaces_infra_sentinel`, `test_diff_probe_error_is_raised_on_bad_repo` |
| malformed `expected_solution.json` → un-capped grep | over-credit | apfp #3 | `test_malformed_expected_solution_flags_infra` |
| judge-init failure → un-capped grep | over-credit | apfp #3 | `test_judge_init_failure_flags_infra` |
| per-checkpoint judge exception → un-capped grep | over-credit | apfp #3 | `test_per_checkpoint_judge_exception_flags_infra` |
| milestone verifier exit 0 + no verdict → free 1.0 | over-credit | chc2z | `test_exit0_without_json_is_infra_not_a_free_1_0` |
| milestone verifier exit 1 + no verdict → false 0.0 | under-credit | chc2z | `test_exit1_without_json_is_infra_not_a_false_0_0` |
| missing / timed-out / unexecutable milestone verifier → 0.0 | under-credit | chc2z | `test_missing_verifier_is_infra_not_a_0_0`, `test_timeout_is_infra_not_a_0_0`, `test_unexecutable_verifier_is_infra_not_a_crash` |
| infra milestone averaged into `chain_result.json` total | both | chc2z | `test_chain_result_json_total_score_is_null_and_exit_is_nonzero` |
| failed session → final checkpoints score an unworked workspace → free 1.0 | over-credit | 6c9wp | `test_failed_first_session_scores_nothing_and_never_runs_the_checkpoints` |
| chain aborted mid-way → total computed from the earlier sessions' milestones | over-credit | 6c9wp | `test_mid_chain_failure_does_not_score_from_the_earlier_milestones` |
| `file_extraction` shipped `--keys` drops a key the answer names its files under | under-credit | ssikq | `test_omitted_mandated_keys_zero_a_spec_compliant_answer` |
| the scan above finds no check script at all (vectors pass vacuously) | — | ssikq | `test_the_corpus_actually_found_the_scorers_call_sites` |
| this table cites a test the corpus does not contain | false coverage | ssikq | `test_every_test_this_readme_cites_exists` |
| a row of this table names no test at all | false coverage | ssikq | `test_every_readme_row_names_a_test` |

The `file_extraction` row is one case per entry in the shipped `--keys` — including
the `code_paths` and `citations` that run_task.py's appendix and
`require_grounded_citations` *mandate* agents answer under — run against every check
script that execs the scorer. Drop any key from any of them and it goes red. It covers
the two scripts that exec `eb_verify.plugins.file_extraction` today and any that adopt
it later; it says nothing about the ~39 sibling checkpoints that still match files with
an inline `af.endswith(gt)`, which carry all the same defects and are bead `rmz1x`.

What the scorer does with the files once it has them — the citation-suffix dialects, the
ambiguity and refinement rules, nested ground truth — is unit-tested in
`tests/test_file_extraction.py`. It belongs there and not here: it costs this gate a
subprocess per case, and it depends on no shipped artifact, which is the whole
membership rule for this directory.

A failed session is **not** a verifier failure, so it routes through a sibling
channel, `session_failure`, rather than being laundered through
`verifier_infra_error`: same re-run destination, honest cause. Both channels
independently force `total_score = None` and a nonzero exit, and both are
reported when both fire.

### Negative controls (guard must not over-flag)

| Case | Test |
|---|---|
| real all-fail 0.0 (verifier ran, agent failed) | `test_genuine_all_fail_zero_passes_through` |
| healthy chain, every session completed → scores normally | `test_every_session_completing_scores_the_final_checkpoints` |
| valid partial score | `test_valid_scores_pass_through` |
| `ImportError` in an error-provenance **task subject** | `test_import_error_in_task_subject_is_not_flagged` |
| genuinely clean repo (no changes) | `test_clean_repo_no_changes_is_not_infra` |
| repo with real changes | `test_repo_with_changes_is_valid` |
| healthy judge caps grep normally | `test_healthy_judge_caps_and_does_not_flag` |

## Adding a vector

When a new score-corruption class is found, add one failing fixture here that
asserts an **infra error, not a number**, before landing the fix. Keep at least
one negative control so the new guard clause cannot over-flag legitimate scores.

Related coverage already on `main`: `tests/test_cross_repo_runner.sh` and
`tests/test_instruction_mcp_preamble.py` (wbsq JSON/awk-injection escaping).
`7jpm` (pristine-verifier re-copy) and `cdzi` (Tier-2 runner consolidation)
carry their own regression tests and fold in when those branches land.
