from django.contrib.auth.models import Group, User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.recebimento.models import Palete

from .models import Endereco
from .services import EnderecoCheioError, PaleteNaoArmazenadoError, alocar_lote, retirar_palete_do_mapa
from .views import _maior_vaga_disponivel


def _criar_enderecos(corredores='A', andar=1, posicoes=3):
    for corredor in corredores:
        for pos in range(1, posicoes + 1):
            Endereco.objects.create(
                corredor=corredor, andar=andar, posicao=pos,
                codigo=Endereco.montar_codigo(corredor, andar, pos),
            )


def _palete(lote='L1', estado='CONFERIDO_LOGISTICA', **kw):
    return Palete.objects.create(sku='X', descricao='x', lote=lote, peso_kg=1, estado=estado, **kw)


def _usuario(grupo):
    u = User.objects.create_user(f'u_{grupo}'.replace(' ', '_'), password='x')
    u.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return u


class MaiorVagaDisponivelTests(TestCase):
    def test_um_palete_nao_elimina_o_corredor_inteiro(self):
        _criar_enderecos(posicoes=5)
        _palete(estado='ARMAZENADO', endereco=Endereco.objects.get(posicao=1))
        self.assertEqual(_maior_vaga_disponivel(), 4)

    def test_sem_nenhuma_vaga(self):
        _criar_enderecos(posicoes=1)
        _palete(estado='ARMAZENADO', endereco=Endereco.objects.get())
        self.assertEqual(_maior_vaga_disponivel(), 0)

    def test_ignora_enderecos_inativos(self):
        _criar_enderecos(posicoes=3)
        Endereco.objects.filter(posicao__gt=1).update(ativo=False)
        self.assertEqual(_maior_vaga_disponivel(), 1)


class AlocarLoteTests(TestCase):
    def setUp(self):
        _criar_enderecos(posicoes=3)

    def test_aloca_nas_primeiras_posicoes_livres(self):
        for _ in range(2):
            _palete()
        enderecos, _ = alocar_lote('L1', 'A', 1)
        self.assertEqual([e.posicao for e in enderecos], [1, 2])
        self.assertEqual(Palete.objects.filter(estado='ARMAZENADO').values('endereco').distinct().count(), 2)

    def test_lote_maior_que_vagas_falha_sem_alterar(self):
        for _ in range(4):
            _palete()
        with self.assertRaises(EnderecoCheioError):
            alocar_lote('L1', 'A', 1)
        self.assertEqual(Palete.objects.filter(estado='CONFERIDO_LOGISTICA').count(), 4)

    def test_parcial_aloca_so_o_que_cabe(self):
        for _ in range(4):
            _palete()
        _, paletes = alocar_lote('L1', 'A', 1, permitir_parcial=True)
        self.assertEqual(len(paletes), 3)
        self.assertEqual(Palete.objects.filter(estado='CONFERIDO_LOGISTICA').count(), 1)

    def test_banco_impede_dois_armazenados_na_mesma_posicao(self):
        e = Endereco.objects.first()
        _palete(estado='ARMAZENADO', endereco=e)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _palete(estado='ARMAZENADO', endereco=e)

    def test_retirar_palete(self):
        _palete()
        _, (p,) = alocar_lote('L1', 'A', 1)
        retirar_palete_do_mapa(p, usuario=None)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'RETIRADO')
        with self.assertRaises(PaleteNaoArmazenadoError):
            retirar_palete_do_mapa(p, usuario=None)


class PutAwayViewTests(TestCase):
    def setUp(self):
        _criar_enderecos(posicoes=3)
        self.url = reverse('estoque:putaway')

    def test_exige_operador_logistica(self):
        self.client.force_login(_usuario('Operador Produção'))
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 403)

    def test_fluxo_bipar_e_armazenar(self):
        p = _palete()
        self.client.force_login(_usuario('Operador Logística'))
        r = self.client.post(self.url, {'barcode_palete': p.barcode_palete}, secure=True)
        self.assertIn('lote=L1', r['Location'])
        r = self.client.get(self.url, {'lote': 'L1'}, secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context['sem_vagas'])
        self.client.post(self.url, {'destino': 'A', 'andar': 1, 'lote': 'L1'}, secure=True)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'ARMAZENADO')

    def test_armazem_com_palete_armazenado_ainda_tem_vagas(self):
        _palete(lote='OLD', estado='ARMAZENADO', endereco=Endereco.objects.get(posicao=1))
        _palete()
        self.client.force_login(_usuario('Operador Logística'))
        r = self.client.get(self.url, {'lote': 'L1'}, secure=True)
        self.assertFalse(r.context['sem_vagas'])
        self.assertFalse(r.context['modo_parcial'])


class RetirarPermissaoTests(TestCase):
    def setUp(self):
        _criar_enderecos(posicoes=1)
        self.p = _palete(estado='ARMAZENADO', endereco=Endereco.objects.get())
        self.url = reverse('estoque:consultar_palete')

    def test_producao_nao_retira(self):
        self.client.force_login(_usuario('Gestor Produção'))
        self.client.post(self.url, {'barcode_palete': self.p.barcode_palete, 'retirar': '1'}, secure=True)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'ARMAZENADO')

    def test_logistica_retira(self):
        self.client.force_login(_usuario('Operador Logística'))
        self.client.post(self.url, {'barcode_palete': self.p.barcode_palete, 'retirar': '1'}, secure=True)
        self.p.refresh_from_db()
        self.assertEqual(self.p.estado, 'RETIRADO')

    def test_plano_armazenagem_sem_endereco_nao_quebra(self):
        Palete.objects.filter(pk=self.p.pk).update(endereco=None)
        self.client.force_login(_usuario('Operador Logística'))
        r = self.client.get(reverse('estoque:plano_armazenagem'), {'barcode_palete': self.p.barcode_palete}, secure=True)
        self.assertEqual(r.status_code, 200)
