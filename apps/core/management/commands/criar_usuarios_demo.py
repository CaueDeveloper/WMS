from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

USUARIOS_DEMO = [
    ('gestor', 'Gestor@123', 'Gestores'),
    ('operador', 'Operador@123', 'Operadores'),
]


class Command(BaseCommand):
    help = (
        'Cria (ou reseta a senha de) usuários de demonstração para acessar o sistema: '
        '"gestor" (grupo Gestores) e "operador" (grupo Operadores).'
    )

    def handle(self, *args, **options):
        for username, password, grupo_nome in USUARIOS_DEMO:
            user, criado = User.objects.get_or_create(username=username)
            user.set_password(password)
            user.save()
            user.groups.set([Group.objects.get(name=grupo_nome)])

            acao = 'criado' if criado else 'já existia — senha redefinida'
            self.stdout.write(self.style.SUCCESS(
                f'Usuário "{username}" {acao} (senha: {password}, grupo: {grupo_nome}).'
            ))
