from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from apps.recebimento.models import Palete


class TelaInicialETests(TestCase):
    def _login(self, grupo=None, **kw):
        u = User.objects.create_user('u%d' % (User.objects.count() + 1), password='x', **kw)
        if grupo:
            u.groups.add(Group.objects.get_or_create(name=grupo)[0])
        self.client.force_login(u)

    def test_home_por_perfil(self):
        Palete.objects.create(sku='A', descricao='x', lote='L1', peso_kg=1, estado='CONFERIDO_LOGISTICA')
        Palete.objects.create(sku='A', descricao='x', lote='L1', peso_kg=1, estado='CONFERIDO')
        self._login('Operador Logística')
        r = self.client.get(reverse('home'), secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Armazenagem')
        self.assertNotContains(r, 'Usuários')
        self.assertNotContains(r, 'Mapa de Estoque')
        etapas = {e['rotulo']: e for e in r.context['etapas']}
        self.assertEqual(etapas['A armazenar']['quantidade'], 1)
        self.assertEqual(etapas['Na Logística']['quantidade'], 1)
        self.assertIsNotNone(etapas['A armazenar']['url'])
        self.assertIsNone(etapas['Armazenados']['url'])

    def test_sem_perfil_e_admin_e_paginas(self):
        self._login()
        r = self.client.get(reverse('home'), secure=True)
        self.assertContains(r, 'ainda não tem um perfil')
        self.client.logout()
        self._login(None, is_superuser=True)
        r = self.client.get(reverse('home'), secure=True)
        self.assertContains(r, 'Usuários')
        for nome in ('estoque:mapa_estoque', 'estoque:putaway', 'estoque:plano_armazenagem', 'estatisticas', 'usuarios',
                     'recebimento:novo', 'recebimento:transferencia_gestor', 'estoque:consultar_palete'):
            self.assertEqual(self.client.get(reverse(nome), secure=True).status_code, 200, nome)
        r = self.client.get(reverse('estoque:consultar_palete') + '?modo=lote', secure=True)
        ativos = [i['rotulo'] for s in r.context['menu_secoes'] for i in s['itens'] if i['ativo']]
        self.assertEqual(ativos, ['Consultar lote'])


class AvisosEPaginasTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('adm', password='x', is_superuser=True)
        self.client.force_login(self.admin)

    def test_aviso_aparece_uma_unica_vez(self):
        r = self.client.post(
            reverse('recebimento:transferencia_gestor'), {'barcode_palete': 'NAOEXISTE'}, secure=True, follow=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'role="alert"', count=1)

    def test_inspecionar_palete_mostra_linha_do_tempo(self):
        from apps.recebimento.models import PaleteHistorico

        palete = Palete.objects.create(sku='A', descricao='Café', lote='L1', peso_kg=1)
        PaleteHistorico.objects.create(palete=palete, estado='CRIADO', usuario=self.admin)
        PaleteHistorico.objects.create(palete=palete, estado='EM_TRANSFERENCIA', usuario=self.admin)
        r = self.client.get(reverse('estoque:inspecionar_palete', args=[palete.pk]), secure=True)
        self.assertContains(r, 'Do início até agora')
        self.assertContains(r, palete.barcode_palete)
        self.assertContains(r, 'last:pb-0', count=2)  # um item da linha do tempo por evento

    def test_consulta_por_lote_com_paginas(self):
        for n in range(25):
            Palete.objects.create(sku='A', descricao='Café', lote='L1', peso_kg=1)
        r = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'lote', 'lote': 'L1'}, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Página 1 de 2')
        self.assertContains(r, 'pg=2')


class TransferenciaTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('adm2', password='x', is_superuser=True))

    def test_lista_de_lotes_em_uma_unica_marcacao(self):
        for estado in ('CRIADO', 'EM_TRANSFERENCIA'):
            Palete.objects.create(sku='A', descricao='Café 500g', lote='L77', peso_kg=1, estado=estado)
        r = self.client.get(reverse('recebimento:transferencia_gestor'), secure=True)
        self.assertEqual(r.status_code, 200)
        # uma única marcação (sem tabela desktop + cartões): uma linha por palete + o rodapé do lote
        self.assertContains(r, 'data-label="Lote"', count=2)
        self.assertContains(r, '<span class="badge-success">Confirmado</span>', count=1)
        self.assertContains(r, '<span class="badge-warning">Pendente</span>', count=1)
        self.assertContains(r, '<option value="reprovar">Reprovar</option>', count=2)
        self.assertNotContains(r, '<option value="aprovar">')  # a aprovação do palete é a bipagem
        self.assertContains(r, 'disabled')  # lote incompleto: aprovar fica desabilitado

    def test_logistica_usa_o_mesmo_modelo(self):
        Palete.objects.create(sku='A', descricao='Café', lote='L88', peso_kg=1, estado='EM_CONFERENCIA_LOGISTICA')
        r = self.client.get(reverse('recebimento:transferencia_logistica'), secure=True)
        self.assertContains(r, 'Transferência: Logística')
        self.assertContains(r, 'L88')

    def test_zoom_liberado(self):
        r = self.client.get(reverse('home'), secure=True)
        self.assertNotContains(r, 'user-scalable=no')


class FormatarPesoTests(TestCase):
    def test_padrao_brasileiro(self):
        from decimal import Decimal

        from apps.core.templatetags.core_extras import formatar_peso
        self.assertEqual(formatar_peso(Decimal('500')), '500,00')
        self.assertEqual(formatar_peso(Decimal('1000.000')), '1.000,00')
        self.assertEqual(formatar_peso(Decimal('1234567.5')), '1.234.567,50')
        self.assertEqual(formatar_peso(Decimal('12.345')), '12,345')
        self.assertEqual(formatar_peso(None), '—')
