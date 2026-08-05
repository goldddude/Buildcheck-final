# BuildCheck Compliance Prototype

This prototype now follows the BuildCheck compliance workflow:

1. Home
2. New Compliance Check
3. Project Details
4. Regulation Selection
5. Upload Drawing
6. Compliance Analysis
7. Compliance Report

The app asks for project information, Maharashtra planning authority, building type and drawing upload first. After analysis, it shows the applicable Maharashtra schemes/regulations for that authority and lets you choose which ones to include in the final downloadable PDF report.

## Run

```powershell
.\start_buildcheck.bat
```

Open:

```text
http://127.0.0.1:8080
```

## Current Scope

Supported drawing inputs:

- PDF annotations, including explicit area labels such as `R.C.A-40.59` and room dimensions such as `3050X3350`.
- DXF geometry, using unit-aware closed polyline extraction and preferred area layers such as `RERA`, `RCA`, `CARPET`, `BUILT` and `BUA`.
- DWG uploads, with a clear conversion note because DWG needs a proprietary converter before reliable measurement.

Compliance workflow features:

- Maharashtra authority and regulation registry in `data/regulations_maharashtra.json`.
- Applicable Maharashtra schemes shown after analysis.
- Scheme selector placed in the far-right report panel.
- Focused analysis summary with main observations, failed/review items and improvements needed.
- Downloadable PDF report.
- Initial room-wise and building-wise compliance checks.
- Compliance score and suggested corrections.

## Important Accuracy Note

The current rule checks use a seed clause database so the workflow can run end to end. Before statutory use, replace the seed thresholds with verified clause records for UDCPR 2020, DCPR 2034, NBC 2016, CFO Fire Rules and MoEFCC Guidelines.

For production approval automation, the next steps are:

- Add a verified clause database with exact rule and clause references.
- Add DWG-to-DXF conversion through an approved server-side converter.
- Add CAD layer mapping for plot boundary, footprint, setbacks, open space, RG, parking, staircase, lift core and corridors.
- Add floor-wise and flat-wise grouping.
- Add jurisdiction-specific scheme matching.
