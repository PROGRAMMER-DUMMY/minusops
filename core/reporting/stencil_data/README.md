# stencil_data

## `aws4_shapes.txt`

Every shape name in draw.io's `mxgraph.aws4` stencil library, one per line, lowercased with
spaces normalised to underscores -- the form a `resIcon=mxgraph.aws4.<name>` reference takes.

**Names only. No artwork.** AWS Architecture Icons may be used to draw diagrams and may not
be redistributed or bundled, so nothing vendor-owned is committed here and nothing is drawn
from this file. draw.io renders the official icon from its own library at view time; this
list exists so that [`diagram_check.py`](../diagram_check.py) can tell whether the name we
referenced is one that library actually has.

That check is not theoretical. Five names in `drawio_generator._STENCILS` were wrong when the
list was first extracted -- `iam`, `glue_crawler`, `subnets`, and the two partner stencils --
and every one rendered as a blank tile with no error anywhere.

**Source:** `src/main/webapp/stencils/aws4.xml` in [jgraph/drawio](https://github.com/jgraph/drawio),
extracted from the `name` attribute of each `<shape>`. Regenerate with:

```
python core/reporting/stencil_data/refresh.py
```

The refresh is deliberately a script run by a person rather than something the test suite
does. A test that fetched the library would fail on a plane, and a list that silently updated
itself would stop being the fixed thing the check is made against.
