"""Command line interface.

scout search "cotton"                  find products, change nothing
scout plan "cotton" "organic cotton"   preview every edit
scout apply "cotton" "organic cotton"  perform them, with an audit trail
scout rollback var/rollback/<id>.json  put it back
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from searchscout.audit import AuditLog
from searchscout.config import get_settings
from searchscout.matching import MatchMode
from searchscout.planner import apply as apply_plan
from searchscout.planner import plan as build_plan
from searchscout.planner import rollback as run_rollback
from searchscout.runtime import build_catalog
from searchscout.search import SearchField, search_products

app = typer.Typer(add_completion=False, help="Catalogue content search and safe bulk editing")
console = Console()

ModeOption = Annotated[MatchMode, typer.Option("--mode", help="How the term is matched.")]
FieldOption = Annotated[
    SearchField,
    typer.Option("--field", help="Which fields to search. Editing is unaffected."),
]


@app.command()
def search(
    term: str,
    mode: ModeOption = MatchMode.CASE_INSENSITIVE,
    field: FieldOption = SearchField.ALL,
    limit: int = 20,
) -> None:
    """Find products by name, SKU or description. Read-only."""
    catalog = build_catalog()
    hits = search_products(catalog.iter_products(), term, mode=mode, field=field)

    table = Table(title=f"Products matching {term!r} in {field.value}", box=None)
    table.add_column("SKU")
    table.add_column("Product")
    table.add_column("Matched in")
    table.add_column("Matches", justify="right")
    table.add_column("Context", overflow="fold")

    for hit in hits[:limit]:
        table.add_row(
            hit.product.sku,
            hit.product.name,
            ", ".join(f.value for f in hit.fields),
            str(hit.matches),
            hit.primary.context[:80] if hit.primary else "",
        )

    console.print(table)
    if len(hits) > limit:
        console.print(f"[dim]… {len(hits) - limit} more[/dim]")
    console.print(f"[dim]{len(hits)} products matched · adapter={catalog.name}[/dim]")


@app.command()
def plan(
    term: str,
    replacement: str,
    mode: ModeOption = MatchMode.LITERAL,
    show: int = 10,
) -> None:
    """Show exactly what an edit would change. Writes nothing."""
    catalog = build_catalog()
    result = build_plan(catalog, term, replacement, match_mode=mode)

    table = Table(title=f"Plan {result.id}", box=None)
    table.add_column("SKU")
    table.add_column("Product")
    table.add_column("Edits", justify="right")
    table.add_column("Before → after", overflow="fold")
    for change in result.changes[:show]:
        before, after = change.fragments[0] if change.fragments else ("", "")
        table.add_row(
            change.sku,
            change.product_name,
            str(change.replacements),
            f"{before[:44]} → {after[:44]}",
        )
    console.print(table)

    if result.product_count > show:
        console.print(f"[dim]… {result.product_count - show} more products[/dim]")
    console.print(
        f"[bold]{result.product_count}[/bold] products, "
        f"[bold]{result.total_replacements}[/bold] replacements. Nothing has been written."
    )
    if result.matched_but_unchanged:
        console.print(
            f"[yellow]{len(result.matched_but_unchanged)} products matched the raw markup "
            f"but no text node — the term appears only inside a tag or attribute.[/yellow]"
        )


@app.command()
def apply(
    term: str,
    replacement: str,
    mode: ModeOption = MatchMode.LITERAL,
    yes: Annotated[bool, typer.Option("--yes", help="Required to write anything.")] = False,
    max_products: int | None = None,
) -> None:
    """Perform an edit. Refuses to run without --yes."""
    settings = get_settings()
    catalog = build_catalog(settings)
    result = build_plan(catalog, term, replacement, match_mode=mode)

    console.print(
        f"Plan [bold]{result.id}[/bold]: {result.product_count} products, "
        f"{result.total_replacements} replacements."
    )
    if result.product_count == 0:
        console.print("[dim]Nothing to do.[/dim]")
        raise typer.Exit(0)

    if not yes:
        # A bulk catalogue edit is not something to do by accident.
        console.print("[yellow]Refusing to write without --yes. Run `scout plan` first.[/yellow]")
        raise typer.Exit(1)

    report = apply_plan(
        catalog,
        result,
        audit=AuditLog(settings.audit_path),
        rollback_dir=settings.rollback_dir,
        rate_per_second=settings.rate_per_second,
        max_products=max_products or settings.max_products_per_plan,
    )
    console.print(f"Updated [green]{len(report.updated)}[/green] products.")
    if report.failed:
        console.print(f"[red]{len(report.failed)} failed:[/red]")
        for sku, error in report.failed[:10]:
            console.print(f"  {sku}: {error}")
    if report.rollback_path:
        console.print(f"Rollback: [bold]scout rollback {report.rollback_path}[/bold]")


@app.command()
def rollback(path: Path) -> None:
    """Restore every product recorded in a rollback file."""
    settings = get_settings()
    report = run_rollback(build_catalog(settings), AuditLog(settings.audit_path), path)
    console.print(f"Restored [green]{len(report.updated)}[/green] products.")
    for sku, error in report.failed[:10]:
        console.print(f"  [red]{sku}[/red]: {error}")


@app.command()
def config() -> None:
    """Print the resolved configuration, with the token masked."""
    settings = get_settings()
    data = settings.model_dump()
    if data.get("magento_api_token"):
        data["magento_api_token"] = "***"  # noqa: S105 - a mask, not a credential
    for key, value in sorted(data.items()):
        console.print(f"  {key:26} {value}")


if __name__ == "__main__":  # pragma: no cover
    app()
