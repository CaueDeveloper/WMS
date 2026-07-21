from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def group_required(*group_names):
    """Restringe a view aos grupos informados (ou superusuário).

    Usuário anônimo é redirecionado para o login; usuário autenticado
    sem o grupo exigido recebe 403 (PermissionDenied), replicando o
    comportamento de `permission_required(..., raise_exception=True)`
    mas com checagem por `auth.Group` em vez de permissão individual.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if user.is_superuser or user.groups.filter(name__in=group_names).exists():
                return view_func(request, *args, **kwargs)
            raise PermissionDenied
        return _wrapped
    return decorator
