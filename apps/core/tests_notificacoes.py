from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from apps.producao.models import Palete
from apps.producao.services import LoteIncompletoError, aprovar_lote

from .models import Notificacao
from .navegacao import ADM, GESTOR_LOG, GESTOR_PROD, OP_LOG, OP_PROD
from .notificacoes import notificar_grupos


def _usuario(grupo, **kw):
    u = User.objects.create_user(f'u_{grupo}_{User.objects.count()}'.replace(' ', '_'), password='x', **kw)
    u.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return u


def _palete(estado, lote='L1', **kw):
    return Palete.objects.create(sku='X', descricao='Café 500g', lote=lote, peso_kg=1, estado=estado, **kw)


class LoteAprovadoProducaoTests(TestCase):
    def setUp(self):
        self.op_log = _usuario(OP_LOG)
        self.gestor_log = _usuario(GESTOR_LOG)
        self.adm = _usuario(ADM)
        self.op_prod = _usuario(OP_PROD)
        self.gestor_prod = _usuario(GESTOR_PROD)
        self.inativo = _usuario(OP_LOG, is_active=False)

    def test_notifica_logistica_e_administradores(self):
        _palete('TRANSFERENCIA_PRODUCAO')
        _palete('TRANSFERENCIA_PRODUCAO')
        with self.captureOnCommitCallbacks(execute=True):
            aprovar_lote('L1', usuario=self.gestor_prod)

        destinatarios = set(Notificacao.objects.values_list('usuario', flat=True))
        self.assertEqual(destinatarios, {self.op_log.pk, self.gestor_log.pk, self.adm.pk})
        n = Notificacao.objects.get(usuario=self.op_log)
        self.assertEqual(n.tipo, Notificacao.LOTE_APROVADO_PRODUCAO)
        self.assertIn('L1', n.titulo)
        self.assertIn('2 palete(s)', n.mensagem)
        self.assertIn('Café 500g', n.mensagem)
        self.assertEqual(n.link, reverse('producao:transferencia_logistica'))
        self.assertIsNone(n.lida_em)

    def test_autor_da_aprovacao_tambem_e_notificado(self):
        _palete('TRANSFERENCIA_PRODUCAO')
        with self.captureOnCommitCallbacks(execute=True):
            aprovar_lote('L1', usuario=self.adm)
        self.assertTrue(Notificacao.objects.filter(usuario=self.adm).exists())
        self.assertTrue(Notificacao.objects.filter(usuario=self.op_log).exists())

    def test_aprovacao_que_falha_nao_notifica(self):
        _palete('TRANSFERENCIA_PRODUCAO')
        _palete('FABRICADO')
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(LoteIncompletoError):
                aprovar_lote('L1', usuario=self.gestor_prod)
        self.assertFalse(Notificacao.objects.exists())

    def test_aprovacao_pela_tela_notifica(self):
        _palete('TRANSFERENCIA_PRODUCAO')
        self.client.force_login(self.gestor_prod)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reverse('producao:transferencia_gestor'), {'aprovar_lote': 'L1'}, secure=True)
        self.assertIn('flash=lote_ok:L1', r['Location'])
        self.assertEqual(Notificacao.objects.count(), 3)


class NotificacaoEndpointsTests(TestCase):
    def setUp(self):
        self.u = _usuario(OP_LOG)
        self.outro = _usuario(OP_LOG)
        with self.captureOnCommitCallbacks(execute=True):
            notificar_grupos([OP_LOG], Notificacao.LOTE_APROVADO_PRODUCAO, 'T1', 'M1', link='/destino/')
            notificar_grupos([OP_LOG], Notificacao.LOTE_APROVADO_PRODUCAO, 'T2', 'M2')
        self.minha = Notificacao.objects.filter(usuario=self.u, titulo='T1').get()
        self.alheia = Notificacao.objects.filter(usuario=self.outro).first()
        self.client.force_login(self.u)

    def _ajax_post(self, url):
        return self.client.post(url, secure=True, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_json_retorna_somente_do_usuario(self):
        r = self.client.get(reverse('notificacoes_json'), secure=True)
        dados = r.json()
        self.assertEqual(dados['nao_lidas'], 2)
        self.assertEqual([i['titulo'] for i in dados['itens']], ['T2', 'T1'])
        ids = {i['id'] for i in dados['itens']}
        self.assertNotIn(self.alheia.pk, ids)

    def test_ler_uma(self):
        r = self._ajax_post(reverse('notificacao_ler', args=[self.minha.pk]))
        self.assertEqual(r.status_code, 204)
        self.minha.refresh_from_db()
        self.assertIsNotNone(self.minha.lida_em)
        self.assertEqual(self.client.get(reverse('notificacoes_json'), secure=True).json()['nao_lidas'], 1)

    def test_ler_via_formulario_redireciona_ao_link(self):
        r = self.client.post(reverse('notificacao_ler', args=[self.minha.pk]), secure=True)
        self.assertRedirects(r, '/destino/', fetch_redirect_response=False)

    def test_nao_le_notificacao_de_outro_usuario(self):
        r = self._ajax_post(reverse('notificacao_ler', args=[self.alheia.pk]))
        self.assertEqual(r.status_code, 404)
        self.alheia.refresh_from_db()
        self.assertIsNone(self.alheia.lida_em)

    def test_ler_todas(self):
        self.assertEqual(self._ajax_post(reverse('notificacoes_ler_todas')).status_code, 204)
        self.assertFalse(Notificacao.objects.filter(usuario=self.u, lida_em__isnull=True).exists())
        self.assertTrue(Notificacao.objects.filter(usuario=self.outro, lida_em__isnull=True).exists())

    def test_ler_exige_post(self):
        self.assertEqual(self.client.get(reverse('notificacao_ler', args=[self.minha.pk]), secure=True).status_code, 405)

    def test_sino_e_contador_no_shell(self):
        r = self.client.get(reverse('notificacoes'), secure=True)
        self.assertContains(r, 'id="notif-botao"')
        self.assertContains(r, 'id="notif-contador"')
        self.assertContains(r, 'T1')
        self.assertNotContains(r, 'Sino de notificações do topbar')

    def test_anonimo_vai_para_login(self):
        self.client.logout()
        for url in (reverse('notificacoes_json'), reverse('notificacoes')):
            r = self.client.get(url, secure=True)
            self.assertEqual(r.status_code, 302)
            self.assertIn('login', r['Location'])
