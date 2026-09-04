import threading
import time
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connection
from django.db.utils import OperationalError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.estoque.models import Endereco
from apps.estoque.services import (
    EnderecoCheioError,
    LoteVazioError,
    PaleteNaoArmazenadoError,
    alocar_lote,
    retirar_palete_do_mapa,
)
from apps.recebimento.models import Palete


class GerarEstruturaArmazemTests(TestCase):

    def test_cria_exatamente_60_enderecos(self):
        call_command('gerar_estrutura_armazem')
        self.assertEqual(Endereco.objects.count(), 60)

    def test_codigos_seguem_padrao_e_sao_unicos(self):
        call_command('gerar_estrutura_armazem')
        codigos = set(Endereco.objects.values_list('codigo', flat=True))
        esperados = {
            f'{andar}-{modulo:02d}'
            for modulo in range(1, 21) for andar in ('A', 'B', 'C')
        }
        self.assertEqual(codigos, esperados)

    def test_todos_iniciam_com_capacidade_21_e_ocupacao_zero(self):
        call_command('gerar_estrutura_armazem')
        self.assertFalse(
            Endereco.objects.exclude(capacidade=21, ocupacao_atual=0).exists()
        )

    def test_comando_e_idempotente(self):
        call_command('gerar_estrutura_armazem')
        call_command('gerar_estrutura_armazem')
        self.assertEqual(Endereco.objects.count(), 60)


def _criar_palete_conferido(sufixo):
    """Cria um palete já elegível para put-away: aprovado pela Produção E confirmado
    pela Logística (CONFERIDO_LOGISTICA) — o estado-gate real de alocar_lote/mapa_estoque."""
    return Palete.objects.create(
        sku=f'CAFE-{sufixo}',
        lote=f'L-{sufixo}',
        descricao='Café teste',
        peso_kg='20.000',
        estado='CONFERIDO_LOGISTICA',
    )


def _criar_palete_armazenado(sufixo, endereco):
    palete = _criar_palete_conferido(sufixo)
    palete.estado = 'ARMAZENADO'
    palete.endereco = endereco
    palete.save()
    return palete


def _criar_paletes_conferidos_lote(lote, quantidade):
    """Cria `quantidade` paletes CONFERIDO_LOGISTICA, todos com o mesmo `lote`."""
    return [
        Palete.objects.create(
            sku=f'CAFE-{lote}', lote=lote, descricao='Café teste',
            peso_kg='20.000', estado='CONFERIDO_LOGISTICA',
        )
        for _ in range(quantidade)
    ]


class AlocarLoteCapacidadeTests(TestCase):

    def setUp(self):
        self.endereco = Endereco.objects.create(
            modulo=2, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', modulo=2),
            capacidade=21, ocupacao_atual=0,
        )

    def test_aloca_lote_inteiro_de_uma_vez(self):
        _criar_paletes_conferidos_lote('L-OK', 5)

        endereco, paletes = alocar_lote('L-OK', self.endereco.codigo)

        self.endereco.refresh_from_db()
        self.assertEqual(endereco, self.endereco)
        self.assertEqual(len(paletes), 5)
        self.assertEqual(self.endereco.ocupacao_atual, 5)
        self.assertEqual(
            Palete.objects.filter(lote='L-OK', endereco=self.endereco, estado='ARMAZENADO').count(), 5
        )
        for palete in paletes:
            self.assertIsNotNone(palete.armazenado_em)

    def test_permite_alocar_ate_21_paletes(self):
        _criar_paletes_conferidos_lote('L-CHEIO', 21)

        alocar_lote('L-CHEIO', self.endereco.codigo)

        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 21)
        self.assertEqual(Palete.objects.filter(endereco=self.endereco, estado='ARMAZENADO').count(), 21)

    def test_lote_maior_que_vagas_livres_e_rejeitado_sem_alterar_nada(self):
        self.endereco.ocupacao_atual = 18
        self.endereco.save()
        paletes = _criar_paletes_conferidos_lote('L-GRANDE', 5)

        with self.assertRaises(EnderecoCheioError):
            alocar_lote('L-GRANDE', self.endereco.codigo)

        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 18)
        for palete in paletes:
            palete.refresh_from_db()
            self.assertEqual(palete.estado, 'CONFERIDO_LOGISTICA')
            self.assertIsNone(palete.endereco)

    def test_lote_sem_paletes_pendentes_levanta_erro(self):
        with self.assertRaises(LoteVazioError):
            alocar_lote('L-INEXISTENTE', self.endereco.codigo)


