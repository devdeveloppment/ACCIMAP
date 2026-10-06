from django import template

from accounts.phone import mask_phone as _mask_phone

register = template.Library()


@register.filter
def mask_phone(value):
    """{{ user.phone_number|mask_phone }} -> +228****56"""
    return _mask_phone(value)
