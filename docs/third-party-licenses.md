# Dependency license review

The author has not selected a license for LearnLoop. Public visibility and permission to reuse code are separate decisions. This candidate does not declare MIT, Apache or another project license.

The existing document parsers require attention before a public release:

| Component | Version reviewed | Upstream license information |
| --- | --- | --- |
| PyMuPDF | 1.28.2 | AGPL-3.0 or commercial licensing: https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright |
| pymupdf4llm | 0.0.27 | AGPL/commercial license metadata; upstream source: https://github.com/pymupdf/pymupdf4llm |
| EbookLib | 0.20 | GNU AGPL; https://github.com/aerkalov/ebooklib |

Review the exact dependency license texts and distribution obligations before selecting the project's license or distributing a built image. This preparation preserves parser functionality and does not grant a commercial license or assert that a permissive project license resolves those obligations. The author can choose a compatible licensing approach or commission replacement parsers and compatibility tests.

`backend-dependencies.tsv` and `frontend-dependencies.tsv` record package metadata for the reviewed environment. They are inventories, not a complete legal clearance or replacement for upstream notices. No dependency source tree, database content or vendor binary is included in the review ZIP.
