# Tests

## Run them

```sh
python3 scripts/run_unit_tests.py          # add -v for more verbosity
```

That is the canonical command. It discovers `tests/test_*.py` itself, so a new test
file is picked up automatically, and it prints the interpreter it used and whether the
optional dependencies were importable **before** the results:

```
interpreter : /usr/sbin/python3
python      : 3.14.7
test files  : 12
PyQt6       : yes
cryptography: yes
keyring     : yes
```

## Which run is the real one

**Host python3.14 is the run that tests anything**, because that is where PyQt6 lives.
It reports `SKIPPED : 0`.

Under an isolated environment without PyQt6 the same command still exits 0, but prints a
warning and `SKIPPED : 13`. Those 13 are the server render tests and the vault key
custody tests. A green run there is partly green because it is not testing what it
appears to, so do not quote it as the suite result.

To reproduce the degraded run deliberately:

```sh
uv run --no-project --with cryptography --with keyring --with aiohttp \
  --with adblock --with jinja2 --with httpx --with lxml \
  python scripts/run_unit_tests.py
```

## Why not `unittest discover`

`tests/` has no `__init__.py` on purpose, so `unittest discover` cannot import it, and
adding one would change setuptools auto-discovery for the package. The runner loads each
file by path instead, which keeps packaging untouched.

It also fixes the two ways a suite rots silently:

- a hand-written list of modules goes stale and new tests never run
- skips are invisible unless you go looking for them

## Known limits

These are string and structural assertions on source. They cannot see a bug in
behaviour, so anything touching the browser, the notices, or `localStorage` is unverified
until someone runs the app.
