from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.ldap_signals import _mapear_grupos_ldap
from apps.estoque.models import Endereco
from apps.recebimento.models import Palete, PaleteHistorico
from apps.recebimento.services import aprovar_lote, aprovar_lote_logistica


class GruposPadraoTests(TestCase):

    def test_grupos_criados_pela_migration(self):
        nomes = set(Group.objects.values_list('name', flat=True))
        self.assertTrue({
            'Administradores', 'Operador Produção', 'Operador Logística',
            'Gestor Produção', 'Gestor Logística',
        }.issubset(nomes))

    def test_grupos_antigos_nao_existem_mais(self):
        nomes = set(Group.objects.values_list('name', flat=True))
        self.assertNotIn('Gestores', nomes)
        self.assertNotIn('Operadores', nomes)


class MapearGruposLdapTests(TestCase):

    MAPA = {
        'cn=wms-operador-producao,ou=grupos,dc=sitio,dc=local': ['Operador Produção'],
        'cn=wms-gestor-logistica,ou=grupos,dc=sitio,dc=local': ['Gestor Logística'],
    }

    class _LdapUserFake:
        def __init__(self, group_dns):
            self.group_dns = group_dns

    @override_settings(AUTH_LDAP_GROUP_DN_MAP=MAPA)
    def test_usuario_recebe_grupo_correspondente_ao_dn_do_ad(self):
        usuario = User.objects.create_user('joao.silva', password='x')
        ldap_user = self._LdapUserFake({'CN=WMS-Operador-Producao,OU=Grupos,DC=sitio,DC=local'})

        _mapear_grupos_ldap(sender=None, user=usuario, ldap_user=ldap_user)

        self.assertEqual(list(usuario.groups.values_list('name', flat=True)), ['Operador Produção'])

    @override_settings(AUTH_LDAP_GROUP_DN_MAP=MAPA)
    def test_grupo_mapeado_e_removido_quando_usuario_sai_do_grupo_ad(self):
        usuario = User.objects.create_user('maria.souza', password='x')
        usuario.groups.add(Group.objects.get(name='Gestor Logística'))

        _mapear_grupos_ldap(sender=None, user=usuario, ldap_user=self._LdapUserFake(set()))

        self.assertEqual(list(usuario.groups.values_list('name', flat=True)), [])

    @override_settings(AUTH_LDAP_GROUP_DN_MAP=MAPA)
    def test_grupo_nao_mapeado_do_usuario_e_preservado(self):
        usuario = User.objects.create_user('ana.lima', password='x')
        usuario.groups.add(Group.objects.get(name='Administradores'))

        _mapear_grupos_ldap(sender=None, user=usuario, ldap_user=self._LdapUserFake(set()))

        self.assertEqual(list(usuario.groups.values_list('name', flat=True)), ['Administradores'])

    @override_settings(AUTH_LDAP_GROUP_DN_MAP=None)
    def test_sem_mapeamento_configurado_nao_faz_nada(self):
        usuario = User.objects.create_user('sem.mapa', password='x')

        _mapear_grupos_ldap(sender=None, user=usuario, ldap_user=self._LdapUserFake({'cn=qualquer'}))

        self.assertEqual(list(usuario.groups.values_list('name', flat=True)), [])


class CriarUsuariosDemoTests(TestCase):

    def test_cada_usuario_demo_fica_no_grupo_correto(self):
        call_command('criar_usuarios_demo')

        mapa_esperado = {
            'admin_demo': 'Administradores',
            'operador_producao': 'Operador Produção',
            'operador_logistica': 'Operador Logística',
            'gestor_producao': 'Gestor Produção',
            'gestor_logistica': 'Gestor Logística',
        }
        for username, grupo_esperado in mapa_esperado.items():
            usuario = User.objects.get(username=username)
            self.assertEqual(list(usuario.groups.values_list('name', flat=True)), [grupo_esperado])

    def test_comando_e_idempotente(self):
        call_command('criar_usuarios_demo')
        call_command('criar_usuarios_demo')
        usernames = [
            'admin_demo', 'operador_producao', 'operador_logistica',
            'gestor_producao', 'gestor_logistica',
        ]
        self.assertEqual(User.objects.filter(username__in=usernames).count(), 5)


