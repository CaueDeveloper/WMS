from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from apps.logistica.models import Endereco


class MapaEstoqueTests(TestCase):
    def setUp(self):
        for andar in (1, 2):
            for corredor in 'AB':
                for pos in range(1, 4):
                    Endereco.objects.create(
                        corredor=corredor, andar=andar, posicao=pos,
                        codigo=Endereco.montar_codigo(corredor, andar, pos),
                    )
        Endereco.objects.filter(codigo=Endereco.montar_codigo('B', 2, 3)).update(ativo=False)
        u = User.objects.create_user('adm', password='x')
        u.groups.add(Group.objects.get_or_create(name='Administradores')[0])
        self.client.force_login(u)

    def test_visoes_modos_e_filtros(self):
        url = reverse('logistica:mapa_estoque')
        for modo in ('simples', 'complexo'):
            for visao in ('ocupacao', 'andares', 'corredores', 'guardas', 'geral'):
                for extra in ({}, {'andar': 2, 'corredor': 'B', 'guarda': 3, 'nivel': 'inativo'}, {'nivel': 'ocupado'}):
                    r = self.client.get(url, {'visao': visao, 'modo': modo, **extra}, secure=True)
                    self.assertEqual(r.status_code, 200, (visao, modo, extra))
        r = self.client.get(url, {'visao': 'geral', 'modo': 'simples', 'andar': 1}, secure=True)
        self.assertContains(r, '001-02-A-01-01')
        self.assertNotContains(r, '001-02-A-02-01')
        r = self.client.get(url, {'visao': 'corredores', 'corredor': 'A'}, secure=True)
        self.assertContains(r, 'Corredor A')
        self.assertNotContains(r, 'Corredor B')
        r = self.client.get(url, {'visao': 'bogus', 'modo': 'x', 'andar': '99'}, secure=True)
        self.assertEqual(r.context['visao'], 'ocupacao')
        self.assertEqual(r.context['modo'], 'simples')
        self.assertIsNone(r.context['filtros']['andar'])
        r = self.client.get(url, {'visao': 'guardas', 'nivel': 'inativo'}, secure=True)
        self.assertEqual(r.context['resumo']['total'], 1)


