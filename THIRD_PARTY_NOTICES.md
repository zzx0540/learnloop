# Third-party notices

LearnLoop's original code is AGPL-3.0-only. Third-party components retain their
respective licenses, copyright notices and attribution requirements. They are
not relicensed under the project's copyright notice.

## Document processing and review

| Component | Reviewed version | License / upstream source |
| --- | --- | --- |
| PyMuPDF | 1.28.2 | AGPLv3 / commercial dual licensing; [source](https://github.com/pymupdf/PyMuPDF) |
| pymupdf4llm | 0.0.27 | AGPLv3 / commercial dual licensing; [source](https://github.com/pymupdf/pymupdf4llm) |
| EbookLib | 0.20 | AGPLv3; [source](https://github.com/aerkalov/ebooklib) |
| py-fsrs | 3.1.0 | MIT; [source](https://github.com/open-spaced-repetition/py-fsrs) |
| Mammoth | 1.13.0 | BSD-2-Clause; [source](https://github.com/mwilliamson/python-mammoth) |

The project does not grant an Artifex commercial license. The open-source route
for the AGPL components is used here. The project license text is in [LICENSE](LICENSE).

## Browser libraries, icons and fonts

React, React Router, TanStack Query, React Flow, KaTeX, remark/rehype and other
frontend runtime packages are documented with their own notices in
[frontend-runtime-notices.txt](LICENSES/frontend-runtime-notices.txt).

Lucide's ISC notice includes the attribution of Feather-derived portions to
Cole Bemis (MIT). KaTeX's JavaScript is MIT, while the embedded metadata of its
bundled font files specifies SIL OFL 1.1. The original font copyright and Reserved
Font Names are preserved in [katex-font-notices.txt](LICENSES/katex-font-notices.txt),
with the [OFL 1.1 terms](LICENSES/OFL-1.1.txt). No font has been modified or relicensed.

## Inventory and distribution

- [Python notice texts](LICENSES/python-dependency-notices.txt)
- [Backend package inventory](docs/backend-dependencies.tsv)
- [Frontend package inventory](docs/frontend-dependencies.tsv)
- Exact installation inputs: `backend/requirements.txt`, `backend/constraints.txt`,
  `frontend/package.json` and `frontend/package-lock.json`.

The source repository does not vendor dependency implementations. Builds install
them from their package registries. License files remain in the installed packages;
the notice collections above supplement, rather than replace, those originals.
The development/build inventory also contains permissive, MPL and CC-BY licensed
packages; retain their notices when redistributing material from them.

The application's source download contains the original project source used for
that build, plus dependency version inputs and these notices. It is not a promise
that a binary redistribution's complete third-party source obligations are met by
a dependency list alone. Anyone distributing images or modified network services
must provide the applicable corresponding source and notices for their version.
