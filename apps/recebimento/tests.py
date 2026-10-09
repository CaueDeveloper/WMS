from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.estoque.models import Produto

from .models import Palete
from .services import LoteIncompletoError, PaleteStateError, aprovar_lote, corrigir_palete, reprovar_palete


def _usuario(grupo):
    u = User.objects.create_user(f'u_{grupo}'.replace(' ', '_'), password='x')
    u.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return u


class ReceberPaleteTests(TestCase):
    def setUp(self):
        self.produto = Produto.objects.create(sku='CAFE', descricao='Café 500g')
        self.client.force_login(_usuario('Operador Produção'))
        self.url = reverse('recebimento:novo')
        self.hoje = timezone.localdate()

    def _post(self, **extra):
        dados = {'produto': self.produto.pk, 'lote': 'l1', 'quantidade': 2,
                 'data_fabricacao': self.hoje.isoformat()}
        dados.update(extra)
        with mock.patch('apps.recebimento.views.disparar_impressao_async'):
            return self.client.post(self.url, dados, secure=True)

    def test_data_fabricacao_obrigatoria(self):
        r = self._post(data_fabricacao='')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Palete.objects.count(), 0)

    def test_data_fabricacao_futura_rejeitada(self):
        r = self._post(data_fabricacao=(self.hoje + timedelta(days=1)).isoformat())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Palete.objects.count(), 0)

    def test_peso_padrao_500_e_data_gravada(self):
        r = self._post()
        self.assertEqual(r.status_code, 302)
        paletes = Palete.objects.all()
        self.assertEqual(paletes.count(), 2)
        for p in paletes:
            self.assertEqual(p.peso_kg, Decimal('500'))
            self.assertEqual(p.data_fabricacao, self.hoje)

    def test_peso_manual_exige_valor(self):
        self.assertEqual(self._post(peso_variavel='on').status_code, 200)
        self.assertEqual(Palete.objects.count(), 0)
        self.assertEqual(self._post(peso_variavel='on', peso_kg='123.5').status_code, 302)
        self.assertEqual(Palete.objects.first().peso_kg, Decimal('123.5'))

    def test_sucesso_usa_tela_cheia(self):
        r = self._post()
        r = self.client.get(self.url, secure=True)
        self.assertContains(r, 'id="sucesso-tela"')
        self.assertContains(r, 'dlg-confirmar')


class ReprovacaoTests(TestCase):
    def setUp(self):
        self.produto = Produto.objects.create(sku='CAFE', descricao='Café 500g')
        self.p = Palete.objects.create(sku='X', descricao='x', lote='L1', peso_kg=1, estado='EM_TRANSFERENCIA')

    def test_reprovar_e_corrigir(self):
        op = _usuario('Operador Produção')
        reprovar_palete(self.p.barcode_palete, 'PRODUCAO', usuario=op, motivo='peso errado')
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'REPROVADO')
        self.assertEqual(self.p.motivo_reprovacao, 'peso errado')
        self.assertTrue(self.p.historico.filter(estado='REPROVADO').exists())

        corrigir_palete(self.p.pk, self.produto, Decimal('480'), timezone.localdate(), usuario=op)
        self.p.refresh_from_db()
        self.assertEqual((self.p.estado, self.p.sku, self.p.peso_kg), ('CRIADO', 'CAFE', Decimal('480')))

    def test_estado_invalido_nao_reprova(self):
        self.p.estado = 'CONFERIDO'
        self.p.save()
        with self.assertRaises(PaleteStateError):
            reprovar_palete(self.p.barcode_palete, 'PRODUCAO')
        reprovar_palete(self.p.barcode_palete, 'LOGISTICA')  # logística aceita CONFERIDO

    def test_lote_com_reprovado_nao_aprova(self):
        Palete.objects.create(sku='X', descricao='x', lote='L1', peso_kg=1, estado='EM_TRANSFERENCIA')
        reprovar_palete(self.p.barcode_palete, 'PRODUCAO')
        with self.assertRaises(LoteIncompletoError):
            aprovar_lote('L1')

    def test_views_e_permissoes(self):
        self.client.force_login(_usuario('Operador Produção'))
        r = self.client.post(reverse('recebimento:transferencia_gestor'),
                             {'reprovar_palete': self.p.barcode_palete, 'motivo': 'x'}, secure=True)
        self.assertEqual(r.status_code, 302)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'REPROVADO')
        url = reverse('recebimento:transferencia_gestor')
        dados = {'corrigir_palete': self.p.pk, 'produto': self.produto.pk, 'peso_kg': '500'}
        # Operador não corrige; o reprovado aparece no lote do Gestor Produção, que corrige
        self.client.post(url, dados, secure=True)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'REPROVADO')
        self.client.force_login(_usuario('Gestor Produção'))
        r = self.client.get(url, secure=True)
        self.assertContains(r, self.p.barcode_palete)
        self.client.post(url, dados, secure=True)
        self.p.refresh_from_db()
        self.assertEqual((self.p.estado, self.p.sku, self.p.peso_kg), ('CRIADO', 'CAFE', Decimal('500')))

    def test_gestor_reprova_pela_planilha(self):
        self.client.force_login(_usuario('Gestor Produção'))
        r = self.client.post(reverse('recebimento:transferencia_gestor'),
                             {'reprovar_palete': self.p.barcode_palete}, secure=True)
        self.assertIn(f'flash=palete_erro:{self.p.barcode_palete}', r['Location'])
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'REPROVADO')


class TransferenciaConfirmacaoTests(TestCase):
    def test_bipagem_redireciona_com_animacao_na_linha_e_confirmacao(self):
        p = Palete.objects.create(sku='X', descricao='x', lote='L1', peso_kg=1, estado='CRIADO')
        self.client.force_login(User.objects.create_user('adm', password='x', is_superuser=True))
        url = reverse('recebimento:transferencia_gestor')
        r = self.client.post(url, {'barcode_palete': p.barcode_palete}, secure=True)
        self.assertEqual(r['Location'], f'{url}?flash=palete_ok:{p.barcode_palete}')
        r = self.client.get(url, secure=True)
        self.assertNotContains(r, 'id="sucesso-tela"')
        self.assertContains(r, 'data-linha-palete')
        self.assertContains(r, 'dlg-confirmar-lote')
        self.assertContains(r, 'data-confirmar-lote')
        r = self.client.post(url, {'aprovar_lote': 'L1'}, secure=True)
        self.assertIn('flash=lote_ok:L1', r['Location'])
        r = self.client.get(url, secure=True)
        self.assertNotContains(r, 'id="sucesso-tela"')
