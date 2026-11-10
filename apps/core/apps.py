from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    label = 'core'
    verbose_name = 'Perfil'

    def ready(self):
        from django.conf import settings
        if getattr(settings, 'AUTH_LDAP_GROUP_DN_MAP', None) is not None:
            from . import ldap_signals  # noqa: F401