class AlocarLoteParcialTests(TestCase):
    """permitir_parcial=True: usado quando o lote não cabe inteiro em nenhum
    endereço único do armazém — enche o endereço escolhido e deixa o resto
    CONFERIDO_LOGISTICA, para o usuário completar em chamadas seguintes."""

    def setUp(self):
        self.endereco = Endereco.objects.create(
            modulo=2, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', modulo=2),
            capacidade=21, ocupacao_atual=0,
        )

    def test_aloca_so_o_que_cabe_e_deixa_o_resto_pendente(self):
        self.endereco.ocupacao_atual = 18
        self.endereco.save()
        paletes = _criar_paletes_conferidos_lote('L-PARCIAL', 5)

        endereco, alocados = alocar_lote('L-PARCIAL', self.endereco.codigo, permitir_parcial=True)

        self.assertEqual(len(alocados), 3)
        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 21)
        alocados_ids = {p.pk for p in alocados}
        for palete in paletes:
            palete.refresh_from_db()
            if palete.pk in alocados_ids:
                self.assertEqual(palete.estado, 'ARMAZENADO')
                self.assertEqual(palete.endereco, self.endereco)
                self.assertIsNotNone(palete.armazenado_em)
            else:
                self.assertEqual(palete.estado, 'CONFERIDO_LOGISTICA')
                self.assertIsNone(palete.endereco)
                self.assertIsNone(palete.armazenado_em)

    def test_endereco_sem_vaga_nenhuma_levanta_erro_sem_alterar_nada(self):
        """Guarda contra loop infinito: se o endereço escolhido não tem nenhuma
        vaga, a chamada deve falhar (não devolver uma alocação de zero paletes),
        senão quem chama em loop nunca avança."""
        self.endereco.ocupacao_atual = 21
        self.endereco.save()
        paletes = _criar_paletes_conferidos_lote('L-CHEIO2', 3)

        with self.assertRaises(EnderecoCheioError):
            alocar_lote('L-CHEIO2', self.endereco.codigo, permitir_parcial=True)

        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 21)
        for palete in paletes:
            palete.refresh_from_db()
            self.assertEqual(palete.estado, 'CONFERIDO_LOGISTICA')

    def test_parcial_que_cabe_inteiro_aloca_tudo(self):
        """permitir_parcial=True não trunca quando o lote inteiro cabe — a flag
        pode estar desatualizada (calculada fora da transação) sem causar dano."""
        _criar_paletes_conferidos_lote('L-CABE', 3)

        endereco, alocados = alocar_lote('L-CABE', self.endereco.codigo, permitir_parcial=True)

        self.assertEqual(len(alocados), 3)
        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 3)

    def test_parcial_respeita_ordem_fifo(self):
        self.endereco.ocupacao_atual = 19
        self.endereco.save()
        paletes = _criar_paletes_conferidos_lote('L-FIFO', 5)
        esperados = {p.pk for p in paletes[:2]}

        endereco, alocados = alocar_lote('L-FIFO', self.endereco.codigo, permitir_parcial=True)

        self.assertEqual({p.pk for p in alocados}, esperados)

    def test_parcial_registra_historico_apenas_dos_paletes_alocados(self):
        from apps.recebimento.models import PaleteHistorico

        self.endereco.ocupacao_atual = 19
        self.endereco.save()
        _criar_paletes_conferidos_lote('L-HIST', 5)

        endereco, alocados = alocar_lote('L-HIST', self.endereco.codigo, permitir_parcial=True)

        self.assertEqual(
            PaleteHistorico.objects.filter(estado='ARMAZENADO', palete__lote='L-HIST').count(),
            len(alocados),
        )
        self.assertEqual(len(alocados), 2)

    def test_duas_chamadas_parciais_armazenam_o_lote_inteiro_em_dois_enderecos(self):
        outro_endereco = Endereco.objects.create(
            modulo=3, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', modulo=3),
            capacidade=21, ocupacao_atual=0,
        )
        self.endereco.ocupacao_atual = 18
        self.endereco.save()
        _criar_paletes_conferidos_lote('L-DOIS', 5)

        _, primeira_leva = alocar_lote('L-DOIS', self.endereco.codigo, permitir_parcial=True)
        self.assertEqual(len(primeira_leva), 3)

        restantes = Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote='L-DOIS').count()
        self.assertEqual(restantes, 2)

        _, segunda_leva = alocar_lote('L-DOIS', outro_endereco.codigo, permitir_parcial=True)
        self.assertEqual(len(segunda_leva), 2)

        self.assertEqual(Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote='L-DOIS').count(), 0)
        self.assertEqual(Palete.objects.filter(estado='ARMAZENADO', lote='L-DOIS').count(), 5)


