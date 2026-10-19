"""The house style, in one place, before it is copied into each app.

`theme.py` and `housestyle.py` here are the originals; `sync.py` copies them
byte for byte into every app's package, which is where they are actually
imported from. This `__init__.py` exists so that the two can also be imported
from *here* — by `sync.py`, by `test_house.py`, and by anything else that
wants to look at the house style without going through an app — because
`housestyle.py` imports `theme.py` as a sibling of its own package, which is
what makes it work after it is vendored.

Nothing else belongs in this file. It is deliberately not copied into the
apps: an app's package already has its own.
"""
