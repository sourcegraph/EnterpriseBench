# Incident: one download request pins a worker at 100% CPU and never returns

## Reported issue

An on-call engineer is paging on a Ruby web service built on a lightweight Sinatra
app. One worker after another stops serving traffic; each stuck worker sits at
100% CPU indefinitely, holding its request thread forever. There is **no
exception and no stack trace** in the logs — the worker simply never finishes the
request. Restarting the worker frees it until the next bad request arrives.

The affected route serves file downloads:

    get "/download/:name" do
      send_file File.join(settings.public_folder, params[:name])
    end

Correlating the access log with the hangs, the team isolated the trigger: every
stuck request carried an unusual `Range` request header. A normal ranged request
like:

    Range: bytes=0-1023

is served fine. But a crafted header of the shape:

    Range: bytes=0-,0-,0-,0-,0-,0-, ... (many repetitions) ... ,0-0

makes the worker spin at 100% CPU and never respond. The time-to-hang grows
explosively with the number of repeated segments, which is the signature of
catastrophic regular-expression backtracking rather than a large-but-finite
amount of work. The payload is well under any body-size limit; it is entirely in
the request header.

The Sinatra app itself never looks at the `Range` header — it only calls
`send_file`. The header is interpreted deep in the serving stack, in a dependency
the app does not reference directly, and the pathological pattern lives in a
low-level utility there.

## Your task

1. Identify the exact repository, file, and routine where the `Range` header value
   is parsed with a regular expression that backtracks catastrophically on the
   crafted input. Sinatra is not where the parsing happens; the file-serving is
   delegated to a dependency, and the vulnerable routine is a utility inside it.
   Name that origin precisely and explain why the input above triggers exponential
   backtracking.

2. Trace the full path from `send_file` to that routine: the Sinatra helper that
   serves the file, the dependency's file-server object it delegates to that reads
   the incoming `Range` header, and the utility routine that file server calls to
   turn the header value into byte ranges. Name the file and the routine at each
   hop across both repositories, and explain why the pathological code lives in a
   different repository than the one the app calls.

3. Fix the defect at its true origin so that parsing the range header runs in
   linear time on adversarial input — replacing the backtracking pattern with a
   non-backtracking parse of each range segment, matching how the library fixed
   this in its patched releases. Make the change in the workspace as an
   uncommitted diff. Do **not** paper over it by capping the header length in the
   app or rejecting `Range` headers at the route; the parsing routine itself must
   stop backtracking.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the request path across both repositories.
- An uncommitted code change at the true origin that removes the catastrophic
  backtracking from the range-header parse.
