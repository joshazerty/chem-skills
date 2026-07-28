"""Physical constants and unit conversions (SI base, energies in J/mol).

Everything downstream works in a single internal unit system:
  * energy   -> J/mol
  * time     -> s
  * amount   -> mol, concentration -> mol/L
Temperatures are in K. Conversions in/out live here so the rest of the code
never sees a magic factor.
"""
from __future__ import annotations

# --- fundamental constants (CODATA 2018) ---
KB_J = 1.380649e-23        # Boltzmann constant, J/K
H_J = 6.62607015e-34       # Planck constant, J*s
R_J = 8.314462618          # gas constant, J/mol/K
NA = 6.02214076e23         # Avogadro, 1/mol
C_CM = 2.99792458e10       # speed of light, cm/s (for wavenumbers)

# kB/h in 1/(K*s): the TST pre-exponential slope
KB_OVER_H = KB_J / H_J     # 2.083661912e10

# --- energy unit -> J/mol ---
ENERGY_TO_J_PER_MOL = {
    "j/mol": 1.0,
    "kj/mol": 1.0e3,
    "kcal/mol": 4184.0,
    "ev": 96485.33212,        # eV per particle -> J/mol
    "hartree": 2625499.6,     # Ha -> J/mol
    "cm-1": 11.96266,         # wavenumber -> J/mol
}


def to_j_per_mol(value, unit: str):
    """Convert an energy in `unit` to J/mol."""
    u = unit.strip().lower()
    if u not in ENERGY_TO_J_PER_MOL:
        raise ValueError(
            f"unknown energy unit {unit!r}; known: {sorted(ENERGY_TO_J_PER_MOL)}"
        )
    return value * ENERGY_TO_J_PER_MOL[u]


def from_j_per_mol(value_j, unit: str):
    u = unit.strip().lower()
    return value_j / ENERGY_TO_J_PER_MOL[u]


def rt(T, unit: str = "j/mol"):
    """RT at temperature T, returned in the requested energy unit."""
    return from_j_per_mol(R_J * T, unit)


def wavenumber_to_hz(nu_cm):
    """cm^-1 -> Hz."""
    return nu_cm * C_CM
