from django import template

register = template.Library()


@register.simple_tag
def next_count(page_obj) -> int:
    """How many rows the next page adds: a full page, or what is left."""
    if not page_obj.has_next():
        return 0
    return min(page_obj.paginator.per_page, page_obj.paginator.count - page_obj.end_index())


@register.simple_tag
def shown_percent(page_obj) -> int:
    """How much of the list is on the screen after this page, 0–100 (for the progress bar)."""
    count = page_obj.paginator.count
    return round(100 * page_obj.end_index() / count) if count else 0
