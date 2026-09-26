# megane-builder-tools - Agent Instructions (Claude Code, Codex CLI)

Reference MCP server, SDK and conformance checker for megane Builder tool
buttons. **The specification is megane's "Builder Tool Contract (MCP)" page**
(`docs/docs/dev/builder-tools.md` in megane-labs/megane). Section numbers in
code comments (`§5.1`) refer to it. Change the contract there first, then here.

## CRITICAL RULES

1. **Commit messages, PR titles and PR descriptions MUST be in English.**
2. **Never write to stdout from server code.** In stdio mode stdout *is* the
   MCP stream. RadonPy prints unless `radonpy.core.const.print_level` is 3
   (set in `chem/polymer.py`); keep it that way and check any new dependency.
3. **Tools are stateless generators.** A tool gets its arguments (and, for
   `insert` tools, a read-only copy of the open document) and returns a
   `BuilderResult`. An `insert` tool returns only the atoms it adds.
4. **Results carry complete bonds.** Builder draws exactly the bonds a result
   lists and infers none. Never return atoms of a molecule without its bonds.
5. **A molecule argument's mol block is used as given.** Never re-embed or
   re-optimise it; refuse it only when its header says `2D`.
6. **Stochastic tools take `seed` and are reproducible.** Seed every source of
   randomness, including libraries that use the global `random` /
   `numpy.random` state (RadonPy: seed under the module lock).
7. **User-fixable failures raise `ToolError`** with a one-line, actionable
   message. Anything else is a bug.
8. **Every new line ships with tests.** Coverage is ~99 %; keep it there. Tests
   must not take minutes: fake packmol with a shell script for failure paths
   (see `tests/test_packing.py`) instead of running a slow real packing.
9. **Run `make check` before pushing** (lock check, ruff, ty, pytest with
   coverage, and the conformance checker against this server over stdio).
10. **Always create a PR after pushing** and verify CI is green.
11. **A deployed call must finish within 100 s.** App Runner closes every
    request at 120 s; `MEGANE_BUILDER_TOOLS_CALL_TIMEOUT` turns an overrun into
    a tool error. Keep new tools' typical sizes well inside that budget, and
    never write server code that assumes a session survives between requests
    (the deployment is stateless).

## Layout

| Path | What |
| --- | --- |
| `src/megane_builder_tools/sdk/` | Contract constants and annotations (`contract.py`), wire models (`models.py`), the `@builder_tool` decorator (`tool.py`), RDKit/ASE converters (`convert.py`) |
| `src/megane_builder_tools/chem/` | Molecule loading, packmol runner, RadonPy polymerisation, assembly of copies into one `Structure` |
| `src/megane_builder_tools/tools/` | The reference tools (`liquid_box`, `polymer_chain`, `solvate`) |
| `src/megane_builder_tools/server.py` | `create_server()`, stdio/HTTP CLI (`megane-builder-tools`) |
| `src/megane_builder_tools/conformance/` | `megane-builder-conformance` |
| `scripts/record_fixtures.py` | Recorded MCP responses for megane's client tests |
| `Dockerfile`, `deploy/` | Container image and the AWS App Runner deployment (Terraform, see `deploy/README.md`) |

## Commands

| Command | What |
| --- | --- |
| `uv sync` | install everything (packmol, RadonPy, RDKit wheels) |
| `make lint` / `make lint-fix` | ruff check + format |
| `make typecheck` | `ty check` (scoped to `src/`) |
| `make test` / `make coverage` | pytest / with `coverage.xml` |
| `make conformance` | the checker against this server over a real stdio process |
| `make check` | all of the above, as CI runs it |
| `make serve` / `make serve-http` | run the server |

## Adding a tool

1. Write it in `src/megane_builder_tools/tools/<name>.py` with `@builder_tool`.
   Give every required argument `examples` so the checker can call it.
2. Add it to `TOOLS` in `server.py`.
3. Unit tests in `tests/test_<name>.py`, covering every `ToolError`.
4. `make check` (the conformance step calls the new tool).
5. Document it in the README table, and in megane's contract page when it is
   one of the worked examples.
