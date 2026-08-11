import threading
import time
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.db.utils import OperationalError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.estoque.models import Endereco
from apps.estoque.services import (
    EnderecoCheioError,
    MesmoEnderecoError,
    PaleteNaoArmazenadoError,
    alocar_palete,
    mover_palete,
    retirar_palete_do_mapa,
)
from apps.recebimento.models import Palete


class GerarEstruturaArmazemTests(TestCase):

    def test_cria_exatamente_120_enderecos(self):
        call_command('gerar_estrutura_armazem')
        self.assertEqual(Endereco.objects.count(), 120)

    def test_codigos_seguem_padrao_e_sao_unicos(self):
        call_command('gerar_estrutura_armazem')
        codigos = set(Endereco.objects.values_list('codigo', flat=True))
        esperados = {
            f'001-{lado:02d}-{andar}-{guarda:02d}-01'
            for lado in (1, 2) for guarda in range(1, 21) for andar in ('A', 'B', 'C')
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
        self.assertEqual(Endereco.objects.count(), 120)


def _criar_palete_conferido(sufixo):
    return Palete.objects.create(
        sku=f'CAFE-{sufixo}',
        lote=f'L-{sufixo}',
        descricao='Café teste',
        peso_kg='20.000',
        estado='CONFERIDO',
    )


def _criar_palete_armazenado(sufixo, endereco):
    palete = _criar_palete_conferido(sufixo)
    palete.estado = 'ARMAZENADO'
    palete.endereco = endereco
    palete.save()
    return palete


class AlocarPaleteCapacidadeTests(TestCase):

    def setUp(self):
        self.endereco = Endereco.objects.create(
            guarda=2, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', guarda=2),
            capacidade=21, ocupacao_atual=0,
        )

    def test_permite_alocar_ate_21_paletes(self):
        for i in range(21):
            alocar_palete(_criar_palete_conferido(f'OK{i}'), self.endereco.codigo)

        self.endereco.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 21)
        self.assertEqual(Palete.objects.filter(endereco=self.endereco, estado='ARMAZENADO').count(), 21)

    def test_22o_palete_e_rejeitado(self):
        for i in range(21):
            alocar_palete(_criar_palete_conferido(f'CHEIO{i}'), self.endereco.codigo)

        vigesimo_segundo = _criar_palete_conferido('EXTRA')
        with self.assertRaises(EnderecoCheioError):
            alocar_palete(vigesimo_segundo, self.endereco.codigo)

        self.endereco.refresh_from_db()
        vigesimo_segundo.refresh_from_db()
        self.assertEqual(self.endereco.ocupacao_atual, 21)
        self.assertEqual(vigesimo_segundo.estado, 'CONFERIDO')
        self.assertIsNone(vigesimo_segundo.endereco)


