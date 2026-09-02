# Bulk updates reported success and were discarded

A bulk content update reported that products had been updated. Searching for
the replacement term immediately afterwards returned **0 products match**.

## Reproduction

Against the running application, before the fix:

```
1. search "linen"                        → 36 products match
2. plan   linen → zxq-searchscout-…-123  → 36 products, 108 replacements
3. apply                                 → "36 products updated"
4. search "zxq-searchscout-…-123"        → 0 products match
```

Every step reported success. Nothing was stored.

## The runtime this actually ran in

Worth stating, because a stale build would have been the easier explanation and
it was not the cause:

- launched as `flask --app searchscout.web.app run --port 5001 --no-reload`
- `PYTHONPATH=src`, so `searchscout` resolved to this repository's `src/searchscout` — the working tree, not an installed copy (`site-packages` contained no `searchscout`)
- one process, no reloader, no workers, no container, no second copy of the source

The code being executed was the code in the repository. The defect was in it.

## Root cause

Each route built its own catalogue:

```python
@app.post("/search")
def search():
    catalog = build_catalog(settings)      # a new DemoCatalog
...
@app.post("/apply/<plan_id>")
def confirm(plan_id):
    report = apply(build_catalog(settings), ...)   # a *different* new DemoCatalog
```

and `DemoCatalog.__init__` regenerated all 200 products from a fixed seed into
an in-memory dict.

So the apply request:

1. built a catalogue from the seed,
2. wrote the replacements into that instance's dict,
3. returned a success page,
4. and the instance was garbage collected at the end of the request.

The next search built another catalogue from the same seed and searched the
original fixture. The replacement had never existed anywhere durable.

**Read and write referenced different objects, and neither outlived a request.**

## Why the UI reported success

`apply()` treated "the adapter did not raise" as success:

```python
try:
    catalog.update_contents(change.sku, change.after)
except CatalogError as exc:
    report.failed.append(...)
    continue
report.updated.append(change.sku)     # ← claimed on the strength of no exception
```

Writing into a dictionary that is about to be discarded raises nothing. The
report was accurate about what it measured; it measured the wrong thing.

## Why the tests did not catch it

Every test built **one** `DemoCatalog` and passed that same object to `plan()`
and `apply()`:

```python
catalog = DemoCatalog(product_count=40)
result = plan(catalog, "cotton", "hemp")
apply(catalog, result, ...)
assert catalog.get_product(sku).contents == ...   # reads its own write
```

That is the read-your-own-writes case, and it worked. No test crossed a request
boundary, constructed a second adapter over the same store, or checked that a
write survived the object that made it — which is precisely where the failure
lived. The web tests exercised routes but only asserted on rendered copy, never
that a later search saw an earlier apply.

## The fix

**Persistence.** `DemoCatalog` is backed by SQLite (`data/searchscout-demo.db`).
The file is the state, so every catalogue built from the same settings — in any
request, in any process — reads and writes the same store. Products are seeded
only when the table is empty, so a restart does not discard an operator's work,
and `reset()` returns the demo to its fixture state deliberately.

**Read-after-write verification.** A write is no longer trusted because it did
not raise. `apply()` now writes, re-reads the product through the adapter, and
compares the stored content against the planned content:

```
PLAN → WRITE → FRESH READ → COMPARE → VERIFIED
```

`ApplyReport` reports `requested`, `written`, `verified` and `failed`
separately, and only `verified` is presented as success. A catalogue that
accepted every write and stored none now reports *0 verified, 32 failed* rather
than *32 updated*. Rollback is verified the same way.

**Tests that would have caught it.** `tests/test_persistence_invariant.py`
asserts the general property — for a range of term/replacement pairs, apply
then search for the replacement finds the affected products — with every check
crossing an instance or request boundary. One pair uses a value no fixture
could generate, so a hit can only have come from newly stored data.
`tests/test_verification.py` drives an adapter that accepts writes and silently
discards them, which is the old behaviour, and asserts it is reported as
failure.
