from django.core.management.base import BaseCommand
from django.db import connection, transaction

RENOMEACOES = (('recebimento', 'producao'), ('estoque', 'logistica'))


class Command(BaseCommand):
    help = (
        'Renomeia, num banco já existente, as tabelas e o histórico de migrations dos apps '
        'recebimento→producao e estoque→logistica. Rodar UMA vez, antes do "migrate" '
        'e depois de fazer backup do banco. É idempotente.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Só mostra o que seria feito.')

    def handle(self, *args, **opcoes):
        simular = opcoes['dry_run']
        tabelas = set(connection.introspection.table_names())
        existentes = set(tabelas)
        renomear = []
        for antigo, novo in RENOMEACOES:
            for tabela in sorted(t for t in tabelas if t.startswith(f'{antigo}_')):
                destino = f'{novo}_{tabela[len(antigo) + 1:]}'
                if destino in existentes:
                    self.stderr.write(f'Abortado: {destino} já existe (conflito com {tabela}).')
                    return
                renomear.append((tabela, destino))

        for tabela, destino in renomear:
            self.stdout.write(f'tabela {tabela} -> {destino}')
        if simular:
            self.stdout.write('Simulação: nada foi alterado.')
            return

        with connection.schema_editor() as editor:
            for tabela, destino in renomear:
                editor.alter_db_table(None, tabela, destino)

        with transaction.atomic(), connection.cursor() as cursor:
            for antigo, novo in RENOMEACOES:
                for tabela in ('django_migrations', 'django_content_type'):
                    if tabela not in tabelas:
                        continue
                    coluna = 'app' if tabela == 'django_migrations' else 'app_label'
                    cursor.execute(
                        f'UPDATE {tabela} SET {coluna} = %s WHERE {coluna} = %s', [novo, antigo]
                    )
                    self.stdout.write(f'{tabela}: {cursor.rowcount} linha(s) {antigo} -> {novo}')
        self.stdout.write(self.style.SUCCESS('Pronto. Agora rode "python manage.py migrate".'))
