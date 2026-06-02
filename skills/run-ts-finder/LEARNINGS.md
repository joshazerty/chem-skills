# run-ts-finder — learnings log

Run-specific notes, appended by `driver.py record` (general/durable fixes also go
into SKILL.md's "Workarounds (self-recorded)" section). Newest at the bottom.

- 2026-06-02  bootstrap  →  g-xTB updated to v2.0.1 (analytic gradients); full GSM→tsq→refine→verify chain validated on HCN→HNC and real DODH logs.
- 2026-06-02  GSM exact-TS stalled: product fragment >300 kcal/mol uphill on g-xTB, climb walked past the saddle  →  Pre-relaxed the product fragment with 'xtb prod.xyz --gxtb --opt' before concatenating endpoints; barrier sane, tsq converged
- 2026-06-02  subgau16/suborc6 jobs were accepted (job IDs returned) but died on the compute node with 'No such file or directory' for the .inp  →  Submit from a SHARED filesystem (under $HOME or the project dir), not /tmp — /tmp is node-local and invisible to compute nodes
- 2026-06-02  Needed to confirm the ORCA backend actually executes for this user (suborc6 only proves the submit script parses)  →  Verified: ORCA 6.0.1 via suborc6 from a $HOME dir ran OptTS+NumFreq to 'ORCA TERMINATED NORMALLY' with NImag=1; driver verify is Gaussian-only, so read ORCA NImag from the .out
