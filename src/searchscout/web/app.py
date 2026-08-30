"""Review UI.

The original was a Flask app because the people doing the editing were
merchandisers, not engineers. That is still the right interface, so it is kept —
with the change that matters: the form cannot write. It plans, shows the diff,
and requires a second, explicit confirmation before anything is applied.
"""

from __future__ import annotations

from typing import Any

from flask import Flask, redirect, render_template, request, url_for

from searchscout.audit import AuditLog
from searchscout.config import get_settings
from searchscout.matching import MatchMode
from searchscout.planner import Plan, apply, plan
from searchscout.runtime import build_catalog
from searchscout.search import SearchField, search_products

#: Plans held between the preview request and the confirmation that applies
#: them. In-process and deliberately simple: this is a single-operator internal
#: tool, and a plan that does not survive a restart is the safe failure.
_PLANS: dict[str, Plan] = {}


def create_app() -> Flask:
    app = Flask(__name__)
    settings = get_settings()

    @app.get("/")
    def index() -> str:
        return render_template("index.html", adapter=settings.adapter)

    @app.post("/search")
    def search() -> str:
        term = (request.form.get("term") or "").strip()
        mode = MatchMode(request.form.get("mode") or MatchMode.CASE_INSENSITIVE)
        # Searching is read-only, so it may look at fields a bulk edit can
        # never write. The scope here has no bearing on what `plan()` touches.
        field = SearchField(request.form.get("field") or SearchField.ALL)
        if not term:
            return render_template(
                "index.html", adapter=settings.adapter, error="Enter a search term."
            )

        catalog = build_catalog(settings)
        hits = search_products(catalog.iter_products(), term, mode=mode, field=field)
        rows: list[dict[str, Any]] = [
            {
                "sku": hit.product.sku,
                "name": hit.product.name,
                "matches": hit.matches,
                "context": hit.primary.context,
                "matched_in": hit.primary.field.value,
                "also": [f.value for f in hit.fields[1:]],
            }
            for hit in hits
        ]

        return render_template(
            "results.html",
            term=term,
            mode=mode.value,
            field=field.value,
            rows=rows[:100],
            total=len(rows),
            adapter=settings.adapter,
        )

    @app.post("/plan")
    def preview() -> str:
        term = (request.form.get("term") or "").strip()
        replacement = request.form.get("replacement") or ""
        mode = MatchMode(request.form.get("mode") or MatchMode.LITERAL)
        if not term:
            return render_template(
                "index.html", adapter=settings.adapter, error="Enter a search term."
            )

        result = plan(build_catalog(settings), term, replacement, match_mode=mode)
        _PLANS[result.id] = result
        return render_template(
            "plan.html",
            plan=result,
            adapter=settings.adapter,
            over_cap=result.product_count > settings.max_products_per_plan,
            cap=settings.max_products_per_plan,
        )

    @app.post("/apply/<plan_id>")
    def confirm(plan_id: str) -> Any:
        stored = _PLANS.get(plan_id)
        if stored is None:
            return redirect(url_for("index"))

        report = apply(
            build_catalog(settings),
            stored,
            audit=AuditLog(settings.audit_path),
            rollback_dir=settings.rollback_dir,
            rate_per_second=settings.rate_per_second,
            max_products=settings.max_products_per_plan,
        )
        _PLANS.pop(plan_id, None)
        return render_template("applied.html", report=report, plan=stored, adapter=settings.adapter)

    return app


app = create_app()