class AlocarLoteConcorrenciaTests(TransactionTestCase):
    """Simula requisições paralelas disputando a última vaga de um endereço,
    cada uma alocando um lote de um único palete."""

    def setUp(self):
        self.endereco = Endereco.objects.create(
            modulo=3, lado=1, andar='C',
            codigo=Endereco.montar_codigo(lado=1, andar='C', modulo=3),
            capacidade=21, ocupacao_atual=20,
        )

    def test_apenas_um_vencedor_para_a_ultima_vaga(self):
        NUM_CONCORRENTES = 5
        lotes = [f'RACE{i}' for i in range(NUM_CONCORRENTES)]
        for lote in lotes:
            _criar_paletes_conferidos_lote(lote, 1)
        resultados = []
        barreira = threading.Barrier(NUM_CONCORRENTES)
        codigo = self.endereco.codigo

        def tentar_alocar(lote):
            barreira.wait()
            # SQLite não bloqueia-e-espera por linha como Postgres/MySQL: sob disputa,
            # a transação perdedora recebe "database is locked" na hora em vez de
            # aguardar o commit da vencedora. Retentar aqui simula essa espera, para
            # que a rejeição observada seja sempre a regra de negócio (EnderecoCheioError),
            # como aconteceria de fato em produção (MySQL).
            try:
                for _ in range(100):
                    try:
                        alocar_lote(lote, codigo)
                        resultados.append('sucesso')
                        return
                    except OperationalError:
                        time.sleep(0.05)
                resultados.append('lock-persistente')
            except EnderecoCheioError:
                resultados.append('rejeitado')
            finally:
                connection.close()

        threads = [threading.Thread(target=tentar_alocar, args=(lote,)) for lote in lotes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.endereco.refresh_from_db()
        self.assertEqual(resultados.count('sucesso'), 1)
        self.assertEqual(resultados.count('rejeitado'), NUM_CONCORRENTES - 1)
        self.assertEqual(self.endereco.ocupacao_atual, 21)
        self.assertEqual(Palete.objects.filter(endereco=self.endereco, estado='ARMAZENADO').count(), 1)


class PutAwayViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('operador2', password='senha-teste-123')
        self.user.groups.add(Group.objects.get(name='Operador Logística'))
        self.client.login(username='operador2', password='senha-teste-123')
        self.endereco = Endereco.objects.create(
            modulo=4, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=4),
            capacidade=21, ocupacao_atual=0,
        )
        self.palete = _criar_palete_conferido('VIEW1')

    # Etapa A: localizar paletes pendentes pelo palete bipado
    def test_tela_inicial_pede_a_bipagem_do_palete(self):
        response = self.client.get(reverse('estoque:putaway'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="barcode_palete"')
        self.assertNotContains(response, 'name="codigo_endereco"')

    def test_tela_inicial_lista_paletes_pendentes_sem_acao(self):
        response = self.client.get(reverse('estoque:putaway'))
        self.assertContains(response, self.palete.barcode_palete)
        self.assertContains(response, self.palete.lote)
        self.assertNotContains(response, 'Armazenar</a>')

    def test_bipar_palete_valido_redireciona_para_tela_do_lote(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': self.palete.barcode_palete
        })
        self.assertRedirects(response, f"{reverse('estoque:putaway')}?lote={self.palete.lote}")

    def test_bipar_barcode_desconhecido_mostra_erro(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': 'PLT-NAOEXISTE'
        }, follow=True)
        self.assertRedirects(response, reverse('estoque:putaway'))
        self.assertContains(response, 'não encontrado')

    def test_bipar_barcode_vazio_mostra_erro(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': ''
        }, follow=True)
        self.assertRedirects(response, reverse('estoque:putaway'))
        self.assertContains(response, 'Bipe ou digite o código do palete.')

    def test_bipar_palete_em_estado_errado_mostra_erro(self):
        palete_armazenado = _criar_palete_conferido('JABIPADO')
        palete_armazenado.estado = 'ARMAZENADO'
        palete_armazenado.save()
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': palete_armazenado.barcode_palete
        }, follow=True)
        self.assertRedirects(response, reverse('estoque:putaway'))
        self.assertContains(response, 'ainda não está pendente de armazenagem')

    def test_tela_do_lote_mostra_dados_e_grade_de_enderecos(self):
        response = self.client.get(reverse('estoque:putaway'), {'lote': self.palete.lote})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertContains(response, self.palete.sku)
        self.assertContains(response, self.palete.lote)
        self.assertContains(response, self.endereco.codigo)
        self.assertContains(response, 'name="codigo_endereco"')
        self.assertContains(response, f'value="{self.endereco.codigo}"')

    def test_post_lote_mostra_todos_os_paletes_do_lote(self):
        paletes = _criar_paletes_conferidos_lote('L-TODOS', 3)
        outro_lote = _criar_palete_conferido('OUTRO')  # lote diferente — não deve aparecer
        palete_estado_errado = _criar_palete_conferido('JAARM')
        palete_estado_errado.estado = 'ARMAZENADO'
        palete_estado_errado.lote = 'L-TODOS'  # mesmo lote, estado errado — não deve aparecer
        palete_estado_errado.save()

        response = self.client.post(
            reverse('estoque:putaway'), {'barcode_palete': paletes[0].barcode_palete}, follow=True
        )

        self.assertEqual(len(response.context['paletes']), 3)
        self.assertCountEqual(
            [p.barcode_palete for p in response.context['paletes']],
            [p.barcode_palete for p in paletes],
        )
        for p in paletes:
            self.assertContains(response, p.barcode_palete)
        self.assertNotContains(response, outro_lote.barcode_palete)
        self.assertNotContains(response, palete_estado_errado.barcode_palete)

    def test_endereco_cheio_aparece_no_grid_mas_nao_e_clicavel(self):
        self.endereco.ocupacao_atual = 21
        self.endereco.save()
        response = self.client.get(reverse('estoque:putaway'), {'lote': self.palete.lote})
        self.assertContains(response, self.endereco.codigo)
        self.assertNotContains(response, f'value="{self.endereco.codigo}"')

    def test_endereco_com_vagas_insuficientes_para_o_lote_nao_e_clicavel(self):
        """Um endereço com 1 vaga livre não pode ser oferecido para um lote de 2 paletes
        quando existe outro endereço com espaço de sobra — o lote deve ficar junto."""
        Endereco.objects.create(
            modulo=5, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=5),
            capacidade=21, ocupacao_atual=0,
        )
        _criar_paletes_conferidos_lote('L-DUPLO', 2)
        self.endereco.ocupacao_atual = 20
        self.endereco.save()
        response = self.client.get(reverse('estoque:putaway'), {'lote': 'L-DUPLO'})
        self.assertFalse(response.context['modo_parcial'])
        self.assertContains(response, self.endereco.codigo)
        self.assertNotContains(response, f'value="{self.endereco.codigo}"')

    def test_bipar_palete_ainda_criado_mostra_erro(self):
        palete_criado = Palete.objects.create(
            sku='TEST-001', descricao='Test', lote='LT-TEST', peso_kg=Decimal('20'),
            estado='CRIADO', barcode_palete='PLT-CRIADO'
        )
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': palete_criado.barcode_palete
        }, follow=True)
        self.assertRedirects(response, reverse('estoque:putaway'))
        self.assertContains(response, 'ainda não está pendente de armazenagem')

    def test_bipar_palete_conferido_pela_producao_mas_nao_pela_logistica_mostra_erro(self):
        """Prova a correção do bug: um palete aprovado pela Produção (CONFERIDO) mas
        ainda não confirmado pela Logística não pode ser guardado — só CONFERIDO_LOGISTICA pode."""
        palete_sem_logistica = Palete.objects.create(
            sku='TEST-002', descricao='Test', lote='LT-TEST2', peso_kg=Decimal('20'),
            estado='CONFERIDO', barcode_palete='PLT-SEM-LOGISTICA'
        )
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': palete_sem_logistica.barcode_palete
        }, follow=True)
        self.assertContains(response, 'ainda não está pendente de armazenagem')

    def test_get_com_lote_desconhecido_redireciona_sem_crashar(self):
        response = self.client.get(reverse('estoque:putaway'), {'lote': 'L-NAOEXISTE'})
        self.assertRedirects(response, reverse('estoque:putaway'))

    # Etapa B: alocar todos os paletes do lote no endereço
    def test_confirmar_guarda_aloca_o_unico_palete_do_lote(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': self.palete.lote,
            'codigo_endereco': self.endereco.codigo,
        })
        self.assertEqual(response.status_code, 302)
        self.palete.refresh_from_db()
        self.endereco.refresh_from_db()
        self.assertEqual(self.palete.estado, 'ARMAZENADO')
        self.assertEqual(self.palete.endereco, self.endereco)
        self.assertEqual(self.endereco.ocupacao_atual, 1)

    def test_confirmar_guarda_aloca_todos_os_paletes_do_lote_de_uma_vez(self):
        paletes = _criar_paletes_conferidos_lote('L-MULTI', 3)
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': 'L-MULTI',
            'codigo_endereco': self.endereco.codigo,
        })
        self.assertEqual(response.status_code, 302)
        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 3)
        for palete in paletes:
            palete.refresh_from_db()
            self.assertEqual(palete.estado, 'ARMAZENADO')
            self.assertEqual(palete.endereco, self.endereco)

    def test_endereco_sem_vagas_suficientes_para_o_lote_e_rejeitado_no_alocar(self):
        """Com outro endereço de sobra disponível, o lote não é aceito parcialmente
        no endereço apertado — tem que caber inteiro em algum lugar."""
        Endereco.objects.create(
            modulo=5, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=5),
            capacidade=21, ocupacao_atual=0,
        )
        self.endereco.ocupacao_atual = 19
        self.endereco.save()
        paletes = _criar_paletes_conferidos_lote('L-GRANDE', 3)
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': 'L-GRANDE',
            'codigo_endereco': self.endereco.codigo,
        })
        self.assertRedirects(response, f"{reverse('estoque:putaway')}?lote=L-GRANDE")
        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 19)
        for palete in paletes:
            palete.refresh_from_db()
            self.assertEqual(palete.estado, 'CONFERIDO_LOGISTICA')
            self.assertIsNone(palete.endereco)

    def test_endereco_desconhecido_no_alocar_mostra_erro(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': self.palete.lote,
            'codigo_endereco': 'Z-99',
        })
        self.assertEqual(response.status_code, 302)
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO_LOGISTICA')

    def test_alocar_com_lote_desconhecido_mostra_erro_sem_crashar(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': 'L-INVALIDO',
            'codigo_endereco': self.endereco.codigo,
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('estoque:putaway'))

    def test_alocar_com_lote_em_estado_invalido_mostra_erro(self):
        palete_armazenado = _criar_palete_conferido('ARMAZENADO')
        palete_armazenado.estado = 'ARMAZENADO'
        palete_armazenado.save()
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': palete_armazenado.lote,
            'codigo_endereco': self.endereco.codigo,
        })
        self.assertEqual(response.status_code, 302)
        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 0)

    # Auth
    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('estoque:putaway'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('estoque:putaway')}")

    def test_operador_producao_recebe_403(self):
        self.client.logout()
        operador_producao = User.objects.create_user('putaway-operador-producao', password='senha-teste-123')
        operador_producao.groups.add(Group.objects.get(name='Operador Produção'))
        self.client.login(username='putaway-operador-producao', password='senha-teste-123')
        response = self.client.get(reverse('estoque:putaway'))
        self.assertEqual(response.status_code, 403)