class AutenticacaoEPermissoesTests(TestCase):

    def setUp(self):
        self.operador = User.objects.create_user('operador1', password='senha-teste-123')
        self.operador.groups.add(Group.objects.get(name='Operador Produção'))

        self.gestor = User.objects.create_user('gestor1', password='senha-teste-123')
        self.gestor.groups.add(Group.objects.get(name='Operador Logística'))

    def test_login_renderiza_formulario(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_login_com_credenciais_validas_redireciona_para_home(self):
        response = self.client.post(reverse('login'), {
            'username': 'operador1',
            'password': 'senha-teste-123',
        })
        self.assertRedirects(response, reverse('home'))

    def test_anonimo_e_redirecionado_para_login(self):
        destino = reverse('confirmar_armazenagem')
        response = self.client.get(destino)
        self.assertRedirects(response, f"{reverse('login')}?next={destino}")

    def test_operador_recebe_403_na_confirmacao_do_gestor(self):
        self.client.login(username='operador1', password='senha-teste-123')
        response = self.client.get(reverse('confirmar_armazenagem'))
        self.assertEqual(response.status_code, 403)

    def test_gestor_acessa_confirmacao(self):
        self.client.login(username='gestor1', password='senha-teste-123')
        response = self.client.get(reverse('confirmar_armazenagem'))
        self.assertEqual(response.status_code, 200)


class AcessoPorPapelTests(TestCase):
    """Cobre a matriz completa de acesso por grupo das views com @group_required
    em apps/core/views.py (hoje só confirmar_armazenagem). Segue o mesmo estilo
    de asserção de AutenticacaoEPermissoesTests."""

    def setUp(self):
        self.operador_producao = User.objects.create_user('ap-operador-producao', password='senha-teste-123')
        self.operador_producao.groups.add(Group.objects.get(name='Operador Produção'))

        self.operador_logistica = User.objects.create_user('ap-operador-logistica', password='senha-teste-123')
        self.operador_logistica.groups.add(Group.objects.get(name='Operador Logística'))

        self.administrador = User.objects.create_user('ap-administrador', password='senha-teste-123')
        self.administrador.groups.add(Group.objects.get(name='Administradores'))

    def test_confirmar_armazenagem_anonimo_e_redirecionado_para_login(self):
        destino = reverse('confirmar_armazenagem')
        response = self.client.get(destino)
        self.assertRedirects(response, f"{reverse('login')}?next={destino}")

    def test_confirmar_armazenagem_operador_producao_recebe_403(self):
        self.client.login(username='ap-operador-producao', password='senha-teste-123')
        response = self.client.get(reverse('confirmar_armazenagem'))
        self.assertEqual(response.status_code, 403)

    def test_confirmar_armazenagem_operador_logistica_acessa(self):
        self.client.login(username='ap-operador-logistica', password='senha-teste-123')
        response = self.client.get(reverse('confirmar_armazenagem'))
        self.assertEqual(response.status_code, 200)

    def test_confirmar_armazenagem_administrador_acessa(self):
        self.client.login(username='ap-administrador', password='senha-teste-123')
        response = self.client.get(reverse('confirmar_armazenagem'))
        self.assertEqual(response.status_code, 200)


class ConfirmacaoArmazenagemTests(TestCase):

    def setUp(self):
        self.gestor = User.objects.create_user('gestor2', password='senha-gestor-123')
        self.gestor.groups.add(Group.objects.get(name='Operador Logística'))

        self.endereco = Endereco.objects.create(
            modulo=5, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=5),
            capacidade=21, ocupacao_atual=1,
        )
        self.palete = Palete.objects.create(
            sku='CAFE-X', lote='L-X', descricao='Café X', peso_kg='10.000',
            estado='ARMAZENADO', endereco=self.endereco,
        )
        self.client.login(username='gestor2', password='senha-gestor-123')

    def test_senha_correta_finaliza_paletes_armazenados(self):
        response = self.client.post(reverse('confirmar_armazenagem'), {'senha': 'senha-gestor-123'})
        self.assertRedirects(response, reverse('confirmar_armazenagem'))
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'FINALIZADO')

    def test_senha_incorreta_nao_altera_estado(self):
        response = self.client.post(reverse('confirmar_armazenagem'), {'senha': 'senha-errada'})
        self.assertEqual(response.status_code, 200)
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'ARMAZENADO')


