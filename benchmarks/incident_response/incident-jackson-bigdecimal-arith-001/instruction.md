# Incident: report-ingestion worker crashes with an uncaught ArithmeticException on one vendor's payload

## Reported issue

A platform team runs a JSON report-ingestion service. Each worker deserializes
third-party vendor payloads into domain objects with a shared, pre-configured
Jackson `ObjectMapper`:

    Report report = mapper.readValue(payloadBytes, Report.class);

For the overwhelming majority of vendors this works. But payloads from one vendor
intermittently take down the worker with an uncaught exception whose top frames
look nothing like a data-validation problem:

    java.lang.ArithmeticException
        at java.base/java.math.BigDecimal.<init>(BigDecimal.java:...)
        at java.base/java.math.BigDecimal.<init>(BigDecimal.java:...)
        ...
        at com.fasterxml.jackson.databind.ObjectMapper.readValue(ObjectMapper.java:...)

The exception message is usually `null`. The service was expecting malformed
numeric input to arrive as an ordinary "not a valid number" parse failure that it
already catches and turns into an HTTP 400; instead it gets a raw
`ArithmeticException` that no layer expects, so the worker thread dies and the
batch is abandoned.

The team has narrowed the trigger to a single field in the affected payloads: a
monetary/measurement value serialized as a decimal in scientific notation with an
extreme exponent (something on the order of `7.65E+2147483647`). Any payload
carrying such a value crashes; the same payload with a normal-magnitude number
processes fine. The vendor insists the value is "just a number".

## Your task

1. Determine which component actually raises the `ArithmeticException`. The stack
   trace bottoms out in a JDK `BigDecimal` constructor and the only Jackson frame
   the team can see is the public `readValue` entry point — but the code that
   decides how the raw characters of the numeric field become a `BigDecimal`, and
   that is supposed to translate bad numeric text into a normal parse error, lives
   somewhere else entirely. Identify the exact repository, file, and method where
   the exception escapes uncaught.

2. Trace the full path from the deserialization call to that origin: the scalar
   value deserializer that handles decimal-typed fields, the parser accessor it
   invokes to pull a decimal out of the token stream, and the number-text helper
   that hands the characters to the recursive big-decimal parsing routine. Name the
   file and the class or method responsible at each hop, and explain why the
   throwing frame lives in a different repository than the one whose name appears
   in the stack trace.

3. Fix the defect at its real origin so that numeric text which overflows during
   big-decimal construction is reported the same way every other malformed number
   is — as a normal number-format parse failure the deserializer already surfaces —
   rather than as an unchecked `ArithmeticException`. Make the change in the
   workspace (leave it as an uncommitted diff); do not paper over it at the
   ingestion layer with a broad catch-all.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories.
- An uncommitted code change at the true origin that converts the overflow into a
  normal number-format parse error.