class PutAwayModoParcialTests(TestCase):
    """Quando o lote não cabe em nenhum endereço único, a Etapa B libera
    qualquer endereço com vaga e permite completar a armazenagem em mais de
    um clique — o bug relatado era a grade ficar sem nenhuma célula clicável
    nesse caso."""

    def setUp(self):
        self.user = User.objects.create_user('operador6', password='senha-teste-123')
        self.user.groups.add(Group.objects.get(name='Operador Logística'))
        self.client.login(username='operador6', password='senha-teste-123')
        self.endereco_a = Endereco.objects.create(
            modulo=7, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=7),
            capacidade=5, ocupacao_atual=0,
        )
        self.endereco_b = Endereco.objects.create(
            modulo=8, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=8),
            capacidade=5, ocupacao_atual=0,
        )
        self.paletes = _criar_paletes_conferidos_lote('L-GIGANTE', 8)

    def test_grade_fica_clicavel_quando_lote_nao_cabe_em_nenhum_endereco(self):
        """Regressão do bug relatado: um lote maior que qualquer endereço
        único não deixava nenhuma célula clicável."""
        response = self.client.get(reverse('estoque:putaway'), {'lote': 'L-GIGANTE'})
        self.assertTrue(response.context['modo_parcial'])
        self.assertContains(response, f'value="{self.endereco_a.codigo}"')
        self.assertContains(response, f'value="{self.endereco_b.codigo}"')

    def test_modo_parcial_mostra_banner_explicativo(self):
        response = self.client.get(reverse('estoque:putaway'), {'lote': 'L-GIGANTE'})
        self.assertContains(response, 'não cabe inteiro em nenhum endereço')

    def test_armazem_sem_vagas_mostra_aviso_dedicado(self):
        self.endereco_a.ocupacao_atual = 5
        self.endereco_a.save()
        self.endereco_b.ocupacao_atual = 5
        self.endereco_b.save()
        response = self.client.get(reverse('estoque:putaway'), {'lote': 'L-GIGANTE'})
        self.assertTrue(response.context['sem_vagas'])
        self.assertContains(response, 'Não há nenhuma vaga livre')
        self.assertNotContains(response, f'value="{self.endereco_a.codigo}"')

    def test_post_parcial_armazena_o_que_cabe_e_volta_para_o_mesmo_lote(self):
        # follow=True: assertRedirects, sem isso, buscaria o destino numa
        # segunda requisição por conta própria e consumiria a mensagem
        # (messages só sobrevive até a primeira renderização) antes de
        # conseguirmos checar o texto "Faltam 3" nela.
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': 'L-GIGANTE',
            'codigo_endereco': self.endereco_a.codigo,
        }, follow=True)
        self.assertRedirects(response, f"{reverse('estoque:putaway')}?lote=L-GIGANTE")
        self.assertContains(response, 'Faltam 3')
        self.endereco_a.refresh_from_db()
        self.assertEqual(self.endereco_a.ocupacao_atual, 5)
        self.assertEqual(
            Palete.objects.filter(estado='ARMAZENADO', lote='L-GIGANTE', endereco=self.endereco_a).count(), 5,
        )
        self.assertEqual(
            Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote='L-GIGANTE').count(), 3,
        )

    def test_dois_cliques_armazenam_o_lote_inteiro_e_o_ultimo_volta_para_a_etapa_a(self):
        self.client.post(reverse('estoque:putaway'), {
            'lote': 'L-GIGANTE',
            'codigo_endereco': self.endereco_a.codigo,
        })
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': 'L-GIGANTE',
            'codigo_endereco': self.endereco_b.codigo,
        })
        self.assertRedirects(response, reverse('estoque:putaway'))
        self.assertEqual(Palete.objects.filter(estado='ARMAZENADO', lote='L-GIGANTE').count(), 8)
        self.assertEqual(Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote='L-GIGANTE').count(), 0)
        self.endereco_a.refresh_from_db()
        self.endereco_b.refresh_from_db()
        self.assertEqual(self.endereco_a.ocupacao_atual, 5)
        self.assertEqual(self.endereco_b.ocupacao_atual, 3)

    def test_endereco_cheio_no_modo_parcial_preserva_o_lote_na_url(self):
        self.endereco_a.ocupacao_atual = 5
        self.endereco_a.save()
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': 'L-GIGANTE',
            'codigo_endereco': self.endereco_a.codigo,
        })
        self.assertRedirects(response, f"{reverse('estoque:putaway')}?lote=L-GIGANTE")
        self.assertEqual(Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote='L-GIGANTE').count(), 8)

    def test_endereco_desconhecido_no_modo_parcial_preserva_o_lote_na_url(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': 'L-GIGANTE',
            'codigo_endereco': 'Z-99',
        })
        self.assertRedirects(response, f"{reverse('estoque:putaway')}?lote=L-GIGANTE")

    def test_lote_desconhecido_continua_voltando_para_a_etapa_a_sem_lote(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'lote': 'L-NAOEXISTE',
            'codigo_endereco': self.endereco_a.codigo,
        })
        self.assertRedirects(response, reverse('estoque:putaway'))


class PlanoArmazenagemViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('operador5', password='senha-teste-123')
        self.user.groups.add(Group.objects.get(name='Operador Logística'))
        self.client.login(username='operador5', password='senha-teste-123')
        call_command('gerar_estrutura_armazem')
        self.endereco = Endereco.objects.get(codigo=Endereco.montar_codigo(lado=1, andar='B', modulo=9))
        self.endereco.ocupacao_atual = 3
        self.endereco.save()
        self.paletes = _criar_paletes_conferidos_lote('L-PLANO', 3)
        for p in self.paletes:
            p.estado = 'ARMAZENADO'
            p.endereco = self.endereco
            p.save()

    def test_tela_inicial_pede_a_bipagem_do_palete(self):
        response = self.client.get(reverse('estoque:plano_armazenagem'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="barcode_palete"')

    def test_palete_armazenado_mostra_endereco_e_grade_marcada(self):
        palete = self.paletes[0]
        response = self.client.get(reverse('estoque:plano_armazenagem'), {'barcode_palete': palete.barcode_palete})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.endereco.codigo)
        self.assertContains(response, palete.barcode_palete)

        grupo = response.context['grupo']
        self.assertEqual(grupo['endereco'], self.endereco)
        self.assertEqual(grupo['quantidade'], 1)
        self.assertEqual([p.pk for p in grupo['paletes']], [palete.pk])

        grade = grupo['grade']
        linha_andar = next(l for l in grade if l['andar'] == self.endereco.andar)
        indice_modulo = self.endereco.modulo - 1  # MODULOS é 1..20, célula é 0-indexada

        celula_destino = linha_andar['celulas'][indice_modulo]
        self.assertTrue(celula_destino['eh_destino'])
        self.assertTrue(celula_destino['no_caminho'])

        celula_antes = linha_andar['celulas'][indice_modulo - 1]
        self.assertTrue(celula_antes['no_caminho'])
        self.assertNotIn('eh_destino', celula_antes)

        celula_depois = linha_andar['celulas'][indice_modulo + 1]
        self.assertNotIn('no_caminho', celula_depois)

    def test_bipar_outro_palete_do_mesmo_lote_mostra_apenas_seu_proprio_endereco(self):
        """Desde a armazenagem parcial, um lote pode ter sido guardado em mais de
        um endereço — bipar cada palete individualmente mostra só o endereço
        daquele palete específico, não o lote inteiro agrupado."""
        outro_endereco = Endereco.objects.get(codigo=Endereco.montar_codigo(lado=1, andar='B', modulo=10))
        outro_endereco.ocupacao_atual = 2
        outro_endereco.save()
        palete_extra = self.paletes[-1]
        palete_extra.endereco = outro_endereco
        palete_extra.save()

        response = self.client.get(
            reverse('estoque:plano_armazenagem'), {'barcode_palete': palete_extra.barcode_palete},
        )

        grupo = response.context['grupo']
        self.assertEqual(grupo['endereco'], outro_endereco)
        self.assertEqual([p.pk for p in grupo['paletes']], [palete_extra.pk])

    def test_palete_nao_armazenado_mostra_erro(self):
        palete_pendente = _criar_palete_conferido('NAOARM')
        response = self.client.get(
            reverse('estoque:plano_armazenagem'), {'barcode_palete': palete_pendente.barcode_palete},
        )
        self.assertContains(response, 'ainda não foi armazenado')

    def test_barcode_desconhecido_mostra_erro(self):
        response = self.client.get(reverse('estoque:plano_armazenagem'), {'barcode_palete': 'PLT-NAO-EXISTE'})
        self.assertContains(response, 'não encontrado')

    def test_barcode_vazio_mostra_erro(self):
        response = self.client.get(reverse('estoque:plano_armazenagem'), {'barcode_palete': ''})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['grupo'])

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('estoque:plano_armazenagem'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('estoque:plano_armazenagem')}")


class MapaEstoqueTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('operador4', password='senha-teste-123')
        self.user.groups.add(Group.objects.get(name='Gestor Produção'))
        self.client.login(username='operador4', password='senha-teste-123')

    def test_mostra_grade_com_todos_os_enderecos_cadastrados(self):
        endereco = Endereco.objects.create(
            modulo=1, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=1),
            capacidade=21, ocupacao_atual=5,
        )
        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, endereco.codigo)
        self.assertContains(response, '5/21')

    def test_endereco_nao_cadastrado_aparece_como_vazio(self):
        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertContains(response, 'nivel-vazio')

    def test_lista_paletes_armazenados_com_endereco(self):
        endereco = Endereco.objects.create(
            modulo=6, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', modulo=6),
            capacidade=21, ocupacao_atual=1,
        )
        palete = _criar_palete_conferido('MAPA1')
        palete.estado = 'ARMAZENADO'
        palete.endereco = endereco
        palete.save()

        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertContains(response, palete.barcode_palete)
        self.assertContains(response, endereco.codigo)

    def test_totais_de_ocupacao_sao_somados_corretamente(self):
        Endereco.objects.create(
            modulo=7, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=7),
            capacidade=21, ocupacao_atual=10,
        )
        Endereco.objects.create(
            modulo=7, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', modulo=7),
            capacidade=21, ocupacao_atual=5,
        )

        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(response.context['ocupacao_total'], 15)
        self.assertEqual(response.context['capacidade_total'], 42)
        self.assertEqual(response.context['percentual_geral'], round(15 / 42 * 100))

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('estoque:mapa_estoque')}")

    def test_operador_logistica_recebe_403(self):
        self.client.logout()
        operador_logistica = User.objects.create_user('mapa-operador-logistica', password='senha-teste-123')
        operador_logistica.groups.add(Group.objects.get(name='Operador Logística'))
        self.client.login(username='mapa-operador-logistica', password='senha-teste-123')
        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(response.status_code, 403)

    def test_conta_paletes_pendentes_de_armazenagem_sem_listar(self):
        """Mapa de Estoque virou somente leitura: mostra a contagem de pendentes no
        resumo, mas a lista/ação de armazenar migrou para o Put-away."""
        palete = _criar_palete_conferido('PEND1')

        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertNotContains(response, palete.barcode_palete)
        self.assertEqual(response.context['total_paletes_pendentes_armazenagem'], 1)

    def test_palete_conferido_pela_producao_mas_nao_pela_logistica_nao_conta_como_pendente(self):
        """Prova a correção do bug: um palete só CONFERIDO (aprovado pela Produção,
        ainda não confirmado pela Logística) não deve contar como pendente de
        armazenagem — só depois de virar CONFERIDO_LOGISTICA."""
        Palete.objects.create(
            sku='CAFE-SEMLOG', lote='L-SEMLOG', descricao='Café teste', peso_kg='20.000', estado='CONFERIDO',
        )
        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(response.context['total_paletes_pendentes_armazenagem'], 0)

    def test_paletes_pendentes_nao_aparecem_na_tabela_de_armazenados(self):
        endereco = Endereco.objects.create(
            modulo=8, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=8),
            capacidade=21, ocupacao_atual=1,
        )
        _criar_palete_conferido('PEND2')
        armazenado = _criar_palete_armazenado('ARM2', endereco)

        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(list(response.context['paletes_armazenados']), [armazenado])
        self.assertEqual(response.context['total_paletes_pendentes_armazenagem'], 1)


class RetirarPaleteDoMapaTests(TestCase):

    def setUp(self):
        self.endereco = Endereco.objects.create(
            modulo=9, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=9),
            capacidade=21, ocupacao_atual=1,
        )
        self.palete = _criar_palete_armazenado('RETIRAR1', self.endereco)

    def test_retira_palete_liberando_vaga_e_marcando_retirado(self):
        endereco = retirar_palete_do_mapa(self.palete)
        self.assertEqual(endereco, self.endereco)
        self.palete.refresh_from_db()
        self.endereco.refresh_from_db()
        self.assertEqual(self.palete.estado, 'RETIRADO')
        self.assertIsNone(self.palete.endereco)
        self.assertEqual(self.endereco.ocupacao_atual, 0)
        self.assertIsNotNone(self.palete.retirado_em)

    def test_palete_nao_armazenado_e_rejeitado(self):
        palete_conferido = _criar_palete_conferido('NAOARM2')
        with self.assertRaises(PaleteNaoArmazenadoError):
            retirar_palete_do_mapa(palete_conferido)


class ConsultarPaleteViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('operador5', password='senha-teste-123')
        self.user.groups.add(Group.objects.get(name='Operador Logística'))
        self.client.login(username='operador5', password='senha-teste-123')
        self.origem = Endereco.objects.create(
            modulo=11, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=11),
            capacidade=21, ocupacao_atual=1,
        )
        self.destino = Endereco.objects.create(
            modulo=11, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', modulo=11),
            capacidade=21, ocupacao_atual=0,
        )
        self.palete = _criar_palete_armazenado('CONSULTA1', self.origem)

    def test_tela_inicial_sem_modo_mostra_os_tres_botoes(self):
        response = self.client.get(reverse('estoque:consultar_palete'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '?modo=palete')
        self.assertContains(response, '?modo=lote')
        self.assertContains(response, '?modo=endereco')
        self.assertNotContains(response, 'Informe ao menos um critério de busca')
        self.assertNotContains(response, self.palete.barcode_palete)

    def test_modo_palete_sem_barcode_mostra_campo_de_bipagem(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'palete'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="barcode"')

    def test_modo_palete_mostra_dados_e_acoes_apenas_quando_armazenado(self):
        estados_sem_acao = [
            'CRIADO', 'EM_TRANSFERENCIA', 'CONFERIDO', 'EM_CONFERENCIA_LOGISTICA',
            'CONFERIDO_LOGISTICA', 'FINALIZADO', 'RETIRADO',
        ]
        for estado in estados_sem_acao:
            palete = _criar_palete_conferido(f'ESTADO-{estado}')
            palete.estado = estado
            palete.save()
            response = self.client.get(
                reverse('estoque:consultar_palete'), {'modo': 'palete', 'barcode': palete.barcode_palete}
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, palete.barcode_palete)
            self.assertContains(response, palete.get_estado_display())
            self.assertNotContains(response, 'name="retirar"')

        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'palete', 'barcode': self.palete.barcode_palete}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertContains(response, self.origem.codigo)
        self.assertContains(response, 'name="retirar"')

    def test_modo_palete_com_barcode_inexistente_mostra_erro(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'palete', 'barcode': 'NAO-EXISTE'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'não encontrado')

    def test_modo_lote_sem_criterios_nao_mostra_erro(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'lote'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Informe ao menos um critério de busca')
        self.assertNotContains(response, self.palete.barcode_palete)

    def test_modo_lote_busca_submetida_sem_criterios_mostra_erro(self):
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'lote', 'lote': '', 'sku': '', 'data_fabricacao': ''}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informe ao menos um critério de busca')
        self.assertNotContains(response, self.palete.barcode_palete)

    def test_modo_lote_busca_por_lote_encontra_palete(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'lote', 'lote': self.palete.lote})
        self.assertContains(response, self.palete.barcode_palete)
        self.assertContains(response, f'?modo=palete&barcode={self.palete.barcode_palete}')

    def test_modo_lote_busca_por_sku_encontra_palete(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'lote', 'sku': self.palete.sku})
        self.assertContains(response, self.palete.barcode_palete)

    def test_modo_lote_nao_mostra_campo_de_busca_por_sku(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'lote'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="sku"')

    def test_modo_lote_busca_por_data_fabricacao_encontra_palete(self):
        hoje = timezone.now().date().isoformat()
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'lote', 'data_fabricacao': hoje}
        )
        self.assertContains(response, self.palete.barcode_palete)

    def test_modo_lote_busca_combinando_lote_e_sku_aplica_ambos_como_filtro(self):
        outro = _criar_palete_armazenado('CONSULTA2', self.destino)
        outro.lote = self.palete.lote  # mesmo lote, SKU diferente — não deve aparecer
        outro.save()

        response = self.client.get(reverse('estoque:consultar_palete'), {
            'modo': 'lote', 'lote': self.palete.lote, 'sku': self.palete.sku,
        })
        self.assertContains(response, self.palete.barcode_palete)
        self.assertNotContains(response, outro.barcode_palete)

    def test_modo_lote_busca_sem_resultados_mostra_erro(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'lote', 'lote': 'L-NAOEXISTE'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nenhum palete encontrado')

    def test_modo_endereco_sem_codigo_mostra_filtros_andar_modulo(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'endereco'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="andar"')
        self.assertContains(response, 'name="modulo"')
        self.assertNotContains(response, 'name="codigo_endereco"')

    def test_modo_endereco_sem_codigo_mostra_mapa_de_estoque_clicavel(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'endereco'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.origem.codigo)
        url_esperada = (
            f"{reverse('estoque:consultar_palete')}?modo=endereco&codigo_endereco={self.origem.codigo}"
        )
        self.assertContains(response, url_esperada)

    def test_clicar_no_endereco_do_mapa_leva_direto_aos_paletes_armazenados(self):
        link = (
            f"{reverse('estoque:consultar_palete')}?modo=endereco&codigo_endereco={self.origem.codigo}"
        )
        response = self.client.get(link)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.origem.codigo)
        self.assertContains(response, self.palete.barcode_palete)

    def test_modo_endereco_com_codigo_lista_paletes_armazenados(self):
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'endereco', 'codigo_endereco': self.origem.codigo}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.origem.codigo)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertContains(response, 'name="retirar"')

    def test_modo_endereco_sem_paletes_mostra_lista_vazia_sem_erro(self):
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'endereco', 'codigo_endereco': self.destino.codigo}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.destino.codigo)
        self.assertContains(response, 'Nenhum palete armazenado')

    def test_modo_endereco_com_codigo_inexistente_mostra_erro_sem_crash(self):
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'endereco', 'codigo_endereco': 'Z-99'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'não encontrado')

    def test_modo_endereco_mapa_inteiro_lista_todos_os_paletes_armazenados(self):
        outro = _criar_palete_armazenado('CONSULTA-MAPA', self.destino)
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'endereco', 'andar': '', 'modulo': ''}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertContains(response, outro.barcode_palete)

    def test_modo_endereco_filtro_por_andar_lista_so_paletes_daquele_andar(self):
        outro = _criar_palete_armazenado('CONSULTA-ANDARB', self.destino)
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'endereco', 'andar': 'A'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertNotContains(response, outro.barcode_palete)
        # a grade em si também deve mostrar só o andar filtrado, não o mapa inteiro marcado
        self.assertContains(response, self.origem.codigo)
        self.assertNotContains(response, self.destino.codigo)

    def test_modo_endereco_filtro_por_modulo_lista_so_paletes_daquele_modulo(self):
        outro_endereco = Endereco.objects.create(
            modulo=5, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=5),
            capacidade=21, ocupacao_atual=1,
        )
        outro = _criar_palete_armazenado('CONSULTA-MOD5', outro_endereco)
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'endereco', 'modulo': '11'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertNotContains(response, outro.barcode_palete)
        self.assertContains(response, self.origem.codigo)
        self.assertNotContains(response, outro_endereco.codigo)

    def test_modo_endereco_filtro_por_andar_e_modulo_combinados(self):
        outro = _criar_palete_armazenado('CONSULTA-COMBO', self.destino)
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'endereco', 'andar': 'A', 'modulo': '11'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertNotContains(response, outro.barcode_palete)

    def test_modo_endereco_filtro_sem_resultados_mostra_mensagem_sem_erro(self):
        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'endereco', 'andar': 'C'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nenhum palete armazenado para este filtro')

    def test_modo_endereco_codigo_tem_prioridade_sobre_filtro_andar_modulo(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {
            'modo': 'endereco', 'codigo_endereco': self.origem.codigo, 'andar': 'C', 'modulo': '9',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.origem.codigo)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertNotContains(response, 'Nenhum palete armazenado para este filtro')

    def test_modo_endereco_sem_filtro_submetido_nao_mostra_tabela_de_filtro(self):
        response = self.client.get(reverse('estoque:consultar_palete'), {'modo': 'endereco'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Nenhum palete armazenado para este filtro')

    def test_modo_endereco_filtro_pagina_resultados(self):
        andares = ['A', 'B', 'C']
        criados = 0
        for modulo in range(1, 21):
            for andar in andares:
                if modulo == 11 and andar in ('A', 'B'):
                    continue  # já usados por self.origem/self.destino no setUp
                endereco = Endereco.objects.create(
                    modulo=modulo, lado=1, andar=andar,
                    codigo=Endereco.montar_codigo(lado=1, andar=andar, modulo=modulo),
                    capacidade=21, ocupacao_atual=1,
                )
                _criar_palete_armazenado(f'CONSULTA-PG{criados}', endereco)
                criados += 1
                if criados >= 25:
                    break
            if criados >= 25:
                break

        response = self.client.get(
            reverse('estoque:consultar_palete'), {'modo': 'endereco', 'andar': '', 'modulo': ''}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Página 1 de')
        self.assertContains(response, 'Próxima')

    def test_retirar_do_mapa_no_modo_palete_libera_vaga_e_marca_retirado(self):
        response = self.client.post(reverse('estoque:consultar_palete'), {
            'barcode_palete': self.palete.barcode_palete,
            'retirar': '1',
        })
        self.assertRedirects(
            response, f"{reverse('estoque:consultar_palete')}?modo=palete&barcode={self.palete.barcode_palete}"
        )
        self.palete.refresh_from_db()
        self.origem.refresh_from_db()
        self.assertEqual(self.palete.estado, 'RETIRADO')
        self.assertIsNone(self.palete.endereco)
        self.assertEqual(self.origem.ocupacao_atual, 0)

    def test_retirar_do_mapa_no_modo_endereco_volta_para_o_mesmo_endereco(self):
        response = self.client.post(reverse('estoque:consultar_palete'), {
            'barcode_palete': self.palete.barcode_palete,
            'codigo_endereco': self.origem.codigo,
            'retirar': '1',
        })
        self.assertRedirects(
            response,
            f"{reverse('estoque:consultar_palete')}?modo=endereco&codigo_endereco={self.origem.codigo}",
        )
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'RETIRADO')

    def test_retirar_em_palete_nao_armazenado_e_rejeitado_sem_crash(self):
        palete_conferido = _criar_palete_conferido('NAOARMVIEW2')
        response = self.client.post(reverse('estoque:consultar_palete'), {
            'barcode_palete': palete_conferido.barcode_palete,
            'retirar': '1',
        })
        self.assertRedirects(
            response, f"{reverse('estoque:consultar_palete')}?modo=palete&barcode={palete_conferido.barcode_palete}"
        )
        palete_conferido.refresh_from_db()
        self.assertEqual(palete_conferido.estado, 'CONFERIDO_LOGISTICA')

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('estoque:consultar_palete'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('estoque:consultar_palete')}")


class InspecionarPaleteViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('inspecionar-gestor-producao', password='senha-teste-123')
        self.user.groups.add(Group.objects.get(name='Gestor Produção'))
        self.client.login(username='inspecionar-gestor-producao', password='senha-teste-123')
        self.endereco = Endereco.objects.create(
            modulo=12, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', modulo=12),
            capacidade=21, ocupacao_atual=1,
        )
        self.palete = _criar_palete_armazenado('INSPECIONAR1', self.endereco)
        from apps.recebimento.models import registrar_historico
        registrar_historico([self.palete], 'ARMAZENADO', usuario=self.user)

    def test_mostra_dados_do_palete_e_historico(self):
        response = self.client.get(reverse('estoque:inspecionar_palete', args=[self.palete.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        historico = response.context['historico']
        self.assertTrue(historico.exists())

    def test_mostra_datas_de_rastreabilidade_quando_preenchidas(self):
        from django.utils import timezone
        self.palete.aprovado_producao_em = timezone.now()
        self.palete.aprovado_logistica_em = timezone.now()
        self.palete.armazenado_em = timezone.now()
        self.palete.save(update_fields=['aprovado_producao_em', 'aprovado_logistica_em', 'armazenado_em'])

        response = self.client.get(reverse('estoque:inspecionar_palete', args=[self.palete.pk]))
        self.assertContains(response, 'Aprovado na Produção')
        self.assertContains(response, 'Aprovado na Logística')
        self.assertContains(response, 'Armazenado em')
        self.assertNotContains(response, 'Retirado em')

    def test_palete_inexistente_retorna_404(self):
        response = self.client.get(reverse('estoque:inspecionar_palete', args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_operador_logistica_recebe_403(self):
        self.client.logout()
        operador_logistica = User.objects.create_user('inspecionar-operador-logistica', password='senha-teste-123')
        operador_logistica.groups.add(Group.objects.get(name='Operador Logística'))
        self.client.login(username='inspecionar-operador-logistica', password='senha-teste-123')
        response = self.client.get(reverse('estoque:inspecionar_palete', args=[self.palete.pk]))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        destino = reverse('estoque:inspecionar_palete', args=[self.palete.pk])
        response = self.client.get(destino)
        self.assertRedirects(response, f"{reverse('login')}?next={destino}")
