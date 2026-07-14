# Security incident: a crafted POST body poisons every object in a running Node service

## Reported issue

A security researcher filed a report against a production Express service. The
service accepts standard HTML form submissions:

    app.use(express.urlencoded({ extended: true }));

    app.post("/profile", (req, res) => {
      const prefs = {};
      Object.assign(prefs, req.body);        // merge submitted fields
      res.json({ ok: true, admin: prefs.isAdmin === true });
    });

The researcher demonstrated that a single request with this body:

    POST /profile HTTP/1.1
    Content-Type: application/x-www-form-urlencoded

    foo[__proto__][isAdmin]=true

changes the behavior of *unrelated* requests afterward: subsequent requests that
never send `isAdmin` start reporting `admin: true`, and freshly created empty
objects elsewhere in the process suddenly carry an `isAdmin` property. Restarting
the process clears it until the payload is replayed. The default `{}` objects the
app relies on are being mutated globally, so the finding is a prototype-pollution
vulnerability, not a bug in the route handler.

The team confirmed:

- The route handler only ever calls `Object.assign(prefs, req.body)`; it does not
  walk nested keys itself.
- The same payload sent as a JSON body (`Content-Type: application/json`) does
  **not** pollute anything — only the form-urlencoded path does.
- Sending `foo[bar][isAdmin]=true` instead is harmless; the effect requires the
  specific `__proto__` segment.

The application code contains none of the parsing logic. The dangerous assignment
lives inside the dependency stack that turns the raw request body into
`req.body`, several levels below anything the team wrote.

## Your task

1. Identify the exact repository, file, and routine where attacker-controlled key
   text is assigned onto an object in a way that reaches the prototype. The stack
   the team can see stops at their own middleware registration; the sink is in a
   transitive dependency that neither the framework nor the body-parsing
   middleware appears (by name) to call directly. Name the origin precisely and
   explain why the JSON body path is unaffected while the form path is.

2. Trace the full path from the HTTP request to that sink. The body-parsing
   middleware selects, for its "extended" mode, a nested query-string expander
   that it pulls in through an indirection rather than a plain top-level import;
   the framework separately exposes the *same* expander through its own built-in
   request-query layer. Name the file and the routine at each hop across all three
   repositories, and explain why a plain import-following search from the app does
   not reach the sink.

3. Fix the defect at its true origin so that the special prototype key is not
   assigned during recursive key expansion, exactly as a normal missing key would
   be ignored — matching how the library handles this in its fixed releases. Make
   the change in the workspace as an uncommitted diff. Do **not** neutralize it at
   the route handler or by swapping the middleware option; the sink itself must
   stop assigning the prototype key.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the pollution path across all three
  repositories.
- An uncommitted code change at the true origin that stops the recursive key
  expander from writing the prototype key onto the object it builds.