class AlocarPaleteConcorrenciaTests(TransactionTestCase):
    """Simula requisições paralelas disputando a última vaga de um endereço."""

    def setUp(self):
        self.endereco = Endereco.objects.create(
            guarda=3, lado=1, andar='C',
            codigo=Endereco.montar_codigo(lado=1, andar='C', guarda=3),
            capacidade=21, ocupacao_atual=20,
        )

    def test_apenas_um_vencedor_para_a_ultima_vaga(self):
        NUM_CONCORRENTES = 5
        paletes = [_criar_palete_conferido(f'RACE{i}') for i in range(NUM_CONCORRENTES)]
        resultados = []
        barreira = threading.Barrier(NUM_CONCORRENTES)
        codigo = self.endereco.codigo

        def tentar_alocar(palete):
            barreira.wait()
            # SQLite não bloqueia-e-espera por linha como Postgres/MySQL: sob disputa,
            # a transação perdedora recebe "database is locked" na hora em vez de
            # aguardar o commit da vencedora. Retentar aqui simula essa espera, para
            # que a rejeição observada seja sempre a regra de negócio (EnderecoCheioError),
            # como aconteceria de fato em produção (MySQL).
            try:
                for _ in range(100):
                    try:
                        alocar_palete(palete, codigo)
                        resultados.append('sucesso')
                        return
                    except OperationalError:
                        time.sleep(0.05)
                resultados.append('lock-persistente')
            except EnderecoCheioError:
                resultados.append('rejeitado')
            finally:
                connection.close()

        threads = [threading.Thread(target=tentar_alocar, args=(p,)) for p in paletes]
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
        self.client.login(username='operador2', password='senha-teste-123')
        self.endereco = Endereco.objects.create(
            guarda=4, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=4),
            capacidade=21, ocupacao_atual=0,
        )
        self.palete = _criar_palete_conferido('VIEW1')

    # Etapa A: localizar palete pelo barcode
    def test_tela_inicial_pede_bipagem_do_palete(self):
        response = self.client.get(reverse('estoque:putaway'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="barcode_palete"')
        self.assertNotContains(response, self.palete.sku)

    def test_bipar_barcode_valido_redireciona_para_tela_do_produto(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': self.palete.barcode_palete
        })
        self.assertRedirects(response, f"{reverse('estoque:putaway')}?barcode={self.palete.barcode_palete}")

    def test_tela_do_produto_mostra_dados_e_grade_de_enderecos(self):
        response = self.client.get(reverse('estoque:putaway'), {'barcode': self.palete.barcode_palete})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertContains(response, self.palete.sku)
        self.assertContains(response, self.palete.lote)
        self.assertContains(response, str(self.palete.peso_kg))
        self.assertContains(response, self.endereco.codigo)
        self.assertContains(response, 'name="codigo_endereco"')
        self.assertContains(response, f'value="{self.endereco.codigo}"')

    def test_endereco_cheio_aparece_no_grid_mas_nao_e_clicavel(self):
        self.endereco.ocupacao_atual = 21
        self.endereco.save()
        response = self.client.get(reverse('estoque:putaway'), {'barcode': self.palete.barcode_palete})
        self.assertContains(response, self.endereco.codigo)
        self.assertNotContains(response, f'value="{self.endereco.codigo}"')

    def test_barcode_desconhecido_mostra_erro_sem_crashar(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': 'PLT-NAOEXISTE'
        })
        self.assertRedirects(response, reverse('estoque:putaway'))

    def test_barcode_vazio_mostra_erro(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': ''
        })
        self.assertRedirects(response, reverse('estoque:putaway'))

    def test_barcode_de_palete_ainda_nao_conferido_mostra_erro(self):
        palete_criado = Palete.objects.create(
            sku='TEST-001', descricao='Test', lote='LT-TEST', peso_kg=Decimal('20'),
            estado='CRIADO', barcode_palete='PLT-CRIADO'
        )
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': palete_criado.barcode_palete
        })
        self.assertRedirects(response, reverse('estoque:putaway'))

    def test_barcode_de_palete_ja_armazenado_mostra_erro(self):
        palete_armazenado = _criar_palete_conferido('ARMAZENADO')
        palete_armazenado.estado = 'ARMAZENADO'
        palete_armazenado.save()
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': palete_armazenado.barcode_palete
        })
        self.assertRedirects(response, reverse('estoque:putaway'))

    def test_get_com_barcode_desconhecido_redireciona_sem_crashar(self):
        response = self.client.get(reverse('estoque:putaway'), {'barcode': 'PLT-NAOEXISTE'})
        self.assertRedirects(response, reverse('estoque:putaway'))

    # Etapa B: alocar palete no endereço
    def test_confirmar_guarda_aloca_palete(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': self.palete.barcode_palete,
            'codigo_endereco': self.endereco.codigo,
        })
        self.assertEqual(response.status_code, 302)
        self.palete.refresh_from_db()
        self.endereco.refresh_from_db()
        self.assertEqual(self.palete.estado, 'ARMAZENADO')
        self.assertEqual(self.palete.endereco, self.endereco)
        self.assertEqual(self.endereco.ocupacao_atual, 1)

    def test_endereco_cheio_e_rejeitado_no_alocar(self):
        self.endereco.ocupacao_atual = 21
        self.endereco.save()
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': self.palete.barcode_palete,
            'codigo_endereco': self.endereco.codigo,
        })
        self.assertEqual(response.status_code, 302)
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')
        self.assertIsNone(self.palete.endereco)

    def test_endereco_desconhecido_no_alocar_mostra_erro(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': self.palete.barcode_palete,
            'codigo_endereco': '001-99-Z-99-01',
        })
        self.assertEqual(response.status_code, 302)
        self.palete.refresh_from_db()
        self.assertEqual(self.palete.estado, 'CONFERIDO')

    def test_alocar_com_barcode_desconhecido_mostra_erro_sem_crashar(self):
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': 'PLT-INVALIDO',
            'codigo_endereco': self.endereco.codigo,
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('estoque:putaway'))

    def test_alocar_com_palete_em_estado_invalido_mostra_erro(self):
        palete_armazenado = _criar_palete_conferido('ARMAZENADO')
        palete_armazenado.estado = 'ARMAZENADO'
        palete_armazenado.save()
        response = self.client.post(reverse('estoque:putaway'), {
            'barcode_palete': palete_armazenado.barcode_palete,
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


class MapaEstoqueTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('operador4', password='senha-teste-123')
        self.client.login(username='operador4', password='senha-teste-123')

    def test_mostra_grade_com_todos_os_enderecos_cadastrados(self):
        endereco = Endereco.objects.create(
            guarda=1, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=1),
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
            guarda=6, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', guarda=6),
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
            guarda=7, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=7),
            capacidade=21, ocupacao_atual=10,
        )
        Endereco.objects.create(
            guarda=7, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', guarda=7),
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

    def test_lista_paletes_pendentes_de_armazenagem(self):
        palete = _criar_palete_conferido('PEND1')

        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertContains(response, palete.barcode_palete)
        self.assertEqual(list(response.context['paletes_pendentes_armazenagem']), [palete])
        self.assertEqual(response.context['total_paletes_pendentes_armazenagem'], 1)

    def test_paletes_conferidos_e_armazenados_nao_se_misturam(self):
        endereco = Endereco.objects.create(
            guarda=8, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=8),
            capacidade=21, ocupacao_atual=1,
        )
        pendente = _criar_palete_conferido('PEND2')
        armazenado = _criar_palete_armazenado('ARM2', endereco)

        response = self.client.get(reverse('estoque:mapa_estoque'))
        self.assertEqual(list(response.context['paletes_pendentes_armazenagem']), [pendente])
        self.assertEqual(list(response.context['paletes_armazenados']), [armazenado])

    def test_link_armazenar_aponta_para_putaway_com_barcode(self):
        palete = _criar_palete_conferido('PEND3')

        response = self.client.get(reverse('estoque:mapa_estoque'))
        url_putaway = f"{reverse('estoque:putaway')}?barcode={palete.barcode_palete}"
        self.assertContains(response, f'href="{url_putaway}"')


