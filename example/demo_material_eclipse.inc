-- ECLIPSE keyword include generated from demo_material(3).txt
-- Assumed unit system: METRIC (pressure=bar, viscosity=cP, density=kg/m3, rates=sm3/day)
-- Source does not provide grid, regions, well completions, start date, Rs, or saturated-oil PVT data.

-- =========================
-- PROPS
-- =========================

-- Oil/water relative permeability and oil-water capillary pressure
-- SW      KRW      KROW     PCOW(bar)
SWOF
  0.15    0.000    0.900    0.35
  0.30    0.048    0.552    0.18
  0.50    0.205    0.251    0.07
  0.70    0.512    0.048    0.02
  0.90    0.800    0.000    0.00 /

-- Oil PVT represented as dead-oil PVT because Rs is not supplied.
-- P(bar)  Bo(rm3/sm3)  Muo(cP)
PVDO
  100     1.25          1.10
  200     1.20          1.35
  300     1.16          1.60
  400     1.13          1.85 /

-- Dry-gas PVT
-- P(bar)  Bg(rm3/sm3)  Mug(cP)
PVDG
  100     0.0120        0.012
  200     0.0058        0.014
  300     0.0038        0.016
  400     0.0028        0.018 /

-- Water PVT at reference pressure 300 bar
-- Pref(bar)  Bw(rm3/sm3)  Cw(1/bar)  Muw(cP)  Cv(1/bar)
PVTW
  300        1.02         4.2E-5     0.45     0.0 /

-- Surface densities: oil, water, gas (kg/m3)
DENSITY
  850   1010   0.95 /

-- Rock compressibility at reference pressure
-- Pref(bar)  Cr(1/bar)
ROCK
  300        4.5E-5 /

-- =========================
-- SCHEDULE
-- =========================

-- Production wells A15 and B2: liquid-rate target 500 sm3/day, minimum BHP 80 bar.
WCONPROD
  'A15'  'OPEN'  'LRAT'  1*  1*  1*  500  1*  80 /
  'B2'   'OPEN'  'LRAT'  1*  1*  1*  500  1*  80 /
/

-- Water injector C1: injection-rate target 800 sm3/day, maximum BHP 420 bar.
WCONINJE
  'C1'  'WATER'  'OPEN'  'RATE'  800  1*  420 /
/

-- Five years with quarterly report steps. No calendar start date was supplied,
-- so 20 equal 91.25-day report steps are used (1825 days total).
TSTEP
  20*91.25 /
