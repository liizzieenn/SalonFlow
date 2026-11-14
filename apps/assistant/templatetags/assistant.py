from django import template

from apps.salons.models import Salon

from ..views import get_history

register = template.Library()


@register.inclusion_tag("assistant/widget.html", takes_context=True)
def assistant_widget(context, salon: Salon) -> dict:
    """The chat widget, pre-filled with this visitor's conversation (session)."""
    request = context["request"]
    return {
        "salon": salon,
        "messages_list": get_history(request, salon),
        "csrf_token": context.get("csrf_token"),
        "request": request,
    }
