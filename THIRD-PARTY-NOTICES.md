# Third-party notices

The Orchestration Harness Kit contains, adapts, invokes, or records provenance
for third-party software. Those materials are not relicensed under the kit's
proprietary terms. Their original licenses control.

## Directly identified components

| Component | Relationship | License and attribution |
|---|---|---|
| [Hermes Agent Self-Evolution](https://github.com/NousResearch/hermes-agent-self-evolution) | Modified, sanitized source under `system-snapshot/hermes-agent-self-evolution/` | MIT; Copyright (c) 2026 Nous Research |
| [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent) | Pinned external source/runtime used by the governed Prime bridge | MIT; Copyright (c) 2025 Mario Zechner; Copyright (c) 2026 Prime Intellect |
| [Oh My Pi](https://github.com/can1357/oh-my-pi) (`@oh-my-pi/pi-coding-agent`) | Pinned OMP coding-sidecar dependency | MIT; Copyright (c) 2025 Mario Zechner; Copyright (c) 2025-2026 Can Bölük; Copyright (c) 2026 Stencil Labs, Inc. |
| [DSPy](https://github.com/stanfordnlp/dspy) and [GEPA](https://github.com/gepa-ai/gepa) | Dependencies referenced by Hermes Agent Self-Evolution | MIT; consult each dependency's distributed metadata for its exact notice |
| [Darwinian Evolver](https://github.com/imbue-ai/darwinian_evolver) | Optional external CLI only; no source is bundled by this kit | AGPL-3.0; use remains subject to the upstream license |

## MIT permission notice

For the MIT-licensed components identified above, permission is granted, free
of charge, to any person obtaining a copy of the applicable software and
associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy,
modify, merge, publish, distribute, sublicense, and/or sell copies of the
Software, and to permit persons to whom the Software is furnished to do so,
subject to the following condition:

The applicable copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Transitive dependencies

Container and language-package dependencies retain their own licenses. Consult
the SBOM and package metadata shipped with the relevant artifact. This notice
records the directly identified components; it is not a representation that a
built image or dependency bundle has completed legal clearance.
