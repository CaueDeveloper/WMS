from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

USUARIOS_DEMO = [
    ('admin_demo', 'AdminDemo@123', 'Administradores'),
    ('operador_producao', 'OperadorProducao@123', 'Operador Produção'),
    ('operador_logistica', 'OperadorLogistica@123', 'Operador Logística'),
    ('gestor_producao', 'GestorProducao@123', 'Gestor Produção'),
    ('gestor_logistica', 'GestorLogistica@123', 'Gestor Logística'),
]


class Command(BaseCommand):
    help = (
        'Cria (ou reseta a senha de) usuários de demonstração para acessar o sistema, '
        'um por papel: "admin_demo", "operador_producao", "operador_logistica", '
        '"gestor_producao", "gestor_logistica".'
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