class MapaEstoque3DTests(TestCase):
    def setUp(self):
        for andar in (1, 2):
            for corredor in 'AB':
                for pos in range(1, 4):
                    Endereco.objects.create(
                        corredor=corredor, andar=andar, posicao=pos,
                        codigo=Endereco.montar_codigo(corredor, andar, pos),
                    )
        Endereco.objects.filter(codigo=Endereco.montar_codigo('B', 2, 3)).update(ativo=False)

    def _login(self, grupo):
        u = User.objects.create_user(grupo.replace(' ', ''), password='x')
        u.groups.add(Group.objects.get_or_create(name=grupo)[0])
        self.client.force_login(u)

    def test_visao_3d_renderiza(self):
        self._login('Administradores')
        r = self.client.get(reverse('logistica:mapa_estoque'), {'visao': '3d'}, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="mapa3d"')
        self.assertContains(r, 'three.module.min')

    def test_dados_json(self):
        self._login('Gestor Logística')
        url = reverse('logistica:mapa_estoque_dados')
        with self.assertNumQueries(6):  # sessão, usuário, grupo (acesso), grupo (inspecionar), endereços (com ocupação) e paletes
            r = self.client.get(url, secure=True)
        self.assertEqual(r.status_code, 200)
        dados = r.json()
        self.assertEqual(len(dados['enderecos']), 12)
        estados = {e['c']: e['s'] for e in dados['enderecos']}
        self.assertEqual(estados[Endereco.montar_codigo('B', 2, 3)], 'inativo')
        self.assertEqual(estados[Endereco.montar_codigo('A', 1, 1)], 'livre')
        r = self.client.get(url, {'andar': 1, 'corredor': 'A'}, secure=True)
        self.assertEqual(len(r.json()['enderecos']), 3)

    def test_dados_exige_perfil(self):
        u = User.objects.create_user('sem_perfil', password='x')
        self.client.force_login(u)
        r = self.client.get(reverse('logistica:mapa_estoque_dados'), secure=True)
        self.assertNotEqual(r.status_code, 200)

    def test_operador_ve_dados_do_corredor_com_link_de_consulta(self):
        from apps.producao.models import Palete

        Palete.objects.create(
            barcode_palete='PLT-AAAAAAAAAAAA', sku='SKU1', descricao='Produto', lote='L1', peso_kg=10,
            estado='ARMAZENADO', endereco=Endereco.objects.get(corredor='A', andar=1, posicao=1),
        )
        self._login('Operador Logística')
        r = self.client.get(reverse('logistica:mapa_estoque_dados'), {'corredor': 'A'}, secure=True)
        self.assertEqual(r.status_code, 200)
        com_palete = [e for e in r.json()['enderecos'] if 'pl' in e]
        self.assertEqual(len(com_palete), 1)
        self.assertIn('modo=palete', com_palete[0]['pl']['url'])


class ConsultarPaleteTests(TestCase):
    def setUp(self):
        from datetime import date

        from apps.producao.models import Palete, registrar_historico
        u = User.objects.create_user('consulta', password='x', is_superuser=True)
        self.client.force_login(u)
        self.url = reverse('logistica:consultar_palete')
        self.p = Palete.objects.create(
            sku='A', descricao='x', lote='L1', peso_kg=1, data_fabricacao=date(2026, 1, 5),
        )
        registrar_historico([self.p], 'FABRICADO', usuario=u)

    def test_form_de_busca_preserva_modo(self):
        # o form é GET: sem o campo oculto, a busca perdia ?modo=palete e voltava ao menu
        r = self.client.get(self.url, {'modo': 'palete'}, secure=True)
        self.assertContains(r, 'name="modo" value="palete"')

    def test_busca_mostra_palete_e_historico(self):
        r = self.client.get(self.url, {'modo': 'palete', 'barcode': self.p.barcode_palete}, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Histórico')
        self.assertContains(r, '05/01/2026')
        self.assertEqual(r.context['palete'], self.p)

    def test_busca_por_lote_sem_campo_de_data(self):
        r = self.client.get(self.url, {'modo': 'lote', 'lote': 'L1'}, secure=True)
        self.assertEqual(len(r.context['resultados']), 1)
        self.assertNotContains(r, 'id_data_fabricacao')


class EtiquetaCorredorTests(TestCase):
    def setUp(self):
        self.adm = User.objects.create_user('adm', password='x')
        self.adm.groups.add(Group.objects.get_or_create(name='Administradores')[0])
        self.client.force_login(self.adm)

    def test_zpl_tem_letra_e_codigo_sem_placeholders(self):
        from apps.logistica.zpl import montar_zpl_etiqueta_corredor

        url = 'https://wms.local/estoque/mapa/?visao=corredores&corredor=B'
        zpl = montar_zpl_etiqueta_corredor('B', url)
        self.assertIn('^FDB^FS', zpl)
        self.assertIn('^FD001-02-B^FS', zpl)
        self.assertIn(f'^FDLA,{url}^FS', zpl)
        self.assertNotIn('[[', zpl)

    def test_preview_png(self):
        r = self.client.get(reverse('logistica:etiqueta_corredor_preview', args=['T']), secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/png')
        r = self.client.get(reverse('logistica:etiqueta_corredor_preview', args=['Z']), secure=True)
        self.assertEqual(r.status_code, 404)

    def test_tela_lista_corredores(self):
        r = self.client.get(reverse('logistica:etiquetas_corredor'), {'corredor': 'C'}, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['corredor'], 'C')

    def test_imprime_corredor_e_todos(self):
        url = reverse('logistica:etiquetas_corredor')
        with mock.patch('apps.logistica.views.disparar_impressao_corredores_async') as imprimir, \
                self.settings(IMPRESSORA_ZPL_HOST='10.0.0.1', IMPRESSORA_ZPL_PORT=9100):
            self.client.post(url, {'corredor': 'd'}, secure=True)
            (enviados,), _ = imprimir.call_args
            self.assertEqual([c for c, _ in enviados], ['D'])
            self.assertTrue(enviados[0][1].endswith('/estoque/consultar/?modo=corredor&corredor=D'))
            imprimir.reset_mock()
            self.client.post(url, {'corredor': 'TODOS'}, secure=True)
            (enviados,), _ = imprimir.call_args
            self.assertEqual([c for c, _ in enviados], list(Endereco.CORREDORES))
            imprimir.reset_mock()
            self.client.post(url, {'corredor': 'ZZ'}, secure=True)
            imprimir.assert_not_called()

    def test_nao_administrador_nao_acessa(self):
        op = User.objects.create_user('op', password='x')
        op.groups.add(Group.objects.get_or_create(name='Operador Logística')[0])
        self.client.force_login(op)
        r = self.client.get(reverse('logistica:etiquetas_corredor'), secure=True)
        self.assertNotEqual(r.status_code, 200)


class ConsultaCorredorTests(TestCase):
    def setUp(self):
        from apps.producao.models import Palete

        for corredor in 'AB':
            for pos in (1, 2):
                Endereco.objects.create(
                    corredor=corredor, andar=1, posicao=pos,
                    codigo=Endereco.montar_codigo(corredor, 1, pos),
                )
        for corredor, barcode in (('A', 'PLT-AAAAAAAAAAAA'), ('B', 'PLT-BBBBBBBBBBBB')):
            Palete.objects.create(
                barcode_palete=barcode, sku='SKU1', descricao='Produto', lote='L1', peso_kg=10,
                estado='ARMAZENADO', endereco=Endereco.objects.get(corredor=corredor, posicao=1),
            )
        op = User.objects.create_user('op', password='x')
        op.groups.add(Group.objects.get_or_create(name='Operador Logística')[0])
        self.client.force_login(op)
        self.url = reverse('logistica:consultar_palete')

    def test_interpreta_o_que_foi_bipado(self):
        from apps.logistica.views import _corredor_do_codigo

        self.assertEqual(_corredor_do_codigo('https://wms/estoque/consultar/?modo=corredor&corredor=C'), 'C')
        self.assertEqual(_corredor_do_codigo('001-02-D'), 'D')
        self.assertEqual(_corredor_do_codigo(' e '), 'E')
        for lixo in ('', 'xyz', '001-02-A-01-01', 'https://wms/?corredor=Z', None):
            self.assertIsNone(_corredor_do_codigo(lixo), lixo)

    def test_detalhe_do_corredor_bipado(self):
        r = self.client.get(self.url, {'modo': 'corredor', 'corredor': '001-02-B'}, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['corredor'], 'B')
        self.assertContains(r, 'PLT-BBBBBBBBBBBB')
        self.assertNotContains(r, 'PLT-AAAAAAAAAAAA')
        self.assertEqual(r.context['resumo']['ocupados'], 1)

    def test_corredor_mostra_mapa_3d_filtrado_e_leitor_de_qr(self):
        r = self.client.get(self.url, {'modo': 'corredor', 'corredor': 'B'}, secure=True)
        self.assertContains(r, 'id="mapa3d"')
        self.assertContains(r, 'data-query="?corredor=B"')
        self.assertContains(r, 'data-leitor-qr="id_corredor"')
        self.assertContains(r, 'core/js/leitor_qr.')
        self.assertNotContains(r, 'visao=andares')
        r = self.client.get(self.url, {'modo': 'corredor'}, secure=True)
        self.assertNotContains(r, 'id="mapa3d"')
        self.assertContains(r, 'data-leitor-qr="id_corredor"')

    def test_url_do_qr_abre_o_corredor(self):
        qr = 'https://wms/estoque/consultar/?modo=corredor&corredor=A'
        r = self.client.get(self.url, {'modo': 'corredor', 'corredor': qr}, secure=True)
        self.assertEqual(r.context['corredor'], 'A')
        self.assertContains(r, 'PLT-AAAAAAAAAAAA')

    def test_codigo_invalido_nao_quebra(self):
        r = self.client.get(self.url, {'modo': 'corredor', 'corredor': 'lixo'}, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['corredor'])
        r = self.client.get(self.url, {'modo': 'corredor'}, secure=True)
        self.assertEqual(r.status_code, 200)
