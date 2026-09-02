"""Catalogue operations console.

Every route builds a catalogue through `build_catalog`, and — since the demo
adapter became SQLite-backed — every one of those references the same store.
That is the property the application previously lacked: search and update used
separate in-memory catalogues, so a bulk update reported success and vanished.

The form can request work. It cannot perform it: previews are read-only, and an
apply consumes a plan the server computed and re-verifies every write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, redirect, render_template, request, url_for

from searchscout.audit import AuditLog, Operation, OperationLog
from searchscout.catalog.base import CatalogAdapter, Product, ProductNotFoundError, StockStatus
from searchscout.catalog.demo import DemoCatalog
from searchscout.config import Settings, get_settings
from searchscout.health import CONTENT_ISSUES, ISSUE_LABELS, CatalogHealth, Issue, assess
from searchscout.matching import MatchMode
from searchscout.planner import Plan, apply, plan, rollback
from searchscout.runtime import build_catalog
from searchscout.search import SearchField, listing, search_products

#: Plans held between the preview request and the confirmation that applies
#: them. In-process and deliberately simple: this is a single-operator internal
#: tool, and a plan that does not survive a restart is the safe failure.
_PLANS: dict[str, Plan] = {}


def _stock_filter(raw: str | None) -> StockStatus | None:
    try:
        return StockStatus(raw) if raw else None
    except ValueError:
        return None


def _issue_filter(raw: str | None) -> Issue | None:
    try:
        return Issue(raw) if raw else None
    except ValueError:
        return None


def create_app(settings: Settings | None = None) -> Flask:
    app = Flask(__name__)
    settings = settings or get_settings()
    audit = AuditLog(settings.audit_path)
    operations = OperationLog(settings.audit_path.parent / "operations.jsonl")

    def catalog() -> CatalogAdapter:
        return build_catalog(settings)

    def health_of(products: list[Product]) -> CatalogHealth:
        return assess(
            products,
            low_stock_threshold=settings.low_stock_threshold,
            short_description_chars=settings.short_description_chars,
        )

    @app.context_processor
    def globals_() -> dict[str, Any]:
        return {
            "adapter": settings.adapter,
            "is_demo": settings.adapter == "demo",
            "issue_labels": ISSUE_LABELS,
        }

    # ---------------------------------------------------------- dashboard

    @app.get("/")
    def dashboard() -> str:
        products = catalog().iter_products()
        health = health_of(products)
        # Counts are resolved here rather than by looking enum keys up from
        # the template, which is both fragile and hard to read.
        counts = {issue.value: health.count(issue) for issue in Issue}
        return render_template(
            "dashboard.html",
            total=health.total,
            healthy=health.healthy,
            counts=counts,
            issues=[(i, health.count(i)) for i in Issue if health.count(i)],
            recent=operations.read(limit=5),
            low_stock_threshold=settings.low_stock_threshold,
        )

    # ------------------------------------------------------------- search

    @app.route("/search", methods=["GET", "POST"])
    def search() -> str:
        form = request.form if request.method == "POST" else request.args
        term = (form.get("term") or "").strip()
        mode = MatchMode(form.get("mode") or MatchMode.CASE_INSENSITIVE)
        field = SearchField(form.get("field") or SearchField.ALL)
        stock = _stock_filter(form.get("stock"))
        issue = _issue_filter(form.get("issue"))

        products = catalog().iter_products()
        health = health_of(products)
        issues_by_sku = {p.product.sku: p.issues for p in health.products}

        # Filters narrow the set; the term then searches whatever remains, so
        # "low stock products mentioning cotton" is one question, not two.
        if stock is not None:
            products = [
                p for p in products if p.stock_status(settings.low_stock_threshold) is stock
            ]
        if issue is not None:
            wanted = set(health.skus_with(issue))
            products = [p for p in products if p.sku in wanted]

        if term:
            hits = search_products(products, term, mode=mode, field=field)
        else:
            # A filter with no term is a legitimate question — "show me
            # everything out of stock" — so it lists rather than demanding one.
            hits = listing(products)

        rows = [
            {
                "sku": hit.product.sku,
                "name": hit.product.name,
                "category": hit.product.category or "—",
                "price": hit.product.price,
                "stock": hit.product.stock_quantity,
                "status": hit.product.stock_status(settings.low_stock_threshold),
                "issues": issues_by_sku.get(hit.product.sku, []),
                "matches": hit.matches,
                "context": hit.primary.context if hit.primary else "",
                "matched_in": hit.primary.field.value if hit.primary else "",
            }
            for hit in hits
        ]

        return render_template(
            "results.html",
            term=term,
            mode=mode.value,
            field=field.value,
            stock=stock.value if stock else "",
            issue=issue.value if issue else "",
            rows=rows[:200],
            total=len(rows),
            low_stock_threshold=settings.low_stock_threshold,
        )

    # -------------------------------------------------------------- plan

    @app.post("/plan")
    def preview() -> str:
        term = (request.form.get("term") or "").strip()
        replacement = request.form.get("replacement") or ""
        mode = MatchMode(request.form.get("mode") or MatchMode.LITERAL)
        if not term:
            return render_template("index.html", error="Enter a search term.")

        result = plan(catalog(), term, replacement, match_mode=mode)
        _PLANS[result.id] = result
        return render_template(
            "plan.html",
            plan=result,
            over_cap=result.product_count > settings.max_products_per_plan,
            cap=settings.max_products_per_plan,
        )

    @app.post("/apply/<plan_id>")
    def confirm(plan_id: str) -> Any:
        stored = _PLANS.get(plan_id)
        if stored is None:
            return redirect(url_for("index"))

        # Only the rows the operator left selected.
        selected = set(request.form.getlist("sku")) or None

        report = apply(
            catalog(),
            stored,
            audit=audit,
            rollback_dir=settings.rollback_dir,
            rate_per_second=settings.rate_per_second,
            max_products=settings.max_products_per_plan,
            only_skus=selected,
        )
        _PLANS.pop(plan_id, None)

        operations.append(
            Operation(
                plan_id=report.plan_id,
                kind="bulk_update",
                search_term=stored.search_term,
                replacement=stored.replacement,
                match_mode=str(stored.match_mode),
                requested=len(report.requested),
                written=len(report.written),
                verified=len(report.verified),
                failed=len(report.failed),
                replacements=report.replacements,
                rollback_path=str(report.rollback_path) if report.rollback_path else None,
                at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        )
        return render_template(
            "applied.html",
            report=report,
            plan=stored,
            minutes_each=settings.manual_minutes_per_product,
        )

    # ---------------------------------------------------------- activity

    @app.get("/activity")
    def activity() -> str:
        return render_template("activity.html", operations=operations.read(limit=50))

    @app.route("/rollback/<plan_id>", methods=["GET", "POST"])
    def rollback_operation(plan_id: str) -> Any:
        operation = operations.get(plan_id)
        if operation is None or not operation.rollback_path:
            return redirect(url_for("activity"))

        if request.method == "GET":
            return render_template("rollback.html", operation=operation)

        report = rollback(catalog(), audit, Path(operation.rollback_path))
        operations.append(
            Operation(
                plan_id=f"{plan_id}-rollback",
                kind="rollback",
                search_term=operation.replacement,
                replacement=operation.search_term,
                match_mode=operation.match_mode,
                requested=len(report.requested),
                written=len(report.written),
                verified=len(report.verified),
                failed=len(report.failed),
                replacements=0,
                rollback_path=None,
                at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        )
        return render_template("rolled-back.html", report=report, operation=operation)

    # ----------------------------------------------------------- product

    @app.get("/product/<sku>")
    def product_detail(sku: str) -> Any:
        active = catalog()
        try:
            product = active.get_product(sku)
        except ProductNotFoundError:
            return render_template("not-found.html", sku=sku), 404

        health = health_of(active.iter_products())
        issues: list[Issue] = next((p.issues for p in health.products if p.product.sku == sku), [])
        return render_template(
            "product.html",
            product=product,
            issues=issues,
            status=product.stock_status(settings.low_stock_threshold),
            content_issues=[i for i in issues if i in CONTENT_ISSUES],
        )

    # -------------------------------------------------------------- demo

    @app.route("/reset-demo", methods=["GET", "POST"])
    def reset_demo() -> Any:
        if settings.adapter != "demo":
            return redirect(url_for("dashboard"))
        if request.method == "GET":
            return render_template("reset.html")
        # Narrowed rather than cast: `reset` is deliberately not part of
        # CatalogAdapter — no protocol should offer a way to wipe a real
        # store — so this route has to prove it is talking to the demo one.
        active = catalog()
        if not isinstance(active, DemoCatalog):
            return redirect(url_for("dashboard"))
        count = active.reset()
        operations.clear()
        return render_template("reset-done.html", count=count)

    @app.get("/index")
    def index() -> str:
        return render_template("index.html")

    return app


app = create_app()
