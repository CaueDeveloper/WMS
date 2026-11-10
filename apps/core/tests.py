from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from apps.producao.models import Palete


class TelaInicialETests(TestCase):
    def _login(self, grupo=None, **kw):
        u = User.objects.create_user('u%d' % (User.objects.count() + 1), password='x', **kw)
        if grupo:
            u.groups.add(Group.objects.get_or_create(name=grupo)[0])
        self.client.force_login(u)

    def test_home_por_perfil(self):
        Palete.objects.create(sku='A', descricao='x', lote='L1', peso_kg=1, estado='APROVADO_LOGISTICA')
        Palete.objects.create(sku='A', descricao='x', lote='L1', peso_kg=1, estado='APROVADO_PRODUCAO')
        self._login('Operador Logística')
        r = self.client.get(reverse('home'), secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Armazenagem')
        self.assertNotContains(r, 'Usuários')
        self.assertNotContains(r, 'Mapa de Estoque')
        etapas = {e['rotulo']: e for e in r.context['etapas']}
        self.assertEqual(etapas['A armazenar']['quantidade'], 1)
        self.assertEqual(etapas['Transferência de Logística']['quantidade'], 1)
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
        for nome in ('logistica:mapa_estoque', 'logistica:putaway', 'estatisticas', 'usuarios',
                     'producao:novo', 'producao:transferencia_gestor', 'logistica:consultar_palete'):
            self.assertEqual(self.client.get(reverse(nome), secure=True).status_code, 200, nome)
        r = self.client.get(reverse('logistica:consultar_palete') + '?modo=lote', secure=True)
        ativos = [i['rotulo'] for s in r.context['menu_secoes'] for i in s['itens'] if i['ativo']]
        self.assertEqual(ativos, ['Consultar lote'])


class AvisosEPaginasTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('adm', password='x', is_superuser=True)
        self.client.force_login(self.admin)

    def test_aviso_aparece_uma_unica_vez(self):
        r = self.client.post(
            reverse('producao:transferencia_gestor'), {'barcode_palete': 'NAOEXISTE'}, secure=True, follow=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'role="alert"', count=1)

    def test_inspecionar_palete_mostra_linha_do_tempo(self):
        from apps.producao.models import PaleteHistorico

        palete = Palete.objects.create(sku='A', descricao='Café', lote='L1', peso_kg=1)
        PaleteHistorico.objects.create(palete=palete, estado='FABRICADO', usuario=self.admin)
        PaleteHistorico.objects.create(palete=palete, estado='TRANSFERENCIA_PRODUCAO', usuario=self.admin)
        r = self.client.get(reverse('logistica:inspecionar_palete', args=[palete.pk]), secure=True)
        self.assertContains(r, 'Do início até agora')
        self.assertContains(r, palete.barcode_palete)
        self.assertContains(r, 'last:pb-0', count=2)  # um item da linha do tempo por evento

    def test_consulta_por_lote_com_paginas(self):
        for n in range(25):
            Palete.objects.create(sku='A', descricao='Café', lote='L1', peso_kg=1)
        r = self.client.get(reverse('logistica:consultar_palete'), {'modo': 'lote', 'lote': 'L1'}, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Página 1 de 2')
        self.assertContains(r, 'pg=2')


class TransferenciaTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('adm2', password='x', is_superuser=True))

    def test_lista_de_lotes_em_uma_unica_marcacao(self):
        for estado in ('FABRICADO', 'TRANSFERENCIA_PRODUCAO'):
            Palete.objects.create(sku='A', descricao='Café 500g', lote='L77', peso_kg=1, estado=estado)
        r = self.client.get(reverse('producao:transferencia_gestor'), secure=True)
        self.assertEqual(r.status_code, 200)
        # uma única marcação (sem tabela desktop + cartões): uma linha por palete + o rodapé do lote
        self.assertContains(r, 'data-label="Lote"', count=2)
        self.assertContains(r, '<span class="badge-success">Confirmado</span>', count=1)
        self.assertContains(r, '<span class="badge-warning">Pendente</span>', count=1)
        self.assertContains(r, '<option value="reprovar">Reprovar</option>', count=2)
        self.assertNotContains(r, '<option value="aprovar">')  # a aprovação do palete é a bipagem
        self.assertContains(r, 'disabled')  # lote incompleto: aprovar fica desabilitado

    def test_logistica_usa_o_mesmo_modelo(self):
        Palete.objects.create(sku='A', descricao='Café', lote='L88', peso_kg=1, estado='TRANSFERENCIA_LOGISTICA')
        r = self.client.get(reverse('producao:transferencia_logistica'), secure=True)
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


class MenuEmGruposTests(TestCase):
    def _secoes(self, grupo, url=None):
        u = User.objects.create_user(f'm_{grupo}'.replace(' ', '_'), password='x')
        u.groups.add(Group.objects.get_or_create(name=grupo)[0])
        self.client.force_login(u)
        r = self.client.get(url or reverse('home'), secure=True)
        return {s['titulo']: s for s in r.context['menu_secoes']}, r

    def test_grupos_por_perfil(self):
        secoes, _ = self._secoes('Operador Produção')
        self.assertEqual([t for t in secoes if t], ['Produção', 'Consultar'])
        self.assertEqual([i['rotulo'] for i in secoes['Consultar']['itens']],
                         ['Consultar palete', 'Consultar lote', 'Consultar corredor'])
        secoes, _ = self._secoes('Gestor Logística')
        self.assertNotIn('Produção', secoes)
        rotulos = [i['rotulo'] for i in secoes['Logística']['itens']]
        for esperado in ('Romaneio', 'Armazenagem'):
            self.assertIn(esperado, rotulos)
        self.assertIn('Mapa de Estoque', [i['rotulo'] for i in secoes['Consultar']['itens']])
        self.assertNotIn('Usuários', secoes)
        self.assertEqual([i['rotulo'] for i in secoes['']['itens']], ['Início', 'Estatísticas'])

    def test_admin_ve_operadores_e_gestores_e_destaca_o_filtro_ativo(self):
        secoes, r = self._secoes('Administradores', reverse('usuarios') + '?papel=gestor')
        itens = secoes['Usuários']['itens']
        self.assertEqual([i['rotulo'] for i in itens], ['Operadores', 'Gestores'])
        self.assertEqual([i['rotulo'] for i in itens if i['ativo']], ['Gestores'])
        self.assertTrue(secoes['Usuários']['aberta'])

    def test_so_o_grupo_da_tela_atual_abre(self):
        secoes, r = self._secoes('Gestor Produção', reverse('producao:novo'))
        self.assertTrue(secoes['Produção']['aberta'])
        self.assertFalse(secoes['Consultar']['aberta'])
        self.assertContains(r, '<details class="menu-grupo" open>', count=1)
        self.assertContains(r, '<details class="menu-grupo">')

    def test_inicio_fica_solto_no_topo(self):
        secoes, _ = self._secoes('Gestor Logística')
        self.assertEqual(secoes[''] ['itens'][0]['rotulo'], 'Início')


class OperadorLocalTests(TestCase):
    def test_admin_cadastra_operador_com_senha_local_e_ele_loga(self):
        adm = User.objects.create_user('adm_op', password='x')
        adm.groups.add(Group.objects.get_or_create(name='Administradores')[0])
        self.client.force_login(adm)
        r = self.client.post(reverse('usuarios') + '?papel=operador', {
            'acao': 'novo_operador', 'nome': 'Maria Souza', 'username': 'maria', 'senha': 'segredo1', 'grupo': 'Logística',
        }, secure=True)
        self.assertRedirects(r, reverse('usuarios') + '?papel=operador', fetch_redirect_response=False)
        op = User.objects.get(username='maria')
        self.assertTrue(op.is_active)
        self.assertEqual(list(op.groups.values_list('name', flat=True)), ['Operador Logística'])
        self.client.logout()
        self.assertTrue(self.client.login(username='maria', password='segredo1'))
