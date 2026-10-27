from django.conf import settings
from django.middleware import locale
from django.utils import translation


class LocaleMiddleware(locale.LocaleMiddleware):
    """Pick the language from the language cookie only, falling back to LANGUAGE_CODE.

    Django's LocaleMiddleware also honours the browser's Accept-Language header,
    which would show English to anyone with an English browser. Here the default
    is always settings.LANGUAGE_CODE until the user chooses otherwise with the
    header switcher (Django's set_language view, which sets the cookie).
    """

    def process_request(self, request):
        language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if language not in dict(settings.LANGUAGES):
            language = settings.LANGUAGE_CODE
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()
