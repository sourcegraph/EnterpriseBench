---
previewImage: 'https://storage.googleapis.com/blog-static-assets-prod/20260616_a-retrieval-tool-vs-a-frontier-model-on-codescalebench_preview.png'
tagline: "When you have a fixed budget, do you spend it on a stronger model or on better tooling? I ran a three-arm study on CodeScaleBench to find out, and the answer surprised me: a cheaper model with Sourcegraph retrieval beat a frontier model without it, and the win was almost entirely the tooling."
authors:
  - Stephanie Jarmak
---

# A retrieval tool vs a frontier model on CodeScaleBench

In my [last post](https://sourcegraph.com/blog/codescalebench-testing-coding-agents-on-large-codebases-and-multi-repo-software-engineering-tasks) I introduced CodeScaleBench and showed that giving an agent the Sourcegraph MCP improves how much relevant context it retrieves. That left an obvious follow-up question, the one anyone choosing an agent configuration actually has to answer: **when you have a fixed budget, do you spend it on a stronger model, or on better tooling?**

A stronger model is the obvious lever. This study points the other way, and the only reason I trust that claim is a third experimental arm that most A/B comparisons skip.

## The trap in a two-way comparison

The deployment question usually gets framed as a head-to-head: a cheap model with retrieval against an expensive model without it. I ran that comparison on nine discovery-heavy CodeScaleBench org tasks (cross-repo, cross-org, vulnerability tracing) and the cheap-plus-retrieval side won.

But that number is a trap. The winning configuration differs from the losing one in **two ways at once**, model and tooling, so the gap could be the retrieval, the model, or any mix. A reader who already believes frontier models are overrated reads it as proof; a reader who believes the opposite dismisses it as a tooling artifact. Both are guessing, because the experiment as built can't tell them apart.

## Three arms instead of two

The fix is to add the arm the two-way comparison leaves out. Three configurations, same nine tasks, three trials each:

- **Arm A:** Sonnet 4.6 with the Sourcegraph MCP and no local source. Retrieval only.
- **Arm B:** a frontier model (Fable 5) with a normal local checkout, no MCP.
- **Arm C:** Sonnet 4.6 with the same local checkout as B, no MCP.

Arm C does the work. Compare A to C and the model is held fixed at Sonnet, so the only thing changing is the tooling: that difference is the MCP effect. Compare C to B and the tooling is held fixed at a plain local checkout, so the only thing changing is the model: that difference is the model effect.

| Arm | Model | Tooling | Mean reward | $/quality point |
|---|---|---|---|---|
| A | Sonnet 4.6 | Sourcegraph MCP | **0.698** | **$1.02** |
| C | Sonnet 4.6 | local checkout | 0.606 | $0.82 |
| B | Fable 5 | local checkout | 0.568 | $1.83 |

<!-- INTERACTIVE: charts from the explorer's summary page. Confirm the blog renderer
     allows an iframe to the GitHub Pages domain; otherwise fall back to a static PNG. -->
<iframe src="{{EXPLORER_URL}}/comparison.html" title="Three-arm reward standings and decomposition" width="100%" height="900" style="border:1px solid #e3e0ea;border-radius:12px"></iframe>

## The deployment win was almost all tooling

Here is the decomposition that the third arm buys you:

- **MCP effect (A − C, same model): +0.092 mean reward, 5 wins / 2 ties / 2 losses.** On these discovery-heavy tasks, Sourcegraph lifts Sonnet's quality by about 9 points of normalized reward at essentially equal dollar cost and less wall-clock than the plain baseline.
- **Model effect (C − B, same tooling): +0.038, 5 / 1 / 3. That is parity.** Given a local checkout, the frontier model does not beat Sonnet here, and it costs about 1.5x more per task.
- **Deployment comparison (A − B): +0.129, 6 / 2 / 1.** More than two-thirds of that gap is the tooling, not the model.

So the headline isn't "a cheap model beats a frontier model." It's narrower and, I think, more useful: **on this kind of work, retrieval beats no-retrieval, and the choice of model barely matters.** A frontier model without good context tooling lands right next to a cheaper one, and both lose to the cheaper model once you give it the retrieval.

## Where the lift shows up

The gap isn't spread evenly. It concentrates exactly where you'd expect retrieval to matter: finding where code is actually used across repositories. Vulnerability usage-site tracing and cross-org dependency following drive most of it, while tasks both arms already solve (a config trace, a straightforward migration) tie near the ceiling, because retrieval can't lift a task that's already solved. There's even one task where Fable's raw capability on a full local checkout beat the retrieval arm, which is the kind of bidirectional result you want to see: it means nothing is rigged.

## I checked, the parity is real

A cheaper model matching a frontier model sounds suspicious, so I went into the transcripts. Across every Fable baseline run: no timeouts, no tool errors, no turn-cap clips, no refusals on the scored tasks, and the instruction sent to each model was identical. Fable simply runs more efficiently. It searches less and commits to an answer sooner, ending its own turn, while baseline-Sonnet burns two to five times the tool calls to reach a near-identical score. The parity is a real result, not a handicapped frontier model.

That's also why I publish the runs. As I said last time, agents are sneaky, manipulative genies, and benchmark results should ship with full transcripts for auditing. So don't take any of these numbers on faith.

## Explore it yourself

Every run is open. The results explorer lets you pick a task and put the three arms **side by side**, Sonnet, Sonnet + Sourcegraph MCP, and Fable, and read the full prompt, conversation history, and every tool call for each one.

<iframe src="{{EXPLORER_URL}}/compare.html" title="3-way per-task trace explorer" width="100%" height="700" style="border:1px solid #e3e0ea;border-radius:12px"></iframe>

[Open the full results explorer →]({{EXPLORER_URL}}/compare.html)

If you're building or evaluating agent tooling for real codebases, I'd love your read on the traces. You can reach me at steph@sourcegraph.com.
