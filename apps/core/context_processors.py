from .navegacao import grupos_do_usuario, menu_para


def menu(request):
    """Disponibiliza o menu lateral e o perfil do usuário ao shell em toda página."""
    if not request.user.is_authenticated:
        return {}
    nomes = sorted(grupos_do_usuario(request.user))
    nome = request.user.get_full_name() or request.user.get_username()
    return {
        'menu_secoes': menu_para(request),
        'usuario_nome': nome,
        'usuario_iniciais': ''.join(parte[0] for parte in nome.split()[:2]).upper() or nome[:1].upper(),
        'usuario_perfil': ', '.join(nomes) if nomes else ('Superusuário' if request.user.is_superuser else 'Sem perfil'),
    }
