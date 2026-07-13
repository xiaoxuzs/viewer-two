# Repository guardrails

- Read `README.md` and the format constraints before development.
- A `BaseBlockTool` may only create/update typed blocks. It must not write `.zp`, set `output_zp_path`, or invoke validation.
- `MockRawToMzmlTool` remains a `pre_conversion` step, not a block tool.
- `PipelineRunner` must not branch on `source_type`, MS level, identification, or other mass-spectrometry business concepts.
- `StepRegistry` registers and retrieves named steps only; it must not choose plans or contain business branching.
- `ZpWriter` is the sole production component allowed to write a final `.zp` file. It must not synthesize or repair missing business blocks, indexes, string pools, or references.
- All nine required logical blocks must remain in every version-1 directory; `extensions` and `core_chromatograms` remain present when empty.
- Changing a core block field requires evaluating whether `ZP_VERSION` must change.
- Any format change must update constants, models, serialization, writer, reader, validator, tests, and README together.
- Treat the documented P0 version-1 byte layout, block names/order, checksum object, JSON rules, ID relations, and RT-in-seconds convention as frozen. Do not silently reinterpret version 1.
- Do not invent sentinel values for missing scan numbers or charges; make an explicit schema/version or extension decision before real mzML ingestion.
- The P0 `arrays` block is intentionally a JSON list of records carrying unique `array_id` values. Do not describe it as on-disk constant-time random access.
- Run `python -m pytest` after every change. Never relax the validator merely to make a test pass.
- Mock implementation details must not leak into Reader or Validator.
- Do not depend on deleted projects, Viewer code, a frontend, or a database.
