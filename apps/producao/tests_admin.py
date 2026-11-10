from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.logistica.models import Endereco

from .models import Palete


class PaleteAdminCriacaoTests(TestCase):
    def setUp(self):
        self.url = reverse('admin:producao_palete_add')
        self.dados = {
            'barcode_palete': 'PLT-ADMIN0000001', 'sku': 'CAFE', 'descricao': 'Café 1KG', 'lote': 'L1',
            'peso_kg': '500', 'estado': 'FABRICADO',
        }

    def _login(self, **kw):
        self.client.force_login(User.objects.create_user('u', password='x', is_staff=True, **kw))

    def test_superusuario_cria_palete_com_historico(self):
        self._login(is_superuser=True)
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 200)
        self.client.post(self.url, self.dados, secure=True)
        p = Palete.objects.get()
        self.assertEqual((p.estado, p.peso_kg), ('FABRICADO', 500))
        h = p.historico.get()
        self.assertEqual((h.estado, h.usuario.username), ('FABRICADO', 'u'))

    def test_staff_comum_nao_cria(self):
        self._login()
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 403)
        self.client.post(self.url, self.dados, secure=True)
        self.assertFalse(Palete.objects.exists())

    def test_estado_e_endereco_precisam_combinar(self):
        self._login(is_superuser=True)
        e = Endereco.objects.create(corredor='A', andar=1, posicao=1, codigo=Endereco.montar_codigo('A', 1, 1))
        self.client.post(self.url, {**self.dados, 'estado': 'ARMAZENADO'}, secure=True)
        self.client.post(self.url, {**self.dados, 'endereco': e.pk}, secure=True)
        self.assertFalse(Palete.objects.exists())
        self.client.post(self.url, {**self.dados, 'estado': 'ARMAZENADO', 'endereco': e.pk}, secure=True)
        self.assertEqual(Palete.objects.get().endereco, e)

    def test_edicao_continua_bloqueada(self):
        self._login(is_superuser=True)
        p = Palete.objects.create(sku='X', descricao='x', lote='L1', peso_kg=1)
        r = self.client.post(reverse('admin:producao_palete_change', args=[p.pk]), {'sku': 'Y'}, secure=True)
        self.assertEqual(r.status_code, 403)
        p.refresh_from_db()
        self.assertEqual(p.sku, 'X')
