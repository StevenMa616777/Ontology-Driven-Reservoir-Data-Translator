# Heterogeneous Reservoir Test Set — Ground Truth

These files intentionally describe overlapping facts using different schemas, units, languages, identifiers and document styles.

## Canonical facts inherited from the supplied demo

### Relative permeability — sample X-12, water displacement
Sw | Krw | Krow | Pcow (bar)
0.15 | 0.000 | 0.900 | 0.35
0.30 | 0.048 | 0.552 | 0.18
0.50 | 0.205 | 0.251 | 0.07
0.70 | 0.512 | 0.048 | 0.02
0.90 | 0.800 | 0.000 | 0.00

### Oil PVT
Pressure (bar): 100 / 200 / 300 / 400
Bo: 1.25 / 1.20 / 1.16 / 1.13 rm3/sm3
Viscosity: 1.10 / 1.35 / 1.60 / 1.85 cP

### Gas PVT
Bg: 0.0120 / 0.0058 / 0.0038 / 0.0028 rm3/sm3
Viscosity: 0.012 / 0.014 / 0.016 / 0.018 cP

### Water / rock
Bw = 1.02
Water compressibility = 4.2e-5 1/bar @ 300 bar
Water viscosity = 0.45 cP
Oil/water/gas density = 850 / 1010 / 0.95 kg/m3
Rock compressibility = 4.5e-5 1/bar @ 300 bar

### Well controls
A15, B2: producers, liquid-rate target 500 m3/day, minimum BHP 80 bar.
C1: water injector, injection target 800 m3/day, maximum BHP 420 bar.
Simulation: 5 years; quarterly reporting.

## Intended heterogeneity
- Chinese vs English terminology.
- Sw represented as fraction vs percent.
- Pc represented in bar, kPa, psi and Pa.
- Pressure represented in bar vs MPa/psi.
- `Krow`, `KRO`, `oilRelPerm`, `油相相对渗透率` denote the same underlying concept in this test set.
- Well identity represented as a single `wellId` vs composite Field+Block+Well_No.
- m3/day vs bbl/day.
- Structured CSV/JSON vs simulator keyword deck vs free-text engineer notes.
- Some documents intentionally omit unrelated fields rather than filling them in.

Note: unit-converted values are generated from the demo facts and rounded for testing; they are not additional facts from the source document.