class EstatisticasViewTests(TestCase):

    def setUp(self):
        self.usuario = User.objects.create_user('usuario-estatisticas', password='senha-teste-123')
        # Administradores enxerga as duas seções (produção e logística) — os testes
        # deste conjunto exercitam total_lotes_producao/total_lotes_logistica e
        # dependem de pode_ver_producao/pode_ver_logistica estarem ambos True.
        self.usuario.groups.add(Group.objects.get(name='Administradores'))
        self.client.login(username='usuario-estatisticas', password='senha-teste-123')

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        destino = reverse('estatisticas')
        response = self.client.get(destino)
        self.assertRedirects(response, f"{reverse('login')}?next={destino}")

    def test_usuario_sem_grupo_acessa_normalmente(self):
        response = self.client.get(reverse('estatisticas'))
        self.assertEqual(response.status_code, 200)

    def test_total_de_paletes_criados_sem_filtro(self):
        Palete.objects.create(sku='CAFE-A', lote='L-1', descricao='Café A', peso_kg='500.000')
        Palete.objects.create(sku='CAFE-B', lote='L-2', descricao='Café B', peso_kg='500.000')

        response = self.client.get(reverse('estatisticas'))
        self.assertEqual(response.context['total_paletes_criados'], 2)

    def test_filtro_de_periodo_de_paletes_criados_exclui_fora_do_intervalo(self):
        dentro = Palete.objects.create(sku='CAFE-A', lote='L-1', descricao='Café A', peso_kg='500.000')
        fora = Palete.objects.create(sku='CAFE-B', lote='L-2', descricao='Café B', peso_kg='500.000')

        Palete.objects.filter(pk=dentro.pk).update(criado_em='2026-08-10T12:00:00Z')
        Palete.objects.filter(pk=fora.pk).update(criado_em='2026-01-01T12:00:00Z')

        response = self.client.get(reverse('estatisticas'), {
            'criados_de': '2026-08-01', 'criados_ate': '2026-08-31',
        })
        self.assertEqual(response.context['total_paletes_criados'], 1)

    def test_agrupamento_por_produto_soma_quantidade_e_peso(self):
        Palete.objects.create(sku='CAFE-A', lote='L-1', descricao='Café A', peso_kg='500.000')
        Palete.objects.create(sku='CAFE-A', lote='L-2', descricao='Café A', peso_kg='500.000')
        Palete.objects.create(sku='CAFE-B', lote='L-3', descricao='Café B', peso_kg='500.000')

        response = self.client.get(reverse('estatisticas'))
        por_produto = {item['sku']: item for item in response.context['por_produto']}

        self.assertEqual(por_produto['CAFE-A']['quantidade'], 2)
        self.assertEqual(por_produto['CAFE-A']['peso_total'], 1000)
        self.assertEqual(por_produto['CAFE-B']['quantidade'], 1)
        self.assertEqual(por_produto['CAFE-B']['peso_total'], 500)

    def test_total_de_lotes_aprovados_producao_conta_lotes_distintos_nao_paletes(self):
        for _ in range(3):
            palete = Palete.objects.create(sku='CAFE-A', lote='L-1', descricao='Café A', peso_kg='500.000')
            palete.estado = 'EM_TRANSFERENCIA'
            palete.save(update_fields=['estado'])

        aprovar_lote('L-1', usuario=self.usuario)

        response = self.client.get(reverse('estatisticas'))
        self.assertEqual(response.context['total_lotes_producao'], 1)
        self.assertEqual(response.context['peso_total_producao'], 1500)

    def test_total_de_lotes_aprovados_logistica_usa_estado_conferido_logistica(self):
        for _ in range(2):
            palete = Palete.objects.create(sku='CAFE-A', lote='L-1', descricao='Café A', peso_kg='500.000')
            palete.estado = 'EM_TRANSFERENCIA'
            palete.save(update_fields=['estado'])
        aprovar_lote('L-1', usuario=self.usuario)

        palete_so_producao = Palete.objects.create(sku='CAFE-B', lote='L-2', descricao='Café B', peso_kg='500.000')
        palete_so_producao.estado = 'EM_TRANSFERENCIA'
        palete_so_producao.save(update_fields=['estado'])
        aprovar_lote('L-2', usuario=self.usuario)

        Palete.objects.filter(lote='L-1').update(estado='EM_CONFERENCIA_LOGISTICA')
        aprovar_lote_logistica('L-1', usuario=self.usuario)

        response = self.client.get(reverse('estatisticas'))
        self.assertEqual(response.context['total_lotes_logistica'], 1)
        self.assertEqual(response.context['peso_total_logistica'], 1000)
        self.assertEqual(response.context['total_lotes_producao'], 2)

    def test_filtro_de_periodo_lotes_producao_usa_data_do_historico_nao_criado_em(self):
        palete = Palete.objects.create(sku='CAFE-A', lote='L-1', descricao='Café A', peso_kg='500.000')
        Palete.objects.filter(pk=palete.pk).update(criado_em='2026-01-01T12:00:00Z')
        palete.estado = 'EM_TRANSFERENCIA'
        palete.save(update_fields=['estado'])

        aprovar_lote('L-1', usuario=self.usuario)
        PaleteHistorico.objects.filter(palete=palete, estado='CONFERIDO').update(criado_em='2026-08-10T12:00:00Z')

        response = self.client.get(reverse('estatisticas'), {
            'producao_de': '2026-08-01', 'producao_ate': '2026-08-31',
        })
        self.assertEqual(response.context['total_lotes_producao'], 1)

    def test_pagina_sem_dados_nao_quebra(self):
        response = self.client.get(reverse('estatisticas'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_paletes_criados'], 0)
        self.assertEqual(response.context['por_produto'], [])
        self.assertEqual(response.context['total_lotes_producao'], 0)
        self.assertEqual(response.context['total_lotes_logistica'], 0)

    def test_grafico_json_embutido_no_html(self):
        response = self.client.get(reverse('estatisticas'))
        self.assertContains(response, 'dados-grafico-criados')
        self.assertContains(response, 'dados-grafico-produto')
        self.assertContains(response, 'dados-grafico-producao')
        self.assertContains(response, 'dados-grafico-logistica')


class EstatisticasVisibilidadePorPapelTests(TestCase):
    """Cobre pode_ver_producao/pode_ver_logistica no contexto de `estatisticas`
    e a visibilidade condicional das seções de lotes aprovados, tanto no GET
    normal (estatisticas.html) quanto no caminho AJAX (_resultados_estatisticas.html)."""

    TITULO_PRODUCAO = 'Lotes Aprovados — Transferência da Produção'
    TITULO_LOGISTICA = 'Lotes Aprovados — Transferência da Logística'

    def _login(self, username, grupo=None, superuser=False):
        if superuser:
            usuario = User.objects.create_superuser(username, password='senha-teste-123')
        else:
            usuario = User.objects.create_user(username, password='senha-teste-123')
            if grupo:
                usuario.groups.add(Group.objects.get(name=grupo))
        self.client.login(username=username, password='senha-teste-123')
        return usuario

    def test_gestor_producao_ve_apenas_secao_de_producao(self):
        self._login('ev-gestor-producao', grupo='Gestor Produção')
        response = self.client.get(reverse('estatisticas'))
        self.assertIs(response.context['pode_ver_producao'], True)
        self.assertIs(response.context['pode_ver_logistica'], False)
        self.assertContains(response, self.TITULO_PRODUCAO)
        self.assertNotContains(response, self.TITULO_LOGISTICA)

    def test_gestor_producao_ve_apenas_secao_de_producao_via_ajax(self):
        self._login('ev-gestor-producao-ajax', grupo='Gestor Produção')
        response = self.client.get(reverse('estatisticas'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertIs(response.context['pode_ver_producao'], True)
        self.assertIs(response.context['pode_ver_logistica'], False)
        self.assertContains(response, self.TITULO_PRODUCAO)
        self.assertNotContains(response, self.TITULO_LOGISTICA)

    def test_gestor_logistica_ve_apenas_secao_de_logistica(self):
        self._login('ev-gestor-logistica', grupo='Gestor Logística')
        response = self.client.get(reverse('estatisticas'))
        self.assertIs(response.context['pode_ver_producao'], False)
        self.assertIs(response.context['pode_ver_logistica'], True)
        self.assertNotContains(response, self.TITULO_PRODUCAO)
        self.assertContains(response, self.TITULO_LOGISTICA)

    def test_administrador_ve_as_duas_secoes(self):
        self._login('ev-administrador', grupo='Administradores')
        response = self.client.get(reverse('estatisticas'))
        self.assertIs(response.context['pode_ver_producao'], True)
        self.assertIs(response.context['pode_ver_logistica'], True)
        self.assertContains(response, self.TITULO_PRODUCAO)
        self.assertContains(response, self.TITULO_LOGISTICA)

    def test_superuser_ve_as_duas_secoes(self):
        self._login('ev-superuser', superuser=True)
        response = self.client.get(reverse('estatisticas'))
        self.assertIs(response.context['pode_ver_producao'], True)
        self.assertIs(response.context['pode_ver_logistica'], True)
        self.assertContains(response, self.TITULO_PRODUCAO)
        self.assertContains(response, self.TITULO_LOGISTICA)

    def test_usuario_sem_grupo_nao_ve_nenhuma_secao_mas_acessa_a_pagina(self):
        self._login('ev-sem-grupo')
        response = self.client.get(reverse('estatisticas'))
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.context['pode_ver_producao'], False)
        self.assertIs(response.context['pode_ver_logistica'], False)
        self.assertNotContains(response, self.TITULO_PRODUCAO)
        self.assertNotContains(response, self.TITULO_LOGISTICA)
