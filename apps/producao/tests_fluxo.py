from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.logistica.models import Produto

from .models import Palete
from .services import LoteIncompletoError, aprovar_lote_logistica, corrigir_palete, reprovar_palete
from .views import _lotes_aprovados_na_data


def _usuario(grupo):
    u = User.objects.create_user(f'u_{grupo}'.replace(' ', '_'), password='x')
    u.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return u


def _palete(estado, lote='L1', **kw):
    return Palete.objects.create(sku='X', descricao='x', lote=lote, peso_kg=1, estado=estado, **kw)


class LoteEHistoricoTests(TestCase):
    def setUp(self):
        self.produto = Produto.objects.create(sku='CAFE', descricao='Café 500g')
        self.client.force_login(_usuario('Operador Produção'))
        self.hoje = timezone.localdate()

    def _post(self, lote):
        with mock.patch('apps.producao.views.disparar_impressao_async'):
            return self.client.post(reverse('producao:novo'), {
                'produto': self.produto.pk, 'lote': lote, 'quantidade': 2,
                'data_fabricacao': self.hoje.isoformat(),
                'data_vencimento': self.hoje.isoformat(),
            }, secure=True)

    def test_lote_com_caracteres_perigosos_rejeitado(self):
        for lote in ('A^FDx', 'A~B', 'A\\B', 'A&B', 'LOTE Ç', '[[PESO]]'):
            self.assertEqual(self._post(lote).status_code, 200, lote)
        self.assertEqual(Palete.objects.count(), 0)

    def test_emissao_grava_historico_para_cada_palete(self):
        self.assertEqual(self._post('l-1/a.2').status_code, 302)
        self.assertEqual(Palete.objects.filter(lote='L-1/A.2').count(), 2)
        for p in Palete.objects.all():
            self.assertEqual(list(p.historico.values_list('estado', flat=True)), ['FABRICADO'])

    def test_impressao_dispara_apos_commit(self):
        with mock.patch('apps.producao.views.disparar_impressao_async') as imprimir:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                self.client.post(reverse('producao:novo'), {
                    'produto': self.produto.pk, 'lote': 'L9', 'quantidade': 1,
                    'data_fabricacao': self.hoje.isoformat(),
                    'data_vencimento': self.hoje.isoformat(),
                }, secure=True)
            self.assertEqual(len(callbacks), 1)
            imprimir.assert_not_called()  # só dispara no commit
            callbacks[0]()
            imprimir.assert_called_once()


class BipagemTests(TestCase):
    def test_segunda_bipagem_e_recusada_sem_duplicar_historico(self):
        p = _palete('FABRICADO')
        self.client.force_login(_usuario('Operador Produção'))
        url = reverse('producao:transferencia_gestor')
        self.client.post(url, {'barcode_palete': p.barcode_palete}, secure=True)
        self.client.post(url, {'barcode_palete': p.barcode_palete}, secure=True)
        self.assertEqual(p.historico.filter(estado='TRANSFERENCIA_PRODUCAO').count(), 1)

    def test_correcao_limpa_dados_de_reprovacao(self):
        produto = Produto.objects.create(sku='CAFE', descricao='Café 500g')
        p = _palete('TRANSFERENCIA_PRODUCAO')
        reprovar_palete(p.barcode_palete, 'PRODUCAO', motivo='peso')
        corrigir_palete(p.pk, produto, Decimal('500'))
        p.refresh_from_db()
        self.assertEqual((p.motivo_reprovacao, p.reprovado_na_etapa, p.reprovado_em), ('', '', None))


class AprovacaoLogisticaTests(TestCase):
    def test_lote_com_paletes_ainda_na_producao_nao_aprova(self):
        _palete('TRANSFERENCIA_LOGISTICA')
        _palete('FABRICADO')
        with self.assertRaises(LoteIncompletoError):
            aprovar_lote_logistica('L1')

    def test_aprova_lote_completo(self):
        _palete('TRANSFERENCIA_LOGISTICA')
        aprovar_lote_logistica('L1')
        self.assertEqual(Palete.objects.get().estado, 'APROVADO_LOGISTICA')

    def test_gestor_logistica_reprova_com_motivo(self):
        p = _palete('TRANSFERENCIA_LOGISTICA')
        self.client.force_login(_usuario('Gestor Logística'))
        self.client.post(reverse('producao:transferencia_logistica'),
                         {'reprovar_palete': p.barcode_palete, 'motivo': 'avaria'}, secure=True)
        p.refresh_from_db()
        self.assertEqual((p.estado, p.motivo_reprovacao), ('REPROVADO', 'avaria'))


class RelatorioDatasTests(TestCase):
    def test_lotes_aprovados_sem_duplicata(self):
        agora = timezone.now()
        for _ in range(2):
            _palete('APROVADO_PRODUCAO', aprovado_producao_em=agora)
        self.assertEqual(_lotes_aprovados_na_data('aprovado_producao_em', timezone.localdate()), ['L1'])


class ReimpressaoTests(TestCase):
    def setUp(self):
        self.produto = Produto.objects.create(sku='CAFE', descricao='Café 500g')
        self.p = _palete('REPROVADO', data_fabricacao=timezone.localdate())
        self.client.force_login(_usuario('Gestor Produção'))
        self.url = reverse('producao:transferencia_gestor')

    def _corrigir(self, peso):
        with mock.patch('apps.producao.views.disparar_impressao_async') as imprimir, \
                self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {
                'corrigir_palete': self.p.pk, 'produto': self.produto.pk, 'peso_kg': peso,
            }, secure=True)
        return imprimir

    def test_peso_alterado_reimprime(self):
        imprimir = self._corrigir('480')
        imprimir.assert_called_once()
        self.assertEqual(imprimir.call_args[0][0][0].pk, self.p.pk)

    def test_sem_mudanca_de_peso_ou_data_nao_reimprime(self):
        self._corrigir('1').assert_not_called()  # peso original é 1
