# megane-builder-tools

Structure-building tools for [megane](https://github.com/megane-labs/megane)
Builder, served over the [Model Context Protocol](https://modelcontextprotocol.io/).

megane Builder is a JavaScript application; most structure-generation software
is Python. Instead of porting it, Builder shows **tool buttons** whose work runs
in an MCP server. This repository is:

- the **reference server** — `liquid_box`, `polymer_chain`, `solvate`;
- the **SDK** (`megane_builder_tools.sdk`) for writing your own tools;
- the **conformance checker** (`megane-builder-conformance`) that verifies any
  server against the contract.

The contract itself — which tools become buttons, how arguments and results
look, how results enter a Builder document — is specified in megane:
[Builder Tool Contract (MCP)](https://megane-labs.github.io/megane/dev/builder-tools).
Every server here is also an ordinary MCP server, so LLM clients (Claude Code,
Claude Desktop, megane's own chat) can call the same tools.

## Tools

| Tool | Builder button | Apply mode | Backed by |
| --- | --- | --- | --- |
| `liquid_box` | Liquid box | new document | [packmol](https://github.com/m3g/packmol) |
| `polymer_chain` | Polymer chain | new document | [RadonPy](https://github.com/RadonPy/RadonPy) (`polymerize_rw`) |
| `solvate` | Solvate | insert into the open document | packmol |

- **liquid_box** fills a periodic box with molecules at a target density. Each
  component is a molecule and a count; the box is sized from the density.
- **polymer_chain** builds a linear homopolymer from a monomer and its head and
  tail atoms. Each junction removes one hydrogen from the head and the tail
  atom (RadonPy linkers), and the chain ends keep theirs. Tacticity is not
  controlled: every unit has the monomer's stereochemistry. RadonPy
  re-optimises the whole growing chain with MMFF94 after every step, so time
  grows steeply with length: about 40 s for 100 ethylene units and 7 min for
  200 (the maximum).
- **solvate** fills the free space of the open structure's orthorhombic cell
  with solvent at a target density. It returns only the solvent.

All three take a `seed`: the same arguments and seed give the same structure.

## Use

```bash
uvx megane-builder-tools            # stdio, what megane and LLM clients spawn
```

Configure it like any MCP server:

```json
{
  "mcpServers": {
    "megane-builder-tools": { "command": "uvx", "args": ["megane-builder-tools"] }
  }
}
```

For the static megane webapp, which cannot spawn processes, serve Streamable HTTP
on loopback:

```bash
uvx megane-builder-tools --transport http --port 8765 \
    --allow-origin https://megane-labs.github.io
```

HTTP mode checks `Host`/`Origin` and requires `Authorization: Bearer <token>`.
The token is taken from `--token` or `$MEGANE_BUILDER_TOOLS_TOKEN`; without one,
a random token is generated and printed to stderr.

## Write your own tool

```python
from typing import Annotated

from pydantic import Field
from mcp.server.mcpserver import MCPServer

from megane_builder_tools.sdk import BuilderResult, Length, Seed, Structure, builder_tool


@builder_tool(title="Argon gas", category="bulk", apply="new_document", expected_seconds=1)
def argon_gas(
    count: Annotated[int, Field(ge=1, le=10_000, title="Atoms", examples=[100])],
    seed: Seed,
    edge: Annotated[Length, Field(gt=0, title="Box edge")] = 30.0,
) -> BuilderResult:
    """Place argon atoms uniformly at random in a cubic box."""
    import numpy as np

    positions = np.random.default_rng(seed).random((count, 3)) * edge
    structure = Structure.from_arrays([18] * count, positions, cell=np.eye(3) * edge)
    return BuilderResult(name="argon", structure=structure)


server = MCPServer("my-tools")
argon_gas.register(server)
server.run("stdio")
```

The decorator derives the Builder marker (`stochastic` from the `Seed`
parameter, `document` from a `DocumentInput` parameter), the output schema,
the text summary for LLM clients, and `provenance`. Synchronous tools run in a
worker thread; declare a `progress: Progress` parameter to report progress.
Molecule arguments (`MoleculeInput`), atom picks (`atom_of("monomer")`), the
open document (`DocumentInput`), cells, selections and units come from
`megane_builder_tools.sdk`. Give every required argument an `examples` value so
the checker and LLM clients can make a valid call.

Then check it:

```bash
megane-builder-conformance -- python my_tools.py
megane-builder-conformance --url http://127.0.0.1:8765/mcp --token TOKEN
```

The checker validates the tool markers, the form-generation subset, widget
schemas, seeds and document rules, calls every tool with sample arguments,
validates the results (lengths, complete bonds, an insert result that does
not repeat the document), checks that a seed reproduces its structure, and
checks that invalid arguments are refused. It exits non-zero on any error.

## Develop

```bash
uv sync
make check        # lock check, ruff, ty, pytest with coverage, conformance over stdio
make fixtures     # re-record tools/list and tools/call JSON for megane's tests
```

See [AGENTS.md](AGENTS.md) for the rules this repository follows.

## License

MIT. packmol (MIT), RadonPy (BSD-3-Clause) and RDKit (BSD-3-Clause) are
installed as dependencies.