class MoverPaleteTests(TestCase):

    def setUp(self):
        self.origem = Endereco.objects.create(
            guarda=8, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=8),
            capacidade=21, ocupacao_atual=1,
        )
        self.destino = Endereco.objects.create(
            guarda=8, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', guarda=8),
            capacidade=21, ocupacao_atual=0,
        )
        self.palete = _criar_palete_armazenado('MOVER1', self.origem)

    def test_move_palete_decrementando_origem_e_incrementando_destino(self):
        endereco = mover_palete(self.palete, self.destino.codigo)
        self.assertEqual(endereco, self.destino)
        self.palete.refresh_from_db()
        self.origem.refresh_from_db()
        self.destino.refresh_from_db()
        self.assertEqual(self.palete.endereco, self.destino)
        self.assertEqual(self.palete.estado, 'ARMAZENADO')
        self.assertEqual(self.origem.ocupacao_atual, 0)
        self.assertEqual(self.destino.ocupacao_atual, 1)

    def test_destino_cheio_e_rejeitado(self):
        self.destino.ocupacao_atual = 21
        self.destino.save()
        with self.assertRaises(EnderecoCheioError):
            mover_palete(self.palete, self.destino.codigo)
        self.palete.refresh_from_db()
        self.origem.refresh_from_db()
        self.assertEqual(self.palete.endereco, self.origem)
        self.assertEqual(self.origem.ocupacao_atual, 1)

    def test_destino_inexistente_e_rejeitado(self):
        with self.assertRaises(Endereco.DoesNotExist):
            mover_palete(self.palete, '001-99-Z-99-01')

    def test_destino_inativo_e_rejeitado(self):
        self.destino.ativo = False
        self.destino.save()
        with self.assertRaises(Endereco.DoesNotExist):
            mover_palete(self.palete, self.destino.codigo)

    def test_mesmo_endereco_e_rejeitado(self):
        with self.assertRaises(MesmoEnderecoError):
            mover_palete(self.palete, self.origem.codigo)

    def test_palete_nao_armazenado_e_rejeitado(self):
        palete_conferido = _criar_palete_conferido('NAOARM')
        with self.assertRaises(PaleteNaoArmazenadoError):
            mover_palete(palete_conferido, self.destino.codigo)


class RetirarPaleteDoMapaTests(TestCase):

    def setUp(self):
        self.endereco = Endereco.objects.create(
            guarda=9, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=9),
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

    def test_palete_nao_armazenado_e_rejeitado(self):
        palete_conferido = _criar_palete_conferido('NAOARM2')
        with self.assertRaises(PaleteNaoArmazenadoError):
            retirar_palete_do_mapa(palete_conferido)


