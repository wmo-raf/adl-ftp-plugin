from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """
    Get an item from a dictionary.

    Usage in template:
    {{ my_dict|get_item:"key_name" }}
    """
    if dictionary is None:
        return None
    return dictionary.get(key)
