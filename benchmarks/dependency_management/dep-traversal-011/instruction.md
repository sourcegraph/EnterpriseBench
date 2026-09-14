# CVE Blast Radius Analysis: Log4Shell (CVE-2021-44228)

## Context

Log4Shell. You know this one. CVE-2021-44228 is a critical RCE in Apache Log4j 2. A crafted string in a log message triggers JNDI lookup, allowing remote code execution. It affected virtually every Java application that uses log4j-core.

Our Java infrastructure includes Spring Boot services and Kafka. I need a thorough blast radius assessment that goes beyond just checking pom.xml — repos sometimes pull in the vulnerable code indirectly through an intermediate module, or repackage/relocate it under different artifact coordinates entirely.

## What I Need

1. **CVE and Artifact Identification**: Confirm the CVE and the primary vulnerable artifact. Then check each workspace repo for whether it ALSO ships the vulnerable classes a second time, repackaged or relocated under a different artifact name — a manifest scan for `log4j-core` alone will miss this.

2. **Direct Dependents**: For each workspace repo, find exactly how (if at all) it declares a dependency on log4j-core. Cite the manifest file and the specific module/artifact that declares it.

3. **Transitive Paths**: For each dependent you find, trace the path from the vulnerable artifact through any intermediary module(s) to the actual consumer. Note anything that would be invisible to a simple top-level manifest scan.

4. **Fix Version Complexity**: There are multiple fix versions in play — an initial fix that turned out to still be exploitable under certain configs, a later fix that fully closed the hole, and separate backports for teams still on older Java runtimes. For each consumer, determine which log4j version it's actually pinned/resolved to and classify whether that pinned version needs the full fix.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