class MoverPaleteConcorrenciaTests(TransactionTestCase):
    """Simula requisições paralelas disputando a última vaga de um endereço de destino,
    e duas ações concorrentes atingindo o mesmo palete."""

    def setUp(self):
        self.destino = Endereco.objects.create(
            guarda=90, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=90),
            capacidade=21, ocupacao_atual=20,
        )

    def test_apenas_um_vencedor_para_a_ultima_vaga_do_destino(self):
        NUM_CONCORRENTES = 5
        paletes = []
        for i in range(NUM_CONCORRENTES):
            origem = Endereco.objects.create(
                guarda=91 + i, lado=1, andar='A', codigo=f'RACE-{i}', capacidade=21, ocupacao_atual=1,
            )
            paletes.append(_criar_palete_armazenado(f'MOVERACE{i}', origem))

        resultados = []
        barreira = threading.Barrier(NUM_CONCORRENTES)
        codigo_destino = self.destino.codigo

        def tentar_mover(palete):
            barreira.wait()
            try:
                for _ in range(100):
                    try:
                        mover_palete(palete, codigo_destino)
                        resultados.append('sucesso')
                        return
                    except OperationalError:
                        time.sleep(0.05)
                resultados.append('lock-persistente')
            except EnderecoCheioError:
                resultados.append('rejeitado')
            finally:
                connection.close()

        threads = [threading.Thread(target=tentar_mover, args=(p,)) for p in paletes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.destino.refresh_from_db()
        self.assertEqual(resultados.count('sucesso'), 1)
        self.assertEqual(resultados.count('rejeitado'), NUM_CONCORRENTES - 1)
        self.assertEqual(self.destino.ocupacao_atual, 21)
        self.assertEqual(Palete.objects.filter(endereco=self.destino).count(), 1)

    def test_duas_acoes_concorrentes_no_mesmo_palete_nao_corrompem_ocupacao(self):
        """Prova que travar a linha do Palete evita decremento duplo da origem
        quando duas ações concorrentes (ex.: duplo clique, duas abas) atingem o
        mesmo palete quase simultaneamente."""
        origem = Endereco.objects.create(
            guarda=95, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=95),
            capacidade=21, ocupacao_atual=1,
        )
        destino_a = Endereco.objects.create(
            guarda=95, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', guarda=95),
            capacidade=21, ocupacao_atual=0,
        )
        destino_b = Endereco.objects.create(
            guarda=95, lado=1, andar='C',
            codigo=Endereco.montar_codigo(lado=1, andar='C', guarda=95),
            capacidade=21, ocupacao_atual=0,
        )
        palete = _criar_palete_armazenado('SAMEPALETE', origem)

        resultados = []
        barreira = threading.Barrier(2)

        def tentar_mover(novo_codigo):
            barreira.wait()
            # Cada "requisição" busca sua própria cópia do palete antes de agir,
            # simulando duas views concorrentes lendo o mesmo registro.
            palete_local = Palete.objects.get(pk=palete.pk)
            try:
                for _ in range(100):
                    try:
                        mover_palete(palete_local, novo_codigo)
                        resultados.append('sucesso')
                        return
                    except OperationalError:
                        time.sleep(0.05)
                resultados.append('lock-persistente')
            except (MesmoEnderecoError, PaleteNaoArmazenadoError):
                resultados.append('rejeitado')
            finally:
                connection.close()

        t1 = threading.Thread(target=tentar_mover, args=(destino_a.codigo,))
        t2 = threading.Thread(target=tentar_mover, args=(destino_b.codigo,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        origem.refresh_from_db()
        destino_a.refresh_from_db()
        destino_b.refresh_from_db()

        # A origem só pode ter sido decrementada uma única vez, e apenas um dos
        # dois destinos recebeu o incremento — sem decremento/incremento duplo.
        self.assertEqual(origem.ocupacao_atual, 0)
        self.assertEqual(destino_a.ocupacao_atual + destino_b.ocupacao_atual, 1)


class ConsultarPaleteViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('operador5', password='senha-teste-123')
        self.client.login(username='operador5', password='senha-teste-123')
        self.origem = Endereco.objects.create(
            guarda=11, lado=1, andar='A',
            codigo=Endereco.montar_codigo(lado=1, andar='A', guarda=11),
            capacidade=21, ocupacao_atual=1,
        )
        self.destino = Endereco.objects.create(
            guarda=11, lado=1, andar='B',
            codigo=Endereco.montar_codigo(lado=1, andar='B', guarda=11),
            capacidade=21, ocupacao_atual=0,
        )
        self.palete = _criar_palete_armazenado('CONSULTA1', self.origem)

    def test_bipagem_inicial_redireciona_para_tela_com_querystring(self):
        response = self.client.post(reverse('estoque:consultar_palete'), {
            'barcode_palete': self.palete.barcode_palete,
        })
        self.assertRedirects(
            response, f"{reverse('estoque:consultar_palete')}?barcode={self.palete.barcode_palete}"
        )

    def test_barcode_desconhecido_mostra_erro_sem_crashar(self):
        response = self.client.post(reverse('estoque:consultar_palete'), {'barcode_palete': 'PLT-NAOEXISTE'})
        self.assertRedirects(response, reverse('estoque:consultar_palete'))

    def test_get_mostra_dados_e_acoes_apenas_quando_armazenado(self):
        estados_sem_acao = ['CRIADO', 'EM_TRANSFERENCIA', 'CONFERIDO', 'FINALIZADO', 'RETIRADO']
        for estado in estados_sem_acao:
            palete = _criar_palete_conferido(f'ESTADO-{estado}')
            palete.estado = estado
            palete.save()
            response = self.client.get(reverse('estoque:consultar_palete'), {'barcode': palete.barcode_palete})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, palete.barcode_palete)
            self.assertContains(response, palete.get_estado_display())
            self.assertNotContains(response, 'name="novo_codigo_endereco"')
            self.assertNotContains(response, 'name="retirar"')

        response = self.client.get(reverse('estoque:consultar_palete'), {'barcode': self.palete.barcode_palete})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.palete.barcode_palete)
        self.assertContains(response, self.origem.codigo)
        self.assertContains(response, 'name="novo_codigo_endereco"')
        self.assertContains(response, 'name="retirar"')

    def test_alterar_endereco_move_o_palete_e_redireciona_mostrando_novo_endereco(self):
        response = self.client.post(reverse('estoque:consultar_palete'), {
            'barcode_palete': self.palete.barcode_palete,
            'novo_codigo_endereco': self.destino.codigo,
        })
        self.assertRedirects(
            response, f"{reverse('estoque:consultar_palete')}?barcode={self.palete.barcode_palete}"
        )
        self.palete.refresh_from_db()
        self.origem.refresh_from_db()
        self.destino.refresh_from_db()
        self.assertEqual(self.palete.endereco, self.destino)
        self.assertEqual(self.origem.ocupacao_atual, 0)
        self.assertEqual(self.destino.ocupacao_atual, 1)

    def test_retirar_do_mapa_libera_vaga_e_marca_retirado(self):
        response = self.client.post(reverse('estoque:consultar_palete'), {
            'barcode_palete': self.palete.barcode_palete,
            'retirar': '1',
        })
        self.assertRedirects(
            response, f"{reverse('estoque:consultar_palete')}?barcode={self.palete.barcode_palete}"
        )
        self.palete.refresh_from_db()
        self.origem.refresh_from_db()
        self.assertEqual(self.palete.estado, 'RETIRADO')
        self.assertIsNone(self.palete.endereco)
        self.assertEqual(self.origem.ocupacao_atual, 0)

    def test_alterar_endereco_em_palete_nao_armazenado_e_rejeitado_sem_crash(self):
        palete_conferido = _criar_palete_conferido('NAOARMVIEW')
        response = self.client.post(reverse('estoque:consultar_palete'), {
            'barcode_palete': palete_conferido.barcode_palete,
            'novo_codigo_endereco': self.destino.codigo,
        })
        self.assertRedirects(
            response, f"{reverse('estoque:consultar_palete')}?barcode={palete_conferido.barcode_palete}"
        )
        palete_conferido.refresh_from_db()
        self.assertEqual(palete_conferido.estado, 'CONFERIDO')

    def test_retirar_em_palete_nao_armazenado_e_rejeitado_sem_crash(self):
        palete_conferido = _criar_palete_conferido('NAOARMVIEW2')
        response = self.client.post(reverse('estoque:consultar_palete'), {
            'barcode_palete': palete_conferido.barcode_palete,
            'retirar': '1',
        })
        self.assertRedirects(
            response, f"{reverse('estoque:consultar_palete')}?barcode={palete_conferido.barcode_palete}"
        )
        palete_conferido.refresh_from_db()
        self.assertEqual(palete_conferido.estado, 'CONFERIDO')

    def test_anonimo_e_redirecionado_para_login(self):
        self.client.logout()
        response = self.client.get(reverse('estoque:consultar_palete'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('estoque:consultar_palete')}")
