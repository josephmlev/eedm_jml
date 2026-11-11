# In-shield coils

Designs coil wire patterns for the inside of our magnetic shield, using the Nottingham `fields_in_shields` code (Hobson, Packer, Fromhold; DOI 10.5281/zenodo.4442661). You give it the shield size, the coil former size and the field you want; it gives back wire paths that make that field with the shield's distortion included.

## Setup

```
inShieldCoils/
  fields_in_shields-master/   their code, don't edit
  scripts/                    our code and run scripts
```

# In-shield coils

Coil designs for the inside of our magnetic shield, made with the Nottingham `fields_in_shields` code and checked with bfieldtools.


Check what's installed:

```bash
python -c "import importlib, importlib.util; [print(m, getattr(importlib.import_module(m),'__version__','?')) if importlib.util.find_spec(m) else print(m, 'MISSING') for m in ['numpy','scipy','matplotlib','mpmath','trimesh','bfieldtools']]"
```

Install anything missing with `pip install <name>`.

## Running a design

From inside `fields_in_shields-master`:

```bash
python generatewires_cylindrical.py
```

Settings are at the top of the script (meters, tesla). The ones you'll change:

- `a`, `L`: shield inner radius and length
- `rho_cs`, `z_prime_c1s`, `z_prime_c2s`: former radius and winding ends
- `B_des_ind`: target field, e.g. `b1p1` uniform Bx, `b10` uniform Bz
- `savepath`: output folder (defaults to `simulations/Bx`)

Wire paths come out in `contours_levels_<n>/`, one file per wire (x, y, z per row). Every wire carries the same current.

## Watch out for

- If coil length / shield length is a simple fraction (like 0.9 m / 1 m), the code drops some terms. Make the coil 1e-7 m longer.
- Wires that cross phi = 0 are split into two files; join them before winding.
- The model assumes a perfect closed shield with no holes. Check real designs before building.
