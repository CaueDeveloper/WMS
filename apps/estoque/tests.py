from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from apps.estoque.models import Endereco


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
        url = reverse('estoque:mapa_estoque')
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


class ConsultarPaleteTests(TestCase):
    def setUp(self):
        from datetime import date

        from apps.recebimento.models import Palete, registrar_historico
        u = User.objects.create_user('consulta', password='x', is_superuser=True)
        self.client.force_login(u)
        self.url = reverse('estoque:consultar_palete')
        self.p = Palete.objects.create(
            sku='A', descricao='x', lote='L1', peso_kg=1, data_fabricacao=date(2026, 1, 5),
        )
        registrar_historico([self.p], 'CRIADO', usuario=u)

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

    def test_busca_por_data_de_fabricacao(self):
        r = self.client.get(self.url, {'modo': 'lote', 'data_fabricacao': '2026-01-05'}, secure=True)
        self.assertEqual(len(r.context['resultados']), 1)
        r = self.client.get(self.url, {'modo': 'lote', 'data_fabricacao': '2026-01-06'}, secure=True)
        self.assertIsNone(r.context['resultados'])
